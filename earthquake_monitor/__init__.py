from nonebot import on_command, require, get_bot, logger
require("nonebot_plugin_apscheduler")
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, PrivateMessageEvent, Message, MessageSegment
from nonebot.permission import SUPERUSER, Permission
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.plugin import PluginMetadata
from nonebot_plugin_apscheduler import scheduler
import asyncio

from .config import config, whitelist_manager, typhoon_whitelist
from .data_source import earthquake_source, EarthquakeInfo
from .typhoon_source import typhoon_source, TyphoonInfo

__plugin_meta__ = PluginMetadata(
    name="天灾监测",
    description="实时监测地震与台风并推送至指定群聊",
    usage="""
/地震推送 开启/关闭/状态 - 地震监测管理
/台风推送 开启/关闭/状态 - 台风监测管理
/历史地震 - 获取最近五条地震信息
/当前台风 - 查看最新的台风动态
    """.strip(),
    extra={
        "author": "Shiro",
        "version": "0.2.0"
    }
)

from nonebot.params import CommandArg

# 注册地震推送命令
eq_push = on_command("地震推送", priority=5, block=True, permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER)

@eq_push.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    cmd = args.extract_plain_text().strip()
    group_id = str(event.group_id)

    if cmd == "开启":
        if whitelist_manager.add(group_id):
            await eq_push.finish("✅ 已开启本群地震推送。")
        else:
            await eq_push.finish("ℹ️ 本群已处于开启状态。")
    elif cmd == "关闭":
        if whitelist_manager.remove(group_id):
            await eq_push.finish("❌ 已关闭本群地震推送。")
        else:
            await eq_push.finish("ℹ️ 本群未开启推送。")
    elif cmd == "状态":
        status = "已开启" if whitelist_manager.is_whitelisted(group_id) else "已关闭"
        await eq_push.finish(f"📊 当前地震推送状态：{status}")
    else:
        await eq_push.finish("请输入：/地震推送 开启/关闭/状态")

# 注册台风推送命令
tf_push = on_command("台风推送", priority=5, block=True, permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER)

@tf_push.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    cmd = args.extract_plain_text().strip()
    group_id = str(event.group_id)

    if cmd == "开启":
        if typhoon_whitelist.add(group_id):
            await tf_push.finish("🌀 已开启本群台风推送。")
        else:
            await tf_push.finish("ℹ️ 本群已处于开启状态。")
    elif cmd == "关闭":
        if typhoon_whitelist.remove(group_id):
            await tf_push.finish("❌ 已关闭本群台风推送。")
        else:
            await tf_push.finish("ℹ️ 本群未开启推送。")
    elif cmd == "状态":
        status = "已开启" if typhoon_whitelist.is_whitelisted(group_id) else "已关闭"
        await tf_push.finish(f"📊 当前台风推送状态：{status}")
    else:
        await tf_push.finish("请输入：/台风推送 开启/关闭/状态")

# 历史地震命令
eq_history = on_command("历史地震", aliases={"地震历史", "最近地震"}, priority=5, block=True)

@eq_history.handle()
async def _(bot: Bot, event: MessageEvent):
    history_eqs = await earthquake_source.get_history(5)
    if not history_eqs:
        await eq_history.finish("⚠️ 暂未获取到地震历史信息。")

    nodes = []
    for eq in history_eqs:
        msg = format_eq_message(eq)
        nodes.append(
            MessageSegment.node_custom(
                user_id=int(bot.self_id),
                nickname="地震速报",
                content=msg
            )
        )
    
    try:
        if isinstance(event, GroupMessageEvent):
            await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=nodes)
        else:
            await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=nodes)
    except Exception as e:
        logger.error(f"发送历史地震合并转发消息失败: {e}")
        await eq_history.finish("❌ 发送历史地震信息失败，请稍后再试。")

# 当前台风命令
tf_current = on_command("当前台风", aliases={"台风动态", "最新台风"}, priority=5, block=True)

@tf_current.handle()
async def _(bot: Bot, event: MessageEvent):
    latest_tf = await typhoon_source.fetch_latest()
    if not latest_tf:
        await tf_current.finish("⚠️ 暂未获取到最新的台风信息。")
    
    msg = format_tf_message(latest_tf)
    await tf_current.finish(msg)

# 定时任务：每分钟检查一次
@scheduler.scheduled_job("interval", seconds=config.earthquake_monitor_interval, id="earthquake_monitor_job")
async def earthquake_monitor_job():
    new_eqs = await earthquake_source.get_new_earthquakes()
    if not new_eqs:
        return

    bot = None
    try:
        bot = get_bot()
    except Exception:
        return

    if not bot:
        return

    whitelisted_groups = whitelist_manager.get_all()
    if not whitelisted_groups:
        return

    for eq in new_eqs:
        msg = format_eq_message(eq)
        for group_id in whitelisted_groups:
            try:
                await bot.send_group_msg(group_id=int(group_id), message=msg)
                await asyncio.sleep(0.5)  # 避免发送太快
            except Exception as e:
                logger.error(f"推送地震信息至群 {group_id} 失败: {e}")

# 定时任务：每10分钟检查一次台风
@scheduler.scheduled_job("interval", seconds=config.typhoon_monitor_interval, id="typhoon_monitor_job")
async def typhoon_monitor_job():
    updates = await typhoon_source.get_new_updates()
    if not updates:
        return

    bot = None
    try:
        bot = get_bot()
    except Exception:
        return

    if not bot:
        return

    whitelisted_groups = typhoon_whitelist.get_all()
    if not whitelisted_groups:
        return

    for tf in updates:
        msg = format_tf_message(tf)
        for group_id in whitelisted_groups:
            try:
                await bot.send_group_msg(group_id=int(group_id), message=msg)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"推送台风信息至群 {group_id} 失败: {e}")

def format_eq_message(eq: EarthquakeInfo) -> str:
    """格式化地震信息"""
    msg = "📢 【地震速报】\n"
    msg += "━━━━━━━━━━━━━━━\n"
    msg += f"📍 地点：{eq.location}\n"
    msg += f"📉 震级：{eq.magnitude} 级\n"
    msg += f"🕒 时间：{eq.time}\n"
    msg += f"📏 深度：{eq.depth} km\n"
    msg += f"🌐 坐标：{eq.latitude}, {eq.longitude}\n"
    msg += "━━━━━━━━━━━━━━━\n"
    msg += "数据来源：中国地震台网"
    return msg

def format_tf_message(tf: TyphoonInfo) -> str:
    """格式化台风信息"""
    msg = "🌀 【台风速报】\n"
    msg += "━━━━━━━━━━━━━━━\n"
    msg += f"🏷️ 名称：{tf.name} ({tf.en_name})\n"
    msg += f"🆔 编号：{tf.id}\n"
    msg += f"🕒 报时：{tf.time}\n"
    msg += f"💪 强度：{tf.level}\n"
    msg += f"🌬️ 风速：{tf.wind_speed}\n"
    msg += f"🌡️ 气压：{tf.pressure}\n"
    msg += f"📍 位置：{tf.location}\n"
    msg += f"🗺️ 参考：{tf.ref_pos}\n"
    msg += "━━━━━━━━━━━━━━━\n"
    msg += "数据来源：中央气象台台风网"
    return msg
