import httpx
import json
from typing import List, Dict, Any, Optional
from datetime import datetime

from nonebot import on_command, get_plugin_config, logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot_plugin_htmlrender import md_to_pic

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="群聊总结",
    description="获取最近150条聊天记录并生成总结 (OpenAI/柏拉图格式)",
    usage="总结 / zongjie",
    config=Config,
)

plugin_config = get_plugin_config(Config)

async def get_group_history(bot: Bot, group_id: int, count: int = 150) -> List[Dict[str, Any]]:
    """获取群聊天记录"""
    try:
        res = await bot.call_api("get_group_msg_history", group_id=group_id, count=count)
        if isinstance(res, dict) and "messages" in res:
            return res["messages"]
        elif isinstance(res, list):
            return res
        return []
    except Exception as e:
        logger.error(f"获取群聊天记录失败: {e}")
        return []

def format_messages(messages: List[Dict[str, Any]]) -> str:
    """格式化聊天记录为文本"""
    formatted = []
    for msg in messages:
        user_id = msg.get("user_id", "未知")
        nickname = msg.get("sender", {}).get("nickname", "未知")
        raw_msg = msg.get("message", "")
        content = ""
        
        if isinstance(raw_msg, str):
            content = raw_msg
        elif isinstance(raw_msg, list):
            for seg in raw_msg:
                if seg.get("type") == "text":
                    content += seg.get("data", {}).get("text", "")
                elif seg.get("type") == "image":
                    content += "[图片]"
                elif seg.get("type") == "face":
                    content += "[表情]"
                else:
                    content += f"[{seg.get('type')}]"
        
        if content.strip():
            formatted.append(f"{nickname}({user_id}): {content}")
            
    return "\n".join(formatted)

async def call_ai_api(prompt: str, model: Optional[str] = None) -> str:
    """调用 AI API (支持 OpenAI 兼容格式和 Gemini 官方格式)"""
    base_url = plugin_config.zongjie_base_url.strip().rstrip('/')
    api_key = plugin_config.zongjie_api_key.strip()
    target_model = model or plugin_config.zongjie_model
    api_type = plugin_config.zongjie_api_type.lower()
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "NoneBot2-Zongjie-Plugin/1.0.0"
    }
    
    if api_type == "gemini":
        # 构建 Gemini 官方格式请求 (v1beta)
        # 路径: /v1beta/models/{model}:generateContent
        if "/v1beta" in base_url:
            url = f"{base_url}/models/{target_model}:generateContent"
        else:
            url = f"{base_url}/v1beta/models/{target_model}:generateContent"
            
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": f"你是一个群聊总结助手。请根据提供的聊天记录，使用标准 Markdown 格式进行总结，包括主要内容、关键结论和重要事项。请确保输出仅包含 Markdown 内容，不要有任何开场白或解释性文字。\n\n以下是最近的群聊记录：\n\n{prompt}"
                        }
                    ]
                }
            ],
            "generationConfig": {
                # 默认开启思考配置，如果是思考模型则生效
                "thinkingConfig": {
                    "thinkingBudget": 128,
                    "includeThoughts": True
                }
            }
        }
    else:
        # 构建 OpenAI 兼容格式请求
        if base_url.endswith('/chat/completions'):
            url = base_url
        elif base_url.endswith('/v1'):
            url = f"{base_url}/chat/completions"
        else:
            url = f"{base_url}/v1/chat/completions"
            
        payload = {
            "model": target_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个群聊总结助手。请根据提供的聊天记录，使用标准 Markdown 格式进行总结，包括主要内容、关键结论和重要事项。请确保输出仅包含 Markdown 内容，不要有任何开场白或解释性文字。"
                },
                {
                    "role": "user",
                    "content": f"以下是最近的群聊记录，请进行总结：\n\n{prompt}"
                }
            ]
        }
    
    async with httpx.AsyncClient() as client:
        try:
            logger.debug(f"Requesting AI API ({api_type}): {url} with model {target_model}")
            response = await client.post(url, headers=headers, json=payload, timeout=60.0)
            
            resp_text = response.text
            if not resp_text:
                return f"AI API 返回空内容 (状态码: {response.status_code})"
                
            if response.status_code != 200:
                try:
                    error_data = response.json()
                    # 尝试解析不同格式的错误信息
                    if "error" in error_data:
                        if isinstance(error_data["error"], dict) and "message" in error_data["error"]:
                            return f"AI API 错误 ({response.status_code}): {error_data['error']['message']}"
                        return f"AI API 错误 ({response.status_code}): {error_data['error']}"
                except:
                    pass
                return f"AI API 错误: {response.status_code}"
            
            try:
                data = response.json()
            except json.JSONDecodeError:
                return "AI API 返回了非 JSON 格式的内容"
            
            # 解析响应
            if api_type == "gemini":
                # 解析 Gemini 官方响应格式
                try:
                    if "candidates" in data and len(data["candidates"]) > 0:
                        candidate = data["candidates"][0]
                        if "content" in candidate and "parts" in candidate["content"]:
                            parts = candidate["content"]["parts"]
                            return "".join([p.get("text", "") for p in parts])
                except Exception as e:
                    logger.error(f"解析 Gemini 响应失败: {e}, Data: {data}")
                    return "Gemini 响应格式异常"
            else:
                # 解析标准的 OpenAI 响应格式
                if "choices" in data and len(data["choices"]) > 0:
                    choice = data["choices"][0]
                    if "message" in choice and "content" in choice["message"]:
                        return choice["message"]["content"] or "未能生成总结"
            
            logger.error(f"Unexpected AI API response structure: {data}")
            return "AI API 返回数据格式异常"
            
        except Exception as e:
            logger.error(f"AI API 调用异常: {e}")
            return f"AI API 调用失败: {e}"

zongjie = on_command("总结", aliases={"zongjie", "群总结"}, priority=5, block=True)
zongjie_models = on_command("总结模型", aliases={"list_models"}, priority=5, block=True)

@zongjie.handle()
async def handle_zongjie(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
    count = plugin_config.zongjie_history_count
    model = None
    
    args = arg.extract_plain_text().strip().split()
    if args:
        # 第一个参数尝试解析为数量
        if args[0].isdigit():
            count = min(int(args[0]), 500)
            # 如果还有第二个参数，解析为模型
            if len(args) > 1:
                model = args[1]
        else:
            # 如果第一个参数不是数字，直接视为模型名
            model = args[0]
    
    msg = f"正在获取最近 {count} 条聊天记录"
    if model:
        msg += f"并使用模型 {model} 生成总结..."
    else:
        msg += f"并使用默认模型生成总结..."
    
    await zongjie.send(msg)
    
    # 1. 获取聊天记录
    messages = await get_group_history(bot, event.group_id, count)
    if not messages:
        await zongjie.finish("未能获取到足够的聊天记录，可能当前环境不支持获取历史记录。")
    
    # 2. 格式化记录
    formatted_text = format_messages(messages)
    if not formatted_text:
        await zongjie.finish("聊天记录为空，无法生成总结。")
    
    # 3. 调用 AI
    summary = await call_ai_api(formatted_text, model=model)
    
    # 如果 summary 以 "AI API 错误" 开头，说明调用失败，直接发送文本
    if summary.startswith("AI API"):
        await zongjie.finish(summary)
    
    # 4. 渲染 Markdown 为图片
    pic = None
    try:
        # 为总结添加一个标题
        md_content = f"# 群聊总结 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n\n{summary}"
        pic = await md_to_pic(md_content, width=800)
    except Exception as e:
        logger.error(f"渲染图片失败: {e}")
    
    if pic:
        await zongjie.finish(MessageSegment.image(pic))
    else:
        # 如果渲染失败，回退到发送纯文本
        await zongjie.finish(summary)

@zongjie_models.handle()
async def handle_list_models():
    base_url = plugin_config.zongjie_base_url.strip().rstrip('/')
    api_key = plugin_config.zongjie_api_key.strip()
    
    # 智能处理 URL 拼接
    if base_url.endswith('/chat/completions'):
        url = base_url.replace('/chat/completions', '/models')
    elif base_url.endswith('/v1'):
        url = f"{base_url}/models"
    else:
        url = f"{base_url}/v1/models"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "NoneBot2-Zongjie-Plugin/1.0.0"
    }
    
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(url, headers=headers, timeout=30.0)
            if response.status_code != 200:
                await zongjie_models.finish(f"获取模型列表失败 ({response.status_code})")
            
            data = response.json()
            # 兼容多种返回格式 (OpenAI 标准是 data 字段为列表)
            model_list = []
            if isinstance(data, dict) and "data" in data:
                if isinstance(data["data"], list):
                    model_list = [m.get("id") for m in data["data"] if isinstance(m, dict) and m.get("id")]
            
            if not model_list:
                await zongjie_models.finish("未能获取到有效模型列表，API 返回格式可能不符合 OpenAI 标准。")
            
            # 按字母排序并取前 30 个
            model_list.sort()
            reply = "当前 API 支持的部分模型：\n" + "\n".join(model_list[:30])
            if len(model_list) > 30:
                reply += f"\n... (共 {len(model_list)} 个)"
            reply += f"\n\n当前配置模型：{plugin_config.zongjie_model}"
            await zongjie_models.finish(reply)
            
        except Exception as e:
            logger.error(f"获取模型列表失败: {e}")
            await zongjie_models.finish(f"获取模型列表失败: {e}")
