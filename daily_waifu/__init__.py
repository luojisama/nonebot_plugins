from nonebot import on_command, require
from nonebot.adapters.onebot.v11 import MessageSegment, MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.adapters import Message
from nonebot.plugin import PluginMetadata
from .model import PluginConfig
from .data_source import WaifuDataSource
import os

__plugin_meta__ = PluginMetadata(
    name="今日老婆",
    description="每天随机抽取一位二次元老婆",
    usage="/今日老婆 [标签]\n/刷新老婆 [标签] (重新抽取)",
    config=PluginConfig,
)

# 初始化数据源
DATA_DIR = "data/daily_waifu"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
    
source = WaifuDataSource(os.path.join(DATA_DIR, "cache.json"))

daily_waifu = on_command("今日老婆", aliases={"today_waifu", "抽老婆"}, priority=5, block=True)

from nonebot.exception import FinishedException

@daily_waifu.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    user_id = event.get_user_id()
    
    # 1. 检查今日是否已抽取
    waifu = source.get_today_waifu(user_id)
    
    if waifu:
        msg = MessageSegment.text(f"你今天的老婆是：\n")
        
        # 尝试下载图片
        img_bytes = await source.download_image(waifu.image_url)
        if img_bytes:
            msg += MessageSegment.image(img_bytes)
        else:
            msg += MessageSegment.image(waifu.image_url)
            
        msg += MessageSegment.text(f"\n💕 {waifu.name}")
        msg += MessageSegment.text(f"\n📺 出自：{waifu.source}")
        if waifu.extra:
            msg += MessageSegment.text(f"\n{waifu.extra}")
            
        await daily_waifu.finish(msg)
        return

    # 2. 抽取新的
    await daily_waifu.send("正在为你寻找命中注定的老婆... (请稍候)")
    
    # 提取参数 (标签) - 暂时未完全实现标签筛选，但预留接口
    tag = args.extract_plain_text().strip()
    
    try:
        waifu = await source.fetch_waifu(tag)
        if waifu:
            # 保存记录
            source.save_today_waifu(user_id, waifu)
            
            msg = MessageSegment.text(f"✨ 命运的邂逅！你今天的老婆是：\n")
            
            # 尝试下载图片
            img_bytes = await source.download_image(waifu.image_url)
            if img_bytes:
                msg += MessageSegment.image(img_bytes)
            else:
                msg += MessageSegment.image(waifu.image_url)
                
            msg += MessageSegment.text(f"\n💕 {waifu.name}")
            msg += MessageSegment.text(f"\n📺 出自：{waifu.source}")
            if waifu.extra:
                msg += MessageSegment.text(f"\n{waifu.extra}")
            
            await daily_waifu.finish(msg)
        else:
            await daily_waifu.finish("今天似乎没有老婆愿意跟你回家呢... (API 请求失败，请稍后再试)")
    except FinishedException:
        raise
    except Exception as e:
        await daily_waifu.finish(f"发生未知错误: {e}")

refresh_waifu = on_command("刷新老婆", aliases={"refresh_waifu", "换个老婆"}, priority=5, block=True)

@refresh_waifu.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    user_id = event.get_user_id()
    await refresh_waifu.send("正在为你重新寻找老婆... (请稍候)")
    
    tag = args.extract_plain_text().strip()
    
    try:
        waifu = await source.fetch_waifu(tag)
        if waifu:
            # 保存记录 (覆盖旧的)
            source.save_today_waifu(user_id, waifu)
            
            msg = MessageSegment.text(f"✨ 新的邂逅！你今天的老婆变成了：\n")
            
            # 尝试下载图片
            img_bytes = await source.download_image(waifu.image_url)
            if img_bytes:
                msg += MessageSegment.image(img_bytes)
            else:
                msg += MessageSegment.image(waifu.image_url)
                
            msg += MessageSegment.text(f"\n💕 {waifu.name}")
            msg += MessageSegment.text(f"\n📺 出自：{waifu.source}")
            if waifu.extra:
                msg += MessageSegment.text(f"\n{waifu.extra}")
            
            await refresh_waifu.finish(msg)
        else:
            await refresh_waifu.finish("刷新失败，老婆不肯走... (API 请求失败)")
    except FinishedException:
        raise
    except Exception as e:
        await refresh_waifu.finish(f"发生未知错误: {e}")
