import re
import json
import base64
import httpx
import asyncio
import traceback
from typing import List, Dict, Optional, Union
from pathlib import Path
from nonebot import on_command, get_plugin_config, logger, get_driver, require
from nonebot.adapters.onebot.v11 import Message, MessageSegment, MessageEvent, Bot, GroupMessageEvent
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException

try:
    require("nonebot_plugin_htmlrender")
    from nonebot_plugin_htmlrender import md_to_pic
    HTMLRENDER_AVAILABLE = True
except ImportError:
    HTMLRENDER_AVAILABLE = False
    logger.warning("nonebot_plugin_htmlrender not found, Markdown rendering disabled.")

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="Gemini 问答",
    description="基于 Google Gemini 模型的智能问答插件，支持联网搜索和图片输入",
    usage="指令：gemini / 问 / 提问 [内容]",
    config=Config,
)

plugin_config = get_plugin_config(Config)
driver_config = get_driver().config

# Whitelist Management
WHITELIST_FILE = Path("data/gemini_chat/whitelist.json")

def load_whitelist() -> List[str]:
    if not WHITELIST_FILE.exists():
        return []
    try:
        with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_whitelist(whitelist: List[str]):
    WHITELIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
        json.dump(whitelist, f, ensure_ascii=False, indent=2)

def is_whitelisted(group_id: str) -> bool:
    if not plugin_config.gemini_chat_whitelist_enabled:
        return True
    
    # Check config whitelist
    if str(group_id) in plugin_config.gemini_chat_whitelist:
        return True
        
    # Check runtime whitelist
    return str(group_id) in load_whitelist()

# Whitelist Commands
whitelist_add_cmd = on_command("gemini_whitelist_add", aliases={"gemini添加白名单"}, permission=SUPERUSER, priority=1, block=True)
whitelist_remove_cmd = on_command("gemini_whitelist_remove", aliases={"gemini移除白名单"}, permission=SUPERUSER, priority=1, block=True)

def remove_markdown(text: str) -> str:
    """移除 Markdown 格式，保留纯文本"""
    # Remove images ![alt](url)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    # Remove links [text](url) -> text
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
    # Remove bold **text** or __text__
    text = re.sub(r'(\*\*|__)(.*?)\1', r'\2', text)
    # Remove italic *text* or _text_
    text = re.sub(r'(\*|_)(.*?)\1', r'\2', text)
    # Remove inline code `text`
    text = re.sub(r'`(.*?)`', r'\1', text)
    # Remove block code ```text``` (simple removal of backticks)
    text = re.sub(r'```', '', text)
    # Remove headers # Title
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
    # Remove blockquotes > text
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
    return text.strip()

async def send_forward_msg(bot: Bot, event: MessageEvent, name: str, uin: str, content: Union[str, Message]):
    """发送合并转发消息"""
    node = {
        "type": "node",
        "data": {
            "name": name,
            "uin": uin,
            "content": content
        }
    }
    try:
        if isinstance(event, GroupMessageEvent):
            await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=[node])
        else:
            await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=[node])
    except Exception as e:
        logger.error(f"发送合并转发消息失败: {e}")
        # Fallback to normal send if forward message fails
        await bot.send(event, content)

@whitelist_add_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    group_id = args.extract_plain_text().strip()
    if not group_id:
        if isinstance(event, GroupMessageEvent):
            group_id = str(event.group_id)
        else:
            await whitelist_add_cmd.finish("请提供群号！")
            
    whitelist = load_whitelist()
    if group_id not in whitelist:
        whitelist.append(group_id)
        save_whitelist(whitelist)
        await whitelist_add_cmd.finish(f"已将群 {group_id} 添加到 Gemini 白名单。")
    else:
        await whitelist_add_cmd.finish(f"群 {group_id} 已在白名单中。")

@whitelist_remove_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    group_id = args.extract_plain_text().strip()
    if not group_id:
        if isinstance(event, GroupMessageEvent):
            group_id = str(event.group_id)
        else:
            await whitelist_remove_cmd.finish("请提供群号！")
            
    whitelist = load_whitelist()
    if group_id in whitelist:
        whitelist.remove(group_id)
        save_whitelist(whitelist)
        await whitelist_remove_cmd.finish(f"已将群 {group_id} 移出 Gemini 白名单。")
    else:
        await whitelist_remove_cmd.finish(f"群 {group_id} 不在白名单中。")

gemini_cmd = on_command("gemini", aliases={"问", "提问"}, priority=10, block=True)

async def download_image_as_base64(url: str) -> Optional[str]:
    """下载图片并转换为 Base64 字符串"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        # httpx >= 0.24.0 use proxy instead of proxies for single proxy
        async with httpx.AsyncClient(timeout=30, headers=headers, proxy=plugin_config.gemini_chat_proxy) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            return base64.b64encode(resp.content).decode("utf-8")
    except Exception as e:
        logger.warning(f"下载图片失败 {url}: {e}")
        return None

async def get_images_from_event(event: MessageEvent) -> List[Dict[str, str]]:
    """从事件中提取所有相关图片（消息图、回复图），返回 {mime_type, data} 字典列表"""
    images = []
    seen_urls = set()
    
    try:
        # 1. 提取消息中的图片
        if hasattr(event, "message"):
            for seg in event.message:
                if seg.type == "image":
                    url = seg.data.get("url")
                    if url and url not in seen_urls:
                        b64 = await download_image_as_base64(url)
                        if b64: 
                            images.append({"mime_type": "image/jpeg", "data": b64})
                            seen_urls.add(url)
        
        # 2. 提取回复消息中的图片
        # 使用 getattr 安全访问，防止部分事件类型没有 reply 属性
        reply = getattr(event, "reply", None)
        if reply:
            logger.debug(f"[Gemini] Found reply object: {type(reply)}")
            # event.reply.message 是 Message 对象，可以直接遍历
            # 加一层安全检查
            reply_msg = getattr(reply, "message", None)
            
            # 兼容性处理：如果 reply 是字典 (某些非标准适配器)
            if reply_msg is None and isinstance(reply, dict):
                reply_msg = reply.get("message")

            if reply_msg:
                try:
                    # 确保 reply_msg 是可迭代的 (Message 或 list)
                    if isinstance(reply_msg, (list, tuple, Message)):
                        for seg in reply_msg:
                            # 兼容对象属性访问 (MessageSegment) 和 字典访问
                            seg_type = getattr(seg, "type", None) or (seg.get("type") if isinstance(seg, dict) else None)
                            
                            if seg_type == "image":
                                data = getattr(seg, "data", None) or (seg.get("data") if isinstance(seg, dict) else {})
                                url = data.get("url")
                                if url and url not in seen_urls:
                                    logger.debug(f"[Gemini] Found image in reply: {url}")
                                    b64 = await download_image_as_base64(url)
                                    if b64: 
                                        images.append({"mime_type": "image/jpeg", "data": b64})
                                        seen_urls.add(url)
                    else:
                        logger.warning(f"Reply message is not iterable: {type(reply_msg)}")
                        
                except Exception as e:
                    logger.warning(f"遍历回复消息失败: {e}")
            else:
                logger.debug("Reply object exists but has no message.")
        
    except Exception as e:
        logger.error(f"get_images_from_event 发生未捕获异常: {traceback.format_exc()}")
        
    return images

@gemini_cmd.handle()
async def handle_gemini(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    # Whitelist Check
    if isinstance(event, GroupMessageEvent) and not is_whitelisted(str(event.group_id)):
        await gemini_cmd.finish("本群未开启 Gemini 问答功能。")

    # 1. 尝试使用 gemini_chat 配置
    api_key = plugin_config.gemini_chat_api_key
    api_url = plugin_config.gemini_chat_api_url
    model_id = plugin_config.gemini_chat_model

    # 2. 如果未配置 API Key，尝试复用拟人插件 (personification) 的配置
    if not api_key and hasattr(driver_config, "personification_api_key"):
        logger.info("Gemini 插件：未配置 API Key，尝试复用拟人插件配置...")
        api_key = driver_config.personification_api_key
        
        # 只有在复用 Key 时，才考虑复用 URL 和 Model (如果 gemini 自身也没配置)
        if hasattr(driver_config, "personification_api_url") and not plugin_config.gemini_chat_api_key:
             p_url = str(driver_config.personification_api_url).strip()
             # 只有当拟人插件的 URL 看起来像 Gemini URL 或 通用转发 URL 时才复用
             # 避免复用 OpenAI 默认 URL (https://api.openai.com/v1)
             if "openai.com" not in p_url or "generativelanguage" in p_url or "v1beta" in p_url:
                 api_url = p_url
        
        # 已移除：不复用拟人插件的模型配置，始终使用 gemini_chat 配置（支持 .env 自定义）
        # if hasattr(driver_config, "personification_model") and not plugin_config.gemini_chat_api_key:
        #      model_id = str(driver_config.personification_model)

    if not api_key:
        await gemini_cmd.finish("未配置 Gemini API Key，且未找到可用的拟人插件配置。")

    # 智能处理 API URL (参考拟人插件逻辑)
    # 如果 URL 中没有包含 generateContent，则自动补全
    if "generateContent" not in api_url:
        if not api_url.endswith("/"):
            api_url += "/"
        if "models/" not in api_url:
            api_url += f"v1beta/models/{model_id}:generateContent"
        else:
            api_url += ":generateContent"

    # 获取用户输入文本
    prompt = args.extract_plain_text().strip()
    
    # 获取图片
    images = await get_images_from_event(event)

    # 获取回复内容
    reply_text = ""
    reply = getattr(event, "reply", None)
    if reply:
        reply_msg = getattr(reply, "message", None) or (reply.get("message") if isinstance(reply, dict) else None)
        if reply_msg:
            reply_text = str(reply_msg)
    
    # 智能合并 prompt 和回复内容
    if prompt and reply_text:
        prompt = f"{prompt}\n\n【引用内容】：\n{reply_text}"
    elif not prompt and reply_text:
        prompt = reply_text
    
    # 如果没有文本且没有图片，提示用户
    if not prompt and not images:
        await gemini_cmd.finish("请输入问题或发送图片。")

    await gemini_cmd.send("正在思考中，请稍候...")

    # 构造请求
    contents = []
    
    # System Prompt
    system_instruction = None
    if plugin_config.gemini_chat_system_prompt:
        system_instruction = {"parts": [{"text": plugin_config.gemini_chat_system_prompt}]}

    # User Message
    user_parts = []
    if prompt:
        user_parts.append({"text": prompt})
    
    for img in images:
        user_parts.append({
            "inline_data": {
                "mime_type": img["mime_type"],
                "data": img["data"]
            }
        })
    
    contents.append({"role": "user", "parts": user_parts})

    # Tools (Google Search)
    tools = []
    if plugin_config.gemini_chat_search_enabled:
        tools.append({"google_search": {}})

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": plugin_config.gemini_chat_temperature,
            "maxOutputTokens": 2048,
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
    }

    if system_instruction:
        payload["systemInstruction"] = system_instruction
        
    if tools:
        payload["tools"] = tools

    # API URL 处理
    # 移除旧的 API URL 处理逻辑，因为上面已经处理过了
    if "key=" not in api_url:
        connector = "&" if "?" in api_url else "?"
        api_url += f"{connector}key={api_key}"

    headers = {"Content-Type": "application/json"}
    
    # Proxy Handling
    # 优先使用插件配置的代理
    proxy = plugin_config.gemini_chat_proxy
    
    # 如果没有配置代理，且使用的是中转站（如 gptbest.vip），通常不需要代理
    # 为了避免环境变量中的错误代理导致连接失败，我们显式控制 trust_env
    trust_env = True
    if not proxy:
        # 如果 URL 是国内直连的中转站，可以尝试禁用系统代理
        # 这里为了稳妥，如果用户没配代理，我们暂时保持 trust_env=True，
        # 但如果遇到 TLS 错误，建议用户检查系统代理或显式配置 gemini_chat_proxy
        pass

    logger.info(f"Gemini API Request URL: {api_url}")
    
    try:
        # 增加连接超时时间，并显式处理代理
        # 如果未配置代理，httpx 会自动读取环境变量 (trust_env=True)
        # 如果配置了代理，则使用配置的代理
        timeout = httpx.Timeout(plugin_config.gemini_chat_timeout, connect=20.0)
        
        async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
            resp = await client.post(api_url, json=payload, headers=headers)
            
            if resp.status_code != 200:
                logger.error(f"Gemini API Error: {resp.text}")
                await gemini_cmd.finish(f"调用 API 失败: {resp.status_code} - {resp.text[:100]}")
            
            result = resp.json()
            
            # 解析响应
            try:
                candidate = result["candidates"][0]
                content_parts = candidate.get("content", {}).get("parts", [])
                
                final_text = ""
                for part in content_parts:
                    if "text" in part:
                        final_text += part["text"]
                
                if not final_text:
                    await gemini_cmd.finish("Gemini 未返回文本内容。")

                # Markdown 渲染逻辑
                if HTMLRENDER_AVAILABLE:
                    try:
                        # 使用 md_to_pic 渲染为图片
                        # 可以在这里添加一些自定义 CSS 或配置
                        pic_bytes = await md_to_pic(final_text, width=800)
                        await gemini_cmd.finish(MessageSegment.image(pic_bytes))
                    except FinishedException:
                        raise
                    except Exception as e:
                        logger.error(f"Markdown 渲染失败: {e}")
                        # 降级处理：回退到纯文本合并转发
                        pass
                
                # 如果渲染失败或不可用，使用纯文本处理
                # 移除 Markdown 格式
                plain_text = remove_markdown(final_text)

                if not plain_text:
                    # 如果移除 Markdown 后为空（例如全是图片链接），尝试直接发送原文
                    plain_text = final_text

                # 发送合并转发消息
                bot_name = "Gemini"
                bot_id = str(event.self_id)
                
                await send_forward_msg(bot, event, bot_name, bot_id, plain_text)
                await gemini_cmd.finish()

            except (KeyError, IndexError) as e:
                logger.error(f"解析响应失败: {e} - Raw: {result}")
                await gemini_cmd.finish("解析响应失败，请查看日志。")
    
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"Gemini 请求异常: {e}")
        await gemini_cmd.finish(f"请求发生错误: {e}")
