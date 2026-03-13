import httpx
import random
import re
from datetime import datetime
from typing import List, Optional, Dict, Any, Union

from nonebot import on_command, get_plugin_config, logger
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
    黄金价格: 获取今日实时金价信息
    健康分析 <身高cm> <体重kg> <年龄> <性别(男/女)>: 获取身体健康深度分析
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
fabing_matcher = on_command("发病", priority=5, block=True)

@fabing_matcher.handle()
async def _(bot: Bot, event: MessageEvent):
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

# --- 黄金价格 ---
gold_matcher = on_command("黄金价格", aliases={"金价", "今日金价"}, priority=5, block=True)

@gold_matcher.handle()
async def _():
    data = await get_api_data("/v2/gold-price")
    if not data or data.get("code") != 200:
        await gold_matcher.finish("金子太重了，我现在搬不动数据...")
    
    gold_data = data["data"]
    metals = gold_data.get("metals", [])
    stores = gold_data.get("stores", [])
    
    msg = f"💰 今日黄金价格 ({gold_data['date']})\n"
    
    # 提取关键金属价格
    for metal in metals:
        if metal["name"] in ["今日金价", "黄金价格"]:
            msg += f"\n📊 {metal['name']}: {metal['today_price']} {metal['unit']}"
            msg += f"\n📈 最高: {metal['high_price']} | 📉 最低: {metal['low_price']}"
            msg += f"\n🕒 更新时间: {metal['updated']}\n"
            break
            
    # 提取主要品牌金价 (取前 5 个)
    if stores:
        msg += "\n💍 品牌金价参考："
        for store in stores[:6]:
            msg += f"\n🔹 {store['brand']}: {store['price']} {store['unit']}"
            
    await gold_matcher.finish(msg)

# --- 身体健康分析 ---
health_matcher = on_command("健康分析", aliases={"身体健康分析", "健康指数"}, priority=5, block=True)

@health_matcher.handle()
async def _(args: Message = CommandArg()):
    # 解析参数: 身高 体重 年龄 性别
    params = args.extract_plain_text().split()
    if len(params) < 4:
        await health_matcher.finish("请提供完整参数哦：健康分析 <身高cm> <体重kg> <年龄> <性别(男/女)>\n示例：健康分析 176 60 24 男")
    
    height, weight, age, gender_zh = params[0], params[1], params[2], params[3]
    gender = "male" if "男" in gender_zh else "female"
    
    # 构建请求
    endpoint = f"/v2/health?height={height}&weight={weight}&age={age}&gender={gender}"
    
    # 这里直接使用 get_api_data 可能不够，因为它是 query 参数
    # get_api_data 内部是拼接 endpoint 的，所以直接传带 query 的 endpoint 也可以
    data = await get_api_data(endpoint)
    
    if not data or data.get("code") != 200:
        await health_matcher.finish("医生现在不在办公室，请稍后再试吧...")
        
    health_data = data["data"]
    
    # 构建回复
    msg = f"🏥 身体健康深度分析报告\n"
    msg += f"\n👤 基本资料: {height}cm | {weight}kg | {age}岁 | {gender_zh}"
    msg += f"\n----------------------"
    
    # 提取关键指标 (假设接口返回结构包含这些)
    msg += f"\n⚖️ BMI指数: {health_data.get('bmi', '未知')}"
    msg += f"\n💡 健康状态: {health_data.get('status', '未知')}"
    msg += f"\n🌟 理想体重: {health_data.get('ideal_weight', '未知')}"
    msg += f"\n🔥 基础代谢: {health_data.get('bmr', '未知')}"
    msg += f"\n📏 建议三围: {health_data.get('suggested_measurements', '未知')}"
    
    # 提取健康建议
    advice = health_data.get("advice")
    if advice:
        msg += f"\n\n📝 健康建议：\n{advice}"
        
    await health_matcher.finish(msg)
