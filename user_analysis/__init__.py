import json
import os
import time
import re
import httpx
from typing import Dict, List, Optional
from pathlib import Path

from nonebot import on_message, on_command, get_plugin_config, logger, get_driver, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment, MessageEvent
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from nonebot.exception import FinishedException
from openai import AsyncOpenAI

from .config import Config

# 尝试加载拟人插件的配置作为默认值
try:
    from ..personification.config import Config as PersonConfig
    person_config = get_plugin_config(PersonConfig)
except Exception:
    person_config = None

require("nonebot_plugin_htmlrender")
try:
    from nonebot_plugin_htmlrender import md_to_pic, template_to_pic
except ImportError:
    md_to_pic = None
    template_to_pic = None

__plugin_meta__ = PluginMetadata(
    name="用户成分分析",
    description="记录用户发言并使用 AI 分析其成分",
    usage="成分分析 [@用户/QQ号]",
    config=Config,
)

plugin_config = get_plugin_config(Config)
driver_config = get_driver().config
history_path = Path(plugin_config.user_analysis_history_path)
history_path.parent.mkdir(parents=True, exist_ok=True)

# 内存中的消息记录缓存
# 格式: { "user_id": [{"role": "user", "content": "...", "time": 123456789}] }
message_histories: Dict[str, List[Dict]] = {}

def load_histories():
    global message_histories
    if history_path.exists():
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                message_histories = json.load(f)
        except Exception as e:
            logger.error(f"加载消息历史失败: {e}")

def fix_truncated_json(json_str: str) -> str:
    """尝试修复被截断或格式不规范的 JSON 字符串"""
    json_str = json_str.strip()
    if not json_str:
        return ""
    
    # 1. 尝试提取第一个 { 和最后一个 } 之间的内容
    start_idx = json_str.find('{')
    if start_idx == -1:
        return ""
    
    # 2. 状态机解析
    clean_json = ""
    stack = []
    in_string = False
    escaped = False
    
    # 从第一个 { 开始处理
    for i in range(start_idx, len(json_str)):
        char = json_str[i]
        
        if escaped:
            clean_json += char
            escaped = False
            continue
            
        if char == '\\':
            clean_json += char
            escaped = True
            continue
            
        if char == '"':
            in_string = not in_string
            clean_json += char
            continue
            
        if not in_string:
            if char == '{':
                stack.append('}')
            elif char == '[':
                stack.append(']')
            elif char == '}':
                if stack and stack[-1] == '}':
                    stack.pop()
                else:
                    # 不匹配的闭合括号，跳过
                    continue
            elif char == ']':
                if stack and stack[-1] == ']':
                    stack.pop()
                else:
                    # 不匹配的闭合括号，跳过
                    continue
        
        clean_json += char
        
        # 如果栈空了且不在字符串中，说明已经到了一个完整的 JSON 对象结尾
        if not stack and not in_string and clean_json.endswith('}'):
            return clean_json

    # 3. 处理截断情况
    if in_string:
        clean_json += '"'  # 补全未闭合的字符串
    
    # 移除末尾多余的逗号
    clean_json = clean_json.rstrip().rstrip(',')
    
    # 按相反顺序闭合所有括号
    while stack:
        clean_json += stack.pop()
    
    return clean_json

def extract_json_from_text(text: str) -> str:
    """从可能包含杂质的文本中提取 JSON"""
    # 移除 markdown 代码块标记
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    
    # 查找第一个 {
    start = text.find('{')
    if start == -1:
        return text
    
    return fix_truncated_json(text[start:])

def save_histories():
    try:
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(message_histories, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存消息历史失败: {e}")

load_histories()

# 消息记录器
message_recorder = on_message(priority=99, block=False)

@message_recorder.handle()
async def handle_message(event: MessageEvent):
    user_id = str(event.user_id)
    content = event.get_plaintext().strip()
    
    if not content:
        return

    # 简单过滤命令 (以 / 或 . 开头的通常是命令)
    command_starts = getattr(driver_config, "command_start", {"/", ""})
    if any(content.startswith(start) for start in command_starts if start):
        return

    if user_id not in message_histories:
        message_histories[user_id] = []
    
    # 记录消息
    message_histories[user_id].append({
        "content": content,
        "time": int(time.time())
    })
    
    # 限制长度
    if len(message_histories[user_id]) > plugin_config.user_analysis_history_max:
        message_histories[user_id] = message_histories[user_id][-plugin_config.user_analysis_history_max:]
    
    # 定期保存 (这里简单处理，每收到消息就保存)
    save_histories()

# 分析命令
analysis_cmd = on_command("成分分析", aliases={"查成分", "分析用户"}, priority=5, block=True)

@analysis_cmd.handle()
async def handle_analysis(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    target_id = ""
    
    # 1. 检查是否有 at
    for seg in event.get_message():
        if seg.type == "at":
            target_id = str(seg.data["qq"])
            break
    
    # 2. 检查是否有参数 (QQ号)
    if not target_id:
        arg_text = args.extract_plain_text().strip()
        if arg_text.isdigit():
            target_id = arg_text
    
    # 3. 默认分析自己
    if not target_id:
        target_id = str(event.user_id)

    # 尝试通过 NapCat 获取历史记录
    history_msgs = []
    if isinstance(event, GroupMessageEvent):
        try:
            # NapCat 扩展 API: get_group_msg_history
            res = await bot.call_api("get_group_msg_history", group_id=event.group_id)
            if res and isinstance(res, list):
                # 过滤出目标用户的文本消息
                for m in res:
                    if str(m.get("user_id")) == target_id:
                        raw_msg = m.get("message", [])
                        msg_text = ""
                        if isinstance(raw_msg, list):
                            for seg in raw_msg:
                                if seg.get("type") == "text":
                                    msg_text += seg.get("data", {}).get("text", "")
                        elif isinstance(raw_msg, str):
                            # 有些实现可能直接返回字符串
                            msg_text = raw_msg
                        
                        if msg_text.strip():
                            history_msgs.append({"content": msg_text.strip()})
        except Exception as e:
            logger.warning(f"通过 NapCat 获取历史记录失败: {e}")

    # 获取最近 100 条消息
    user_msgs = message_histories.get(target_id, [])
    if not user_msgs:
        await analysis_cmd.finish(f"由于真寻酱刚刚醒来（或者该用户还没说话），目前还没有用户 {target_id} 的聊天记录呢。")
    
    # 取最近 100 条
    final_msgs = user_msgs[-100:]
    msgs_text = "\n".join([f"- {m['content']}" for m in final_msgs])
    
    await analysis_cmd.send(f"正在分析用户 {target_id} 的 {len(final_msgs)} 条最近发言，请稍候...")

    # 准备 AI 调用 - 优先级: 插件配置 > 拟人插件配置 > 环境变量
    api_url = plugin_config.user_analysis_api_url or \
              (person_config.personification_api_url if person_config else None) or \
              getattr(driver_config, "ai_api_base", "https://api.openai.com/v1")
              
    api_key = plugin_config.user_analysis_api_key or \
              (person_config.personification_api_key if person_config else None) or \
              getattr(driver_config, "ai_model_api_key", getattr(driver_config, "ai_api_key", ""))
              
    model = plugin_config.user_analysis_model or \
            (person_config.personification_model if person_config else None) or \
            getattr(driver_config, "ai_model", "gpt-4o-mini")

    if not api_key:
        await analysis_cmd.finish("未配置 AI API Key，无法进行分析。")

    is_gemini = "gemini" in model.lower()
    base_url = api_url.rstrip("/")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    system_prompt = (
        "你是一个深谙互联网文化、毒舌又精准的人格与成分分析大师。你会根据用户提供的一系列最近发言记录，"
        "深度剖析该用户的性格特点、兴趣爱好、语言风格、可能的职业或身份，并给出一个极具梗点的'成分占比'。"
        "分析要求：\n"
        "1. 语气要有个性，可以犀利、幽默或带点二次元槽点，严禁死板。\n"
        "2. 直接返回 Markdown 格式的内容，严禁使用 JSON，严禁包含任何 Markdown 以外的解释性文字。\n"
        "3. Markdown 结构要求：\n"
        "   - 使用 ### 性格剖析\n"
        "   - 使用 ### 兴趣画像 (使用 #标签 格式)\n"
        "   - 使用 ### 成分鉴定 (使用列表格式，如 - 成分: 占比%)\n"
    )
    
    user_prompt = f"请分析以下用户 (QQ: {target_id}) 的最近 {len(final_msgs)} 条发言记录并直接返回 Markdown 分析结果：\n\n{msgs_text}"

    if is_gemini:
        # Gemini 官方格式处理
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        
        url = f"{base_url}/v1beta/models/{model}:generateContent"
        
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]
                }
            ]
        }
        
        if "thinking" in model.lower():
            payload["generationConfig"] = {
                "thinkingConfig": {
                    "thinkingBudget": 1024,
                    "includeThoughts": True
                }
            }
    else:
        # OpenAI 格式
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.8,
            "max_tokens": 1500
        }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            result = response.json()
            
            if is_gemini:
                try:
                    analysis_md = result["candidates"][0]["content"]["parts"][0]["text"].strip()
                except (KeyError, IndexError):
                    logger.error(f"Gemini 响应解析失败: {result}")
                    await analysis_cmd.finish("AI 分析失败，响应格式异常。")
            else:
                analysis_md = result["choices"][0]["message"]["content"].strip()
        
        # 获取用户信息
        nickname = target_id
        try:
            if isinstance(event, GroupMessageEvent):
                user_info = await bot.get_group_member_info(group_id=event.group_id, user_id=int(target_id))
                nickname = user_info.get("card") or user_info.get("nickname") or target_id
            else:
                user_info = await bot.get_stranger_info(user_id=int(target_id))
                nickname = user_info.get("nickname", target_id)
        except Exception:
            pass
            
        avatar = f"https://q1.qlogo.cn/g?b=qq&nk={target_id}&s=640"

        # 构造最终用于渲染的 Markdown (嵌入 CSS)
        render_md = f"""
<style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 30px; background-color: #f8fafc; }}
    h3 {{ color: #4a5568; border-left: 4px solid #667eea; padding-left: 10px; margin-top: 25px; }}
    li {{ margin-bottom: 8px; color: #4a5568; }}
    code {{ background: #ebf4ff; color: #4299e1; padding: 2px 6px; border-radius: 4px; font-weight: bold; }}
    hr {{ border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0; }}
</style>

<div align="center">
  <img src="{avatar}" width="100" height="100" style="border-radius: 50%; border: 4px solid #667eea; box-shadow: 0 4px 12px rgba(0,0,0,0.15);">
  <h2 style="margin-top: 10px; color: #2d3748;">{nickname} 的成分分析报告</h2>
  <p style="color: #718096; font-size: 0.9em;">分析样本：最近 {len(final_msgs)} 条发言</p>
</div>

---

{analysis_md}

<br>
<div align="right" style="color: #a0aec0; font-size: 0.8em; border-top: 1px solid #edf2f7; padding-top: 10px;">
  AI 成分大师 · 真寻酱驱动
</div>
"""

        if md_to_pic:
            try:
                pic = await md_to_pic(
                    md=render_md,
                    width=600
                )
            except Exception as e:
                logger.error(f"Markdown 渲染图片失败: {e}")
                await analysis_cmd.finish(render_md)
            
            # 发送图片并结束（finish 会抛出 FinishedException，不能放在 try 块内被 Exception 捕获）
            await analysis_cmd.finish(MessageSegment.image(pic))
        else:
            await analysis_cmd.finish(render_md)

    except FinishedException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"分析过程中发生错误: {e}\n{traceback.format_exc()}")
        await analysis_cmd.finish(f"分析过程中发生错误: {str(e)}")
