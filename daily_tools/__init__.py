import httpx
import random
import re
from datetime import datetime
from typing import List, Optional, Dict, Any, Union

from nonebot import on_command, on_message, get_plugin_config, logger
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment, MessageEvent, GroupMessageEvent
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="日常工具箱",
    description="Epic免费游戏、发病、KFC、冷笑话等日常小工具",
    usage="""
    Epic: 查看本周Epic免费游戏
    发病 [@某人]: 获取发病文案 (支持@获取对方姓名，不艾特则为自己)
    疯狂星期四: 获取KFC文案
    冷笑话: 讲个冷笑话
    """,
    config=Config,
)

plugin_config = get_plugin_config(Config)

async def get_api_data(endpoint: str) -> Optional[Dict[str, Any]]:
    """尝试从主备 API 获取数据"""
    urls = [
        f"{plugin_config.daily_tools_api_primary}{endpoint}",
        f"{plugin_config.daily_tools_api_backup}{endpoint}"
    ]
    
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json()
            except Exception as e:
                logger.warning(f"从 {url} 获取数据失败: {e}")
                continue
    return None

async def get_epic_free_games() -> Union[str, Message]:
    """获取 Epic 免费游戏信息"""
    data = await get_api_data("/v2/epic")
    if not data or data.get("code") != 200:
        return "获取 Epic 数据失败，请稍后再试。"
    
    games = data.get("data", [])
    if not games:
        return "目前 Epic 似乎没有正在进行的免费游戏活动。"
    
    msg = Message("🎮 Epic 免费游戏提醒：\n")
    
    # 分类：正在免费和即将免费
    current_free = [g for g in games if g.get("is_free_now")]
    upcoming_free = [g for g in games if not g.get("is_free_now")]
    
    if current_free:
        msg += "\n🔥 [正在免费]"
        for game in current_free:
            title = game.get("title", "未知游戏")
            cover = game.get("cover", "").replace("`", "").strip()
            end_time = game.get("free_end", "未知")
            
            msg += f"\n📖 {title}"
            msg += f"\n⏰ 截止时间: {end_time}"
            if cover:
                msg += MessageSegment.image(cover)
            msg += "\n"

    if upcoming_free:
        msg += "\n⏳ [即将免费]"
        for game in upcoming_free:
            title = game.get("title", "未知游戏")
            cover = game.get("cover", "").replace("`", "").strip()
            start_time = game.get("free_start", "未知")
            
            msg += f"\n📖 {title}"
            msg += f"\n⏰ 开始时间: {start_time}"
            if cover:
                msg += MessageSegment.image(cover)
            msg += "\n"
            
    return msg

# --- Epic 免费游戏 ---
epic_matcher = on_command("epic", aliases={"epic免费游戏", "喜加一"}, priority=5, block=True)

@epic_matcher.handle()
async def _():
    msg = await get_epic_free_games()
    await epic_matcher.finish(msg)

# --- 发病 ---
# 不再强制要求 to_me()
fabing_matcher = on_message(priority=5, block=False)

@fabing_matcher.handle()
async def _(bot: Bot, event: MessageEvent):
    content = event.get_plaintext().strip()
    # 只有当消息中包含“发病”且字数较少时触发（防止误触长难句）
    if "发病" not in content or len(content) > 15:
        return
    
    # 尝试获取被艾特的人的名字
    target_name = None
    if isinstance(event, GroupMessageEvent):
        # 遍历消息段寻找第一个艾特
        for seg in event.message:
            if seg.type == "at":
                at_id = seg.data.get("qq")
                if at_id and at_id != "all" and at_id != bot.self_id:
                    try:
                        member_info = await bot.get_group_member_info(group_id=event.group_id, user_id=int(at_id))
                        target_name = member_info.get("card") or member_info.get("nickname")
                        if target_name:
                            break
                    except Exception:
                        continue
    
    # 如果没艾特或者艾特的是机器人自己，则使用发送者的名字
    if not target_name:
        target_name = event.sender.card or event.sender.nickname or str(event.user_id)
    
    data = await get_api_data("/v2/fabing")
    if not data or data.get("code") != 200:
        await fabing_matcher.finish("哎呀，我现在发不出病来...")
    
    saying = data["data"]["saying"]
    # 替换关键词为目标姓名
    saying = saying.replace("主人", target_name).replace("你", target_name)
    
    await fabing_matcher.finish(saying)

# --- 疯狂星期四 ---
kfc_matcher = on_command("疯狂星期四", aliases={"kfc", "肯德基"}, priority=5, block=True)

@kfc_matcher.handle()
async def _():
    data = await get_api_data("/v2/kfc")
    if not data or data.get("code") != 200:
        await kfc_matcher.finish("V我50，我就告诉你文案（接口挂了）")
    
    await kfc_matcher.finish(data["data"]["kfc"])

# --- 冷笑话 ---
joke_matcher = on_command("冷笑话", aliases={"讲个笑话"}, priority=5, block=True)

@joke_matcher.handle()
async def _():
    data = await get_api_data("/v2/dad-joke")
    if not data or data.get("code") != 200:
        await joke_matcher.finish("这个笑话太冷了，冻得我打不开接口...")
    
    await joke_matcher.finish(data["data"]["content"])
