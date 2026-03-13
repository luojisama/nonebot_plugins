import httpx
import json
import base64
import asyncio
import sys
import time
import traceback
from pathlib import Path
from nonebot import get_plugin_config, on_command, logger, get_driver
from nonebot.adapters.onebot.v11 import Message, MessageSegment, MessageEvent, GroupMessageEvent, Bot
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException

from .config import Config
from .presets import DEFAULT_PROMPTS

try:
    from plugin.sign_in.utils import get_user_data, update_user_data
except ImportError:
    # Fallback if import fails (e.g. plugin structure difference)
    # Try relative import if they are in the same package (unlikely for plugins)
    # Or just mock it/log warning
    logger.warning("Failed to import sign_in plugin. Economy system will be disabled.")
    def get_user_data(user_id): return {"coins": 999999}
    def update_user_data(user_id, **kwargs): pass

# 确保数据目录存在
DATA_DIR = Path("data/ai_painting")
DATA_DIR.mkdir(parents=True, exist_ok=True)
BLACKLIST_FILE = DATA_DIR / "blacklist.json"
WHITELIST_FILE = DATA_DIR / "whitelist.json"

# 用户冷却时间记录 {user_id: last_usage_timestamp}
user_cooldowns = {}

plugin_config = get_plugin_config(Config)

if not BLACKLIST_FILE.exists():
    BLACKLIST_FILE.write_text("[]", encoding="utf-8")

if not WHITELIST_FILE.exists():
    WHITELIST_FILE.write_text("[]", encoding="utf-8")

def load_blacklist() -> list:
    try:
        data = json.loads(BLACKLIST_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except:
        return []

def save_blacklist(blacklist: list):
    BLACKLIST_FILE.write_text(json.dumps(blacklist, ensure_ascii=False, indent=2), encoding="utf-8")

def load_whitelist() -> list:
    try:
        data = json.loads(WHITELIST_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except:
        return []

def save_whitelist(whitelist: list):
    WHITELIST_FILE.write_text(json.dumps(whitelist, ensure_ascii=False, indent=2), encoding="utf-8")

# 合并配置文件和运行时黑名单
def get_blacklist() -> list:
    runtime_bl = load_blacklist()
    config_bl = plugin_config.ai_painting_blacklist
    return list(set(runtime_bl + config_bl))

# 获取白名单
def get_whitelist() -> list:
    return load_whitelist()



def get_avatar_url(user_id: str) -> str:
    return f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"

async def download_image_as_base64(url: str) -> str:
    """下载图片并转换为 Base64 字符串"""
    # 增加 User-Agent 防止被某些 CDN 拦截
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            return base64.b64encode(resp.content).decode("utf-8")
    except Exception as e:
        logger.warning(f"下载图片失败 {url}: {e}")
        return None

async def get_images_from_event(bot: Bot, event: MessageEvent) -> list[dict]:
    """从事件中提取所有相关图片（消息图、回复图、头像），返回 {url, base64} 字典列表"""
    images = []
    seen_urls = set() # 用于去重
    
    try:
        # 调试日志：打印消息段结构
        if hasattr(event, "message"):
            logger.debug(f"[AI Painting] Event Message Segments: {[s.type for s in event.message]}")

        # 1. 提取消息中的图片
        if hasattr(event, "message"):
            for seg in event.message:
                if seg.type == "image":
                    url = seg.data.get("url")
                    if url and url not in seen_urls:
                        b64 = await download_image_as_base64(url)
                        if b64: 
                            images.append({"url": url, "base64": b64})
                            seen_urls.add(url)
        
        # 2. 提取回复消息中的图片
        # 使用 getattr 安全访问，防止部分事件类型没有 reply 属性
        reply = getattr(event, "reply", None)
        if reply:
            logger.debug(f"[AI Painting] Found reply object: {type(reply)}")
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
                                    logger.debug(f"[AI Painting] Found image in reply: {url}")
                                    b64 = await download_image_as_base64(url)
                                    if b64: 
                                        images.append({"url": url, "base64": b64})
                                        seen_urls.add(url)
                    else:
                        logger.warning(f"Reply message is not iterable: {type(reply_msg)}")
                        
                except Exception as e:
                    logger.warning(f"遍历回复消息失败: {e}")
            else:
                logger.debug("Reply object exists but has no message.")

        # 3. 提取 @用户的头像
        # 注意：如果用户只想用图不想用头像，可能会有冲突，但通常 @人 就是为了用头像
        if hasattr(event, "message"):
            for seg in event.message:
                if seg.type == "at":
                    uid = str(seg.data.get("qq"))
                    
                    # 过滤掉 @全体成员
                    if uid == "all":
                        continue
                    
                    # 过滤掉机器人自己的头像（避免 @Bot 触发时把 Bot 头像也算进去）
                    if uid == bot.self_id:
                        logger.debug(f"跳过 Bot 自身头像: {uid}")
                        continue
                        
                    url = get_avatar_url(uid)
                    
                    # 去重检查
                    if url in seen_urls:
                        continue
                        
                    # 增加一点点延迟，避免并发请求 QQ 头像接口被频控（虽然概率很低）
                    await asyncio.sleep(0.1)
                    b64 = await download_image_as_base64(url)
                    
                    if b64: 
                        images.append({"url": url, "base64": b64})
                        seen_urls.add(url)
                        logger.debug(f"提取头像成功: {uid}")
                    else:
                        logger.warning(f"提取头像失败: {uid}")
            
        logger.debug(f"[AI Painting] Total images extracted: {len(images)}")
        
    except Exception as e:
        logger.error(f"get_images_from_event 发生未捕获异常: {traceback.format_exc()}")
        
    return images

# --- 核心逻辑 ---

async def call_api(prompt: str, n: int = 1, size: str = "1024x1024", images: list[dict] = None) -> list[str]:
    headers = {
        "Authorization": f"Bearer {plugin_config.ai_painting_api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": plugin_config.ai_painting_model,
        "n": n,
        "size": size,
    }

    # 1. 基础 Prompt 设置
    # 如果 prompt 为空（例如只发了 @user），则设置默认 prompt 以避免 "prompt is required"
    if not prompt.strip():
        prompt = "a drawing of this person"

    # 移除 {avatar} 占位符，并替换为 URL（如果存在）
    # 同时，如果有图片但没有 {avatar} 占位符，我们也把 URL 加到 prompt 最后，
    # 这样不支持 Base64 传图的模型（如 MJ/Gemini Wrapper）也能通过 URL 获取参考图。
    
    image_urls = [img["url"] for img in images] if images else []
    base64_images = [img["base64"] for img in images] if images else []
    
    if "{avatar}" in prompt:
        # 如果有占位符，优先用 URL 替换占位符
        # 取第一张图的 URL (假设是头像)
        if image_urls:
             prompt = prompt.replace("{avatar}", image_urls[0])
             # 如果有多张图，剩下的加到后面
             if len(image_urls) > 1:
                 prompt += " " + " ".join(image_urls[1:])
        else:
             prompt = prompt.replace("{avatar}", "")
    elif image_urls:
        # 没有占位符，直接追加到末尾
        prompt += " " + " ".join(image_urls)
    
    # 2. 构造 Payload
    # 必须包含 prompt，否则标准 API 会报错 "prompt is required"
    payload["prompt"] = prompt

    # 针对不同 API 格式的适配
    is_sd_webui = "sdapi" in plugin_config.ai_painting_api_url
    is_chat_api = "chat/completions" in plugin_config.ai_painting_api_url
    
    # 增加对 Vision 模型的显式判断
    model_name = plugin_config.ai_painting_model.lower()
    is_vision_model = "vision" in model_name or "gpt-4" in model_name or "gemini" in model_name or "claude" in model_name or "banana" in model_name or "nano" in model_name
    
    # 如果配置了 Chat 接口或者是 Vision 模型，使用 Chat 格式
    if is_chat_api or (is_vision_model and not is_sd_webui):
        # Chat Completion 格式 (OpenAI Vision / Gemini via OpenAI Wrapper)
        # 参考 nonebot_plugin_templates_draw 的实现
        content = [{"type": "text", "text": prompt}]
        
        # 记录图片数量
        if base64_images:
            logger.info(f"[AI Painting] Packing {len(base64_images)} images into Chat Payload.")
            
        for img_b64 in base64_images:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img_b64}"
                }
            })
        
        payload["messages"] = [
            {
                "role": "user",
                "content": content
            }
        ]
        # Chat API 通常不接受 'prompt' 字段，只接受 'messages'
        if "prompt" in payload:
            del payload["prompt"]
            
        # Chat API 可能不支持 size，但支持 n (choices)
        if "size" in payload:
            del payload["size"]

    elif is_sd_webui:
        # SD WebUI (img2img)
        # init_images 接受 base64 列表
        if base64_images:
            payload["init_images"] = base64_images
            payload["denoising_strength"] = 0.75
            logger.info(f"[AI Painting] Packing {len(base64_images)} images into SD WebUI Payload (init_images).")
        
        # SD WebUI 格式 (简单适配 txt2img)
        # 使用 update 避免覆盖之前注入的 init_images
        payload.update({
            "steps": 20,
            "batch_size": n,
            "width": 1024 if size == "1024x1024" else 512,
            "height": 1024 if size == "1024x1024" else 512,
        })
        # 删除不兼容的 OpenAI 字段
        payload.pop("model", None)
        payload.pop("n", None)
        payload.pop("size", None)
        payload.pop("image", None) # SD WebUI 不用 image 字段
        payload.pop("image_url", None) # SD WebUI 不用 image_url 字段
        payload.pop("prompt", None) # SD WebUI 有时用 prompt 有时用 payload["prompt"]? 
        # 修正: SD WebUI 标准字段是 'prompt'，但我们之前已经在 payload["prompt"] 设置了
        # 只是需要确保不被 pop 掉。上面没有 pop prompt。
        
    else:
        # OpenAI Image Generation Compatible (Standard /v1/images/generations)
        # 这种接口通常不支持传图，或者使用自定义字段
        # 我们保留 prompt，并尝试注入 image/images 字段
        if base64_images:
            payload["images"] = base64_images
            payload["image"] = base64_images[0]
            logger.info(f"[AI Painting] Packing {len(base64_images)} images into Standard/MJ Payload (images field).")
            
    # 日志记录 (截断 Base64)
    payload_log = payload.copy()
    if "init_images" in payload_log:
        payload_log["init_images"] = [f"Base64({len(s)} chars)" for s in payload_log["init_images"]]
    if "images" in payload_log and isinstance(payload_log["images"], list):
        payload_log["images"] = [f"Base64({len(s)} chars)" if isinstance(s, str) else s for s in payload_log["images"]]
    if "image" in payload_log and isinstance(payload_log["image"], str) and len(payload_log["image"]) > 100:
        payload_log["image"] = f"Base64({len(payload_log['image'])} chars)"
    if "messages" in payload_log:
        # 深度复制 messages 以避免修改原 payload
        import copy
        msgs = copy.deepcopy(payload_log["messages"])
        for msg in msgs:
            if isinstance(msg.get("content"), list):
                for part in msg["content"]:
                    if part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url.startswith("data:image"):
                            part["image_url"]["url"] = f"Base64...({len(url)} chars)"
        payload_log["messages"] = msgs

    logger.info(f"AI Painting Request: {plugin_config.ai_painting_api_url} | Model: {plugin_config.ai_painting_model}")
    logger.debug(f"Request Payload: {json.dumps(payload_log, ensure_ascii=False)}")
    
    async with httpx.AsyncClient(timeout=300, proxy=plugin_config.ai_painting_proxy or None) as client:
        try:
            resp = await client.post(plugin_config.ai_painting_api_url, json=payload, headers=headers)
            
            if resp.status_code != 200:
                error_text = resp.text
                # 针对 422 敏感内容拦截的优化提示
                if resp.status_code == 422 and "Gemini could not generate" in error_text:
                    raise Exception("安全拦截: 您的提示词可能包含敏感内容，被 AI 模型拒绝生成。请尝试修改描述。")
                raise Exception(f"API Error ({resp.status_code}): {error_text}")
            
            data = resp.json()
            
            urls = []
            if "data" in data: # OpenAI Image 格式
                for item in data["data"]:
                    if "url" in item:
                        urls.append(item["url"])
                    elif "b64_json" in item:
                        urls.append(f"base64://{item['b64_json']}")
            elif "choices" in data: # Chat Completion 格式
                # 收集输入图片的 URL 以便过滤
                input_urls = set()
                if images:
                    for img in images:
                        if "url" in img:
                            input_urls.add(img["url"])

                for choice in data["choices"]:
                    content = choice.get("message", {}).get("content", "")
                    if content:
                        # 尝试提取 Markdown 图片链接
                        import re
                        # 匹配 ![desc](url) 或 [desc](url) 或 直接 url
                        # 某些模型可能直接返回 URL 文本
                        # 先尝试匹配 http/https 链接
                        links = re.findall(r'https?://[^\s\)]+', content)
                        if links:
                            # 过滤掉非图片链接可能比较难，但通常 Chat-to-Image 只返回图片
                            # 简单清理一下 markdown 的尾部括号
                            for link in links:
                                clean_link = link.split('"')[0].split(')')[0]
                                # 过滤掉参考图 (简单的包含检查，因为 input url 可能带参数)
                                is_ref = False
                                for ref_url in input_urls:
                                    if ref_url in clean_link or clean_link in ref_url:
                                        is_ref = True
                                        break
                                if not is_ref:
                                    urls.append(clean_link)
                        else:
                            # 如果没有链接，可能返回了 base64? 或者是错误信息
                            # 这里假设返回的是纯文本 URL
                            if content.startswith("http"):
                                clean_link = content.strip()
                                # 同样过滤
                                is_ref = False
                                for ref_url in input_urls:
                                    if ref_url in clean_link or clean_link in ref_url:
                                        is_ref = True
                                        break
                                if not is_ref:
                                    urls.append(clean_link)
            
            elif "images" in data: # SD WebUI 格式 (返回 base64)
                for img_b64 in data["images"]:
                    urls.append(f"base64://{img_b64}")
            else:
                 raise Exception(f"Unknown response format: {data.keys()}")
            
            # 限制返回数量 (移除参考图后，确保数量符合预期)
            if len(urls) > n:
                urls = urls[:n]

            return urls
            
        except Exception as e:
            logger.error(f"AI Painting Failed: {traceback.format_exc()}")
            raise e

# --- 命令处理器 ---

draw_cmd = on_command("绘图", aliases={"画图", "ai画图"}, priority=5, block=True)
blacklist_cmd = on_command("绘图黑名单", permission=SUPERUSER, priority=5, block=True)
whitelist_cmd = on_command("绘图白名单", permission=SUPERUSER, priority=5, block=True)
balance_cmd = on_command("查询余额", aliases={"额度", "查询额度", "ai余额"}, priority=5, block=True)

@draw_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    try:
        # 1. 检查黑名单
        user_id = str(event.user_id)
        group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else None
        
        blacklist = get_blacklist()
        if user_id in blacklist or (group_id and group_id in blacklist):
            await draw_cmd.finish("您或当前群组已被禁止使用绘图功能。")

        # 2. 检查冷却时间 (CD: 3分钟)
        # 超级用户免除冷却
        superusers = get_driver().config.superusers
        if user_id not in superusers:
            current_time = time.time()
            last_time = user_cooldowns.get(user_id, 0)
            cooldown_seconds = 180 # 3分钟
            
            if current_time - last_time < cooldown_seconds:
                remaining = int(cooldown_seconds - (current_time - last_time))
                # 友好提示
                await draw_cmd.finish(f"🎨 您的画笔正在清洗中，请休息 {remaining} 秒后再来吧！")
        else:
            # 即使是超级用户，也更新一下时间，避免逻辑混乱，或者干脆不更新？
            # 为了逻辑统一，超级用户不检查也不记录，或者只记录不检查。
            # 这里选择：不检查，但下面记录时间的代码是通用的，所以超级用户也会被记录，但不影响下次判断。
            pass

        # 3. 解析参数
        raw_text = args.extract_plain_text().strip()

        # 3.0 检查是否是查看模板命令
        if raw_text == "模板":
            template_list = "\n".join([f"- {k}" for k in DEFAULT_PROMPTS.keys()])
            await draw_cmd.finish(f"🎨 可用绘图模板：\n{template_list}\n使用方法：绘图 [模板名] [额外描述]")
            
        # 3.0.1 检查是否是帮助命令
        if raw_text == "帮助":
            price = plugin_config.ai_painting_price
            help_msg = (
                f"🎨 AI 绘图使用帮助\n"
                f"━━━━━━━━━━━━━━\n"
                f"基础指令：\n"
                f"• 绘图 [描述]\n"
                f"• 绘图 [图片/@用户] [描述]\n\n"
                f"进阶功能：\n"
                f"• 多张生成：绘图 [N]张 [描述]\n"
                f"  例：绘图 2张 少女\n"
                f"• 使用模板：绘图 [模板名] [额外描述]\n"
                f"  例：绘图 手办化1 @用户 (无需额外描述)\n"
                f"  查看所有模板：绘图 模板\n\n"
                f"组合技巧：\n"
                f"• 绘图 3张 手办化1 @某人\n"
                f"  (使用对方头像+手办模板，生成3张图片)\n\n"
                f"当前价格：{price} 金币/张"
            )
            await draw_cmd.finish(help_msg)
        
        # 3.1 R18G 内容过滤
        # 简单的关键词匹配，可根据需要扩展
        r18g_keywords = ["r18", "r18g", "nsfw", "sex", "nude", "naked", "裸", "色情", "血腥", "暴力", "Guro", "guro"]
        if any(k in raw_text.lower() for k in r18g_keywords):
            # 记录冷却防止滥用（可选，这里直接记录）
            current_time = time.time()
            user_cooldowns[user_id] = current_time
            await draw_cmd.finish("⚠️ 禁止生成 R18/R18G 或不适内容！")

        # 获取相关图片 (消息图、回复图、头像)
        images = await get_images_from_event(bot, event)
        
        # 如果没有图片且没有文字，则无法绘图
        if not raw_text and not images:
            await draw_cmd.finish("请提供绘图描述或参考图！\n发送【绘图 帮助】查看详细用法。\n示例: 绘图 2张 少女")

        # 记录冷却时间 (在参数校验通过后记录，避免误触冷却)
        current_time = time.time()
        user_cooldowns[user_id] = current_time

        # 4. 处理多图参数 (例如 "绘图 3张 少女")
        count = 1
        # 简单的正则或切分来检测 "x张"
        import re
        match = re.match(r"^(\d+)张\s+(.*)$", raw_text)
        if match:
            try:
                count = int(match.group(1))
                raw_text = match.group(2)
                if count > 4: count = 4 # 限制最大数量
                if count < 1: count = 1
            except:
                pass
        
        # 4.2 模板匹配 (在处理完数量参数后进行，因为 raw_text 已经去除了 "x张")
        matched_template = None
        # 按长度降序匹配，防止前缀冲突
        sorted_keys = sorted(DEFAULT_PROMPTS.keys(), key=len, reverse=True)
        for key in sorted_keys:
            if raw_text.startswith(key):
                matched_template = key
                # 提取额外描述 (如果有)
                extra_desc = raw_text[len(key):].strip()
                # 组合 prompt: 模板内容 + 额外描述
                # 如果模板很长，可能需要适当截断或处理，这里直接拼接
                raw_text = f"{DEFAULT_PROMPTS[key]} {extra_desc}".strip()
                break
        
        # 4.5 金币检查
        price_per_image = plugin_config.ai_painting_price
        total_cost = price_per_image * count
        
        # 检查白名单
        is_free = False
        if group_id and group_id in get_whitelist():
            is_free = True
            total_cost = 0

        if total_cost > 0:
            user_data = get_user_data(user_id)
            user_coins = user_data.get("coins", 0)
            if user_coins < total_cost:
                await draw_cmd.finish(f"💸 金币不足！\n需要 {total_cost} 金币，您只有 {user_coins} 金币。\n请通过签到或商店获取金币。")

        # 5. 执行绘图
        prompt = raw_text
        cost_msg = f" (消耗 {total_cost} 金币)" if total_cost > 0 else (" (✨白名单免费)" if is_free else "")
        await draw_cmd.send(f"AI 绘图中... (生成 {count} 张){cost_msg}")
        
        urls = await call_api(prompt, n=count, images=images)
        
        if not urls:
                await draw_cmd.finish("生成失败，API 未返回图片。")
        
        # 扣除金币 (生成成功后)
        if total_cost > 0:
            current_data = get_user_data(user_id)
            current_coins = current_data.get("coins", 0)
            new_balance = max(0, current_coins - total_cost)
            update_user_data(user_id, coins=new_balance)

        # 构建消息
        msg = Message()
        for url in urls:
            if url.startswith("base64://"):
                msg.append(MessageSegment.image(file=url))
            else:
                msg.append(MessageSegment.image(file=url))
        
        # 发送
        await draw_cmd.finish(msg)
        
    except FinishedException:
        raise # 重新抛出 FinishedException 以便 NoneBot 正确结束
    except Exception as e:
        err_msg = traceback.format_exc()
        logger.error(f"绘图异常: {err_msg}")
        await draw_cmd.finish(f"绘图失败:\n{err_msg}")

# --- 黑名单管理 (命令保持不变) ---
@blacklist_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    # ... (原有逻辑，此处未变)
    cmd = args.extract_plain_text().strip()
    
    if not cmd:
        await blacklist_cmd.finish("用法: 绘图黑名单 添加/删除 [QQ号/群号]")

    parts = cmd.split()
    action = parts[0]
    target = parts[1] if len(parts) > 1 else str(event.user_id) # 默认操作自己？不，默认需要指定
    
    # 如果是在群里直接 @某人
    for seg in event.message:
        if seg.type == "at":
            target = str(seg.data.get("qq"))
            break
            
    runtime_bl = load_blacklist()
    
    if action == "添加":
        if target not in runtime_bl:
            runtime_bl.append(target)
            save_blacklist(runtime_bl)
            await blacklist_cmd.finish(f"已将 {target} 加入绘图黑名单。")
        else:
            await blacklist_cmd.finish(f"{target} 已在黑名单中。")
            
    elif action == "删除":
        if target in runtime_bl:
            runtime_bl.remove(target)
            save_blacklist(runtime_bl)
            await blacklist_cmd.finish(f"已将 {target} 移出绘图黑名单。")
        else:
            await blacklist_cmd.finish(f"{target} 不在黑名单中。")
            
    elif action == "列表":
        bl = get_blacklist()
        await blacklist_cmd.finish(f"当前黑名单: {bl}")
        
    else:
        await blacklist_cmd.finish("未知操作，支持: 添加、删除、列表")


# --- 白名单管理 ---
@whitelist_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    cmd = args.extract_plain_text().strip()
    
    if not cmd:
        await whitelist_cmd.finish("用法: 绘图白名单 添加/删除 [群号]")

    parts = cmd.split()
    action = parts[0]
    target = parts[1] if len(parts) > 1 else (str(event.group_id) if isinstance(event, GroupMessageEvent) else None)
    
    if not target:
         await whitelist_cmd.finish("请指定群号！")

    whitelist = get_whitelist()
    
    if action == "添加":
        if target not in whitelist:
            whitelist.append(target)
            save_whitelist(whitelist)
            await whitelist_cmd.finish(f"已将群 {target} 加入绘图白名单（免费绘图）。")
        else:
            await whitelist_cmd.finish(f"群 {target} 已在白名单中。")
            
    elif action == "删除":
        if target in whitelist:
            whitelist.remove(target)
            save_whitelist(whitelist)
            await whitelist_cmd.finish(f"已将群 {target} 移出绘图白名单。")
        else:
            await whitelist_cmd.finish(f"群 {target} 不在白名单中。")
            
    elif action == "列表":
        await whitelist_cmd.finish(f"当前免费白名单群组: {whitelist}")
        
    else:
        await whitelist_cmd.finish("未知操作，支持: 添加、删除、列表")

@balance_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    # 优先使用配置的余额查询 URL
    if hasattr(plugin_config, "ai_painting_balance_url") and plugin_config.ai_painting_balance_url:
        base_url = plugin_config.ai_painting_balance_url.rstrip("/")
    else:
        # 尝试从配置的 API URL 推导额度查询 URL
        api_url = plugin_config.ai_painting_api_url
        
        if "/v1/" in api_url:
            base_url = api_url.split("/v1/")[0]
        else:
            from urllib.parse import urlparse
            parsed = urlparse(api_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
        
    # 优先使用专门的余额 Token，否则使用绘图 API Key
    token = plugin_config.ai_painting_balance_token or plugin_config.ai_painting_api_key
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # New-API-User 必须存在 (如果在配置中设置了)
    if hasattr(plugin_config, "ai_painting_balance_user_id") and plugin_config.ai_painting_balance_user_id:
        headers["New-API-User"] = plugin_config.ai_painting_balance_user_id
    
    # New API / One API 用户信息接口 (包含余额)
    user_url = f"{base_url}/api/user/self"
    
    # 备用接口 (旧版 Subscription)
    quota_url_subscription = f"{base_url}/v1/dashboard/billing/subscription"
    
    # 备用接口 (One API Token Quota)
    quota_url_token = f"{base_url}/v1/token/quota"
    
    await balance_cmd.send("正在查询额度...")
    
    try:
        async with httpx.AsyncClient(timeout=30, proxy=plugin_config.ai_painting_proxy or None) as client:
            # 尝试 /api/user/self
            try:
                resp = await client.get(user_url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success") and "data" in data:
                        user_data = data["data"]
                        # quota: 当前剩余额度
                        # used_quota: 已使用额度
                        quota = user_data.get("quota", 0)
                        used_quota = user_data.get("used_quota", 0)
                        
                        # 转换汇率 (通常 500000 = $1)
                        # 并转换为人民币 (假设汇率 7.2)
                        usd_to_rmb = 1
                        remaining_usd = quota / 500000
                        used_usd = used_quota / 500000
                        
                        remaining_rmb = remaining_usd * usd_to_rmb
                        used_rmb = used_usd * usd_to_rmb
                        
                        msg = (
                            f"当前余额 (NewAPI):\n"
                            f"剩余: ¥{remaining_rmb:.4f}\n"
                            f"已用: ¥{used_rmb:.4f}"
                        )
                        await balance_cmd.finish(msg)
            except FinishedException:
                raise
            except Exception as e:
                logger.warning(f"NewAPI 余额查询失败: {e}")
            
            # 如果上面的失败了，尝试旧接口 (Subscription)
            resp = await client.get(quota_url_subscription, headers=headers)
            
            used_quota = 0.0
            total_quota = 0.0
            remaining_quota = 0.0
            found = False
            
            if resp.status_code == 200:
                data = resp.json()
                # OpenAI Subscription Format
                # ...
                
                if "hard_limit_usd" in data:
                    total_quota = float(data.get("hard_limit_usd", 0))
                    # 如果中转站返回了 remaining，直接用
                    if "remaining_amount" in data:
                        remaining_quota = float(data["remaining_amount"]) # 某些中转站字段
                        found = True
                    # 否则我们需要查询 usage 接口，这比较麻烦，先看能不能 fallback 到 quota 接口
                
            # 如果标准接口没拿到余额，尝试 One API 的 /v1/token/quota (这是专门查余额的)
            if not found:
                 resp_alt = await client.get(quota_url_token, headers=headers)
                 if resp_alt.status_code == 200:
                     data = resp_alt.json()
                     # One API / New API Format:
                     # {"success": true, "message": "", "data": {"remain_quota": 123456, "used_quota": 7890, ...}}
                     # 或者直接 {"id": 1, "quota": 12345} (旧版/简化版)
                     
                     if "data" in data and "remain_quota" in data["data"]:
                         # 单位通常是 500000 = $1
                         remain = float(data["data"]["remain_quota"])
                         remaining_quota = remain / 500000
                         found = True
                     elif "quota" in data:
                         # 某些旧版直接返回 quota，但不确定是剩余还是总额，通常是剩余
                         # 且单位可能是 $ 也可能是 token 积分
                         # 如果是整数且很大，可能是积分；如果是小数，可能是 $
                         val = float(data["quota"])
                         if val > 10000: # 假设很大是积分
                             remaining_quota = val / 500000
                         else:
                             remaining_quota = val
                         found = True
            
            if found:
                # 同样转换为人民币
                usd_to_rmb = 7.2
                remaining_rmb = remaining_quota * usd_to_rmb
                await balance_cmd.finish(f"💰 当前剩余额度: ¥{remaining_rmb:.4f}")
            else:
                # 如果都失败了，打印原始响应以便调试 (截取前100字符)
                err_info = ""
                if resp.status_code != 200:
                    err_info += f"Sub({resp.status_code}): {resp.text[:100]} "
                if 'resp_alt' in locals() and resp_alt.status_code != 200:
                    err_info += f"Quota({resp_alt.status_code}): {resp_alt.text[:100]}"
                    
                await balance_cmd.finish(f"查询失败或格式未知。接口响应: {err_info}")

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"余额查询异常: {traceback.format_exc()}")
        await balance_cmd.finish(f"查询异常: {e}")
