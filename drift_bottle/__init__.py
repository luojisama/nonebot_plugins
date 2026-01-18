import json
import random
import time
import httpx
from pathlib import Path
from typing import List, Dict, Any, Optional

from nonebot import on_command, get_plugin_config, logger, get_driver
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment, GroupMessageEvent, MessageEvent
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.permission import SUPERUSER

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="漂流瓶",
    description="支持扔、捡漂流瓶，跨群互通",
    usage="""
    扔漂流瓶 [内容] : 扔出一个漂流瓶（支持图片、文字、表情）
    捡漂流瓶 : 随机捡到一个漂流瓶
    查看漂流瓶 [页码] : 管理员分页查看所有漂流瓶（默认第1页，每页20个）
    删除漂流瓶 [编号] : 管理员删除指定编号的漂流瓶
    """,
    config=Config,
)

plugin_config = get_plugin_config(Config)
superusers = get_driver().config.superusers

# 确保目录存在
plugin_config.drift_bottle_data_dir.mkdir(parents=True, exist_ok=True)
plugin_config.drift_bottle_image_dir.mkdir(parents=True, exist_ok=True)

def load_bottles() -> List[Dict[str, Any]]:
    if not plugin_config.drift_bottle_json_path.exists():
        return []
    try:
        with open(plugin_config.drift_bottle_json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载漂流瓶数据失败: {e}")
        return []

def save_bottles(bottles: List[Dict[str, Any]]):
    try:
        with open(plugin_config.drift_bottle_json_path, "w", encoding="utf-8") as f:
            json.dump(bottles, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存漂流瓶数据失败: {e}")

async def download_image(url: str, filename: str) -> Optional[Path]:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            if resp.status_code == 200:
                path = plugin_config.drift_bottle_image_dir / filename
                path.write_bytes(resp.content)
                return path
    except Exception as e:
        logger.error(f"下载图片失败: {e}")
    return None

throw_matcher = on_command("扔漂流瓶", priority=5, block=True)
@throw_matcher.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    # 获取完整消息内容
    msg = event.get_message()
    
    # 提取内容：移除命令前缀和命令词
    # NoneBot 会在 state 中存储命令信息，但这里我们简单处理
    content_msg = Message()
    first_text = True
    for seg in msg:
        if seg.type == "text":
            text = seg.data["text"]
            if first_text:
                # 移除可能的命令词（简单处理：查找第一个出现的“扔漂流瓶”并移除它及其之前的内容）
                if "扔漂流瓶" in text:
                    text = text.split("扔漂流瓶", 1)[1].strip()
                first_text = False
            if text.strip():
                content_msg.append(MessageSegment.text(text.strip()))
        else:
            content_msg.append(seg)

    if not content_msg:
        await throw_matcher.finish("漂流瓶里总得放点什么吧？(支持文字、图片、表情包)")

    bottles = load_bottles()
    bottle_id = len(bottles) + 1
    
    # 处理消息中的图片
    final_content = []
    for seg in content_msg:
        if seg.type == "image":
            url = seg.data.get("url")
            if url:
                file_ext = ".jpg"
                filename = f"bottle_{bottle_id}_{int(time.time())}{file_ext}"
                path = await download_image(url, filename)
                if path:
                    # 确保存储的是相对于当前运行目录的路径，或者绝对路径
                    try:
                        rel_path = path.absolute().relative_to(Path.cwd())
                        final_content.append({"type": "image", "data": {"file": str(rel_path)}})
                    except ValueError:
                        # 如果不在子目录中，存绝对路径
                        final_content.append({"type": "image", "data": {"file": str(path.absolute())}})
                else:
                    await throw_matcher.send("图片保存失败，该图片可能无法被捡到。")
        elif seg.type == "text":
            final_content.append({"type": "text", "data": {"text": seg.data["text"]}})
        elif seg.type == "face":
            final_content.append({"type": "face", "data": {"id": seg.data["id"]}})

    if not final_content:
        await throw_matcher.finish("漂流瓶内容处理失败，请稍后再试。")

    # 来源信息处理
    group_id = getattr(event, "group_id", "私聊")
    nickname = getattr(event.sender, "card", "") or getattr(event.sender, "nickname", "") or str(event.user_id)
    
    # 构造元数据
    new_bottle = {
        "id": bottle_id,
        "content": final_content,
        "user_id": event.user_id,
        "group_id": group_id,
        "nickname": nickname,
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    }
    
    bottles.append(new_bottle)
    save_bottles(bottles)
    
    await throw_matcher.finish(f"📦 漂流瓶已扔向大海！(编号: {bottle_id})")

pick_matcher = on_command("捡漂流瓶", priority=5, block=True)
@pick_matcher.handle()
async def _(bot: Bot, event: MessageEvent):
    bottles = load_bottles()
    if not bottles:
        await pick_matcher.finish("大海上一片寂静，目前还没有人扔漂流瓶内容。")
    
    # 随机选择一个
    bottle = random.choice(bottles)
    
    # 构建消息
    result_msg = Message()
    result_msg.append(MessageSegment.text("🌊 你捡到了一个漂流瓶：\n\n"))
    
    for item in bottle["content"]:
        if item["type"] == "text":
            result_msg.append(MessageSegment.text(item["data"]["text"]))
        elif item["type"] == "image":
            # 兼容处理：file 可能已经是绝对路径，也可能是相对路径
            stored_path = Path(item["data"]["file"])
            if stored_path.is_absolute():
                img_path = stored_path
            else:
                img_path = Path.cwd() / stored_path
            
            if img_path.exists():
                result_msg.append(MessageSegment.image(f"file:///{img_path.absolute()}"))
            else:
                result_msg.append(MessageSegment.text("[图片丢失]"))
        elif item["type"] == "face":
            result_msg.append(MessageSegment.face(item["data"]["id"]))

    # 添加来源信息
    info = f"\n\n--- 漂流瓶信息 ---\n"
    info += f"🔢 编号: {bottle['id']}\n"
    info += f"📍 来自群: {bottle['group_id']}\n"
    info += f"👤 扔瓶人: {bottle['nickname']} ({bottle['user_id']})\n"
    info += f"⏰ 时间: {bottle['time']}"
    
    result_msg.append(MessageSegment.text(info))
    
    await pick_matcher.finish(result_msg)

# --- 管理员功能 ---

list_all_matcher = on_command("查看漂流瓶", aliases={"查看所有漂流瓶", "漂流瓶列表"}, permission=SUPERUSER, priority=5, block=True)
@list_all_matcher.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    bottles = load_bottles()
    if not bottles:
        await list_all_matcher.finish("大海上一片寂静，目前还没有漂流瓶。")
    
    # 分页处理
    page_size = 20
    total_bottles = len(bottles)
    total_pages = (total_bottles + page_size - 1) // page_size
    
    page = 1
    page_str = args.extract_plain_text().strip()
    if page_str and page_str.isdigit():
        page = int(page_str)
    
    if page < 1:
        page = 1
    
    if page > total_pages:
        await list_all_matcher.finish(f"目前只有 {total_pages} 页漂流瓶，请输入正确的页码。")

    start_idx = (page - 1) * page_size
    end_idx = min(start_idx + page_size, total_bottles)
    current_page_bottles = bottles[start_idx:end_idx]

    messages = []
    # 顶部提示信息
    header_info = f"🌊 大海的记忆 (第 {page}/{total_pages} 页)\n"
    header_info += f"当前展示第 {start_idx + 1} 到 {end_idx} 个漂流瓶，共 {total_bottles} 个。"
    messages.append({
        "type": "node",
        "data": {
            "name": "大海的记忆",
            "uin": bot.self_id,
            "content": Message(header_info)
        }
    })

    # 构造合并转发节点
    for bottle in current_page_bottles:
        content = Message()
        for item in bottle["content"]:
            if item["type"] == "text":
                content.append(MessageSegment.text(item["data"]["text"]))
            elif item["type"] == "image":
                stored_path = Path(item["data"]["file"])
                img_path = stored_path if stored_path.is_absolute() else Path.cwd() / stored_path
                if img_path.exists():
                    content.append(MessageSegment.image(f"file:///{img_path.absolute()}"))
                else:
                    content.append(MessageSegment.text("[图片丢失]"))
            elif item["type"] == "face":
                content.append(MessageSegment.face(item["data"]["id"]))
        
        info = f"\n\n--- 漂流瓶详情 ---\n"
        info += f"🔢 编号: {bottle['id']}\n"
        info += f"📍 来自群: {bottle['group_id']}\n"
        info += f"👤 扔瓶人: {bottle['nickname']} ({bottle['user_id']})\n"
        info += f"⏰ 时间: {bottle['time']}"
        content.append(MessageSegment.text(info))

        messages.append({
            "type": "node",
            "data": {
                "name": "大海的记忆",
                "uin": bot.self_id,
                "content": content
            }
        })
    
    # 底部翻页提示
    if total_pages > 1:
        footer_info = f"\n💡 提示：输入“查看漂流瓶 [页码]”可以翻页查看。\n当前第 {page}/{total_pages} 页。"
        messages.append({
            "type": "node",
            "data": {
                "name": "大海的记忆",
                "uin": bot.self_id,
                "content": Message(footer_info)
            }
        })
    
    try:
        if isinstance(event, GroupMessageEvent):
            await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=messages)
        else:
            await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=messages)
    except Exception as e:
        logger.error(f"发送漂流瓶列表失败: {e}")
        await list_all_matcher.finish(f"发送失败，可能是由于消息过长或 API 限制: {e}\n建议尝试更小的分页或联系开发者。")

delete_matcher = on_command("删除漂流瓶", permission=SUPERUSER, priority=5, block=True)
@delete_matcher.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    bottle_id_str = args.extract_plain_text().strip()
    if not bottle_id_str or not bottle_id_str.isdigit():
        await delete_matcher.finish("请输入要删除的漂流瓶编号，例如：/删除漂流瓶 1")
    
    bottle_id = int(bottle_id_str)
    bottles = load_bottles()
    
    new_bottles = [b for b in bottles if b["id"] != bottle_id]
    
    if len(new_bottles) == len(bottles):
        await delete_matcher.finish(f"未找到编号为 {bottle_id} 的漂流瓶。")
    
    # 如果删除了中间的，为了保持 ID 逻辑，这里不重新排序 ID，只移除数据
    save_bottles(new_bottles)
    await delete_matcher.finish(f"✅ 已成功删除编号为 {bottle_id} 的漂流瓶。")
