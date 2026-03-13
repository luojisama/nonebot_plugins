import asyncio
from typing import Iterable, List

from nonebot import get_bots, logger, on_command, require
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .config import (
    config,
    tsunami_whitelist,
    typhoon_whitelist,
    weather_whitelist,
    whitelist_manager,
)
from .data_source import EarthquakeInfo, earthquake_source
from .tsunami_source import TsunamiInfo, tsunami_source
from .typhoon_source import TyphoonInfo, typhoon_source
from .weather_source import WeatherWarningInfo, weather_warning_source

__plugin_meta__ = PluginMetadata(
    name="灾害监测",
    description="地震/台风/天气预警/海啸多灾种监测推送",
    usage=(
        "/地震推送 开启|关闭|状态\n"
        "/地震源状态\n"
        "/台风推送 开启|关闭|状态\n"
        "/天气预警推送 开启|关闭|状态\n"
        "/海啸推送 开启|关闭|状态\n"
        "/历史地震\n/当前台风\n/当前天气预警\n/当前海啸"
    ),
    extra={"author": "Shiro", "version": "0.4.0"},
)

MANAGE_PERMISSION = SUPERUSER | GROUP_ADMIN | GROUP_OWNER


def _first_bot() -> Bot | None:
    bots = list(get_bots().values())
    return bots[0] if bots else None


async def _broadcast(groups: Iterable[str], message: str) -> None:
    bot = _first_bot()
    if not bot:
        return
    for gid in groups:
        try:
            await bot.send_group_msg(group_id=int(gid), message=message)
            await asyncio.sleep(0.4)
        except Exception as e:
            logger.error(f"[earthquake_monitor] send group {gid} failed: {e}")


def _parse_toggle_arg(args: Message) -> str:
    return args.extract_plain_text().strip()


def _toggle_text(feature: str, enabled: bool) -> str:
    return f"当前{feature}推送状态：{'已开启' if enabled else '已关闭'}"


# 地震推送开关


eq_push = on_command("地震推送", priority=5, block=True, permission=MANAGE_PERMISSION)


@eq_push.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    cmd = _parse_toggle_arg(args)
    gid = str(event.group_id)
    if cmd == "开启":
        await eq_push.finish("已开启本群地震推送" if whitelist_manager.add(gid) else "本群已开启地震推送")
    elif cmd == "关闭":
        await eq_push.finish("已关闭本群地震推送" if whitelist_manager.remove(gid) else "本群未开启地震推送")
    elif cmd == "状态":
        await eq_push.finish(_toggle_text("地震", whitelist_manager.is_enabled(gid)))
    await eq_push.finish("用法：/地震推送 开启|关闭|状态")


# 台风推送开关


tf_push = on_command("台风推送", priority=5, block=True, permission=MANAGE_PERMISSION)


@tf_push.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    cmd = _parse_toggle_arg(args)
    gid = str(event.group_id)
    if cmd == "开启":
        await tf_push.finish("已开启本群台风推送" if typhoon_whitelist.add(gid) else "本群已开启台风推送")
    elif cmd == "关闭":
        await tf_push.finish("已关闭本群台风推送" if typhoon_whitelist.remove(gid) else "本群未开启台风推送")
    elif cmd == "状态":
        await tf_push.finish(_toggle_text("台风", typhoon_whitelist.is_enabled(gid)))
    await tf_push.finish("用法：/台风推送 开启|关闭|状态")


# 天气预警推送开关


weather_push = on_command("天气预警推送", priority=5, block=True, permission=MANAGE_PERMISSION)


@weather_push.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    cmd = _parse_toggle_arg(args)
    gid = str(event.group_id)
    if cmd == "开启":
        await weather_push.finish("已开启本群天气预警推送" if weather_whitelist.add(gid) else "本群已开启天气预警推送")
    elif cmd == "关闭":
        await weather_push.finish("已关闭本群天气预警推送" if weather_whitelist.remove(gid) else "本群未开启天气预警推送")
    elif cmd == "状态":
        await weather_push.finish(_toggle_text("天气预警", weather_whitelist.is_enabled(gid)))
    await weather_push.finish("用法：/天气预警推送 开启|关闭|状态")


# 海啸推送开关


tsunami_push = on_command("海啸推送", priority=5, block=True, permission=MANAGE_PERMISSION)


@tsunami_push.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    cmd = _parse_toggle_arg(args)
    gid = str(event.group_id)
    if cmd == "开启":
        await tsunami_push.finish("已开启本群海啸推送" if tsunami_whitelist.add(gid) else "本群已开启海啸推送")
    elif cmd == "关闭":
        await tsunami_push.finish("已关闭本群海啸推送" if tsunami_whitelist.remove(gid) else "本群未开启海啸推送")
    elif cmd == "状态":
        await tsunami_push.finish(_toggle_text("海啸", tsunami_whitelist.is_enabled(gid)))
    await tsunami_push.finish("用法：/海啸推送 开启|关闭|状态")


# 查询命令


eq_history = on_command("历史地震", aliases={"地震历史", "最近地震"}, priority=5, block=True)


@eq_history.handle()
async def _(event: MessageEvent):
    bot = _first_bot()
    if not bot:
        await eq_history.finish("机器人未连接")
    items = await earthquake_source.get_history(5)
    if not items:
        await eq_history.finish("暂无地震历史信息")

    nodes = [
        MessageSegment.node_custom(user_id=int(bot.self_id), nickname="地震速报", content=format_eq_message(x))
        for x in items
    ]
    try:
        if isinstance(event, GroupMessageEvent):
            await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=nodes)
        else:
            await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=nodes)
    except Exception as e:
        logger.error(f"[earthquake_monitor] send earthquake history failed: {e}")
        await eq_history.finish("发送历史地震失败，请稍后重试")


eq_sources = on_command("地震源状态", aliases={"地震数据源", "地震源"}, priority=5, block=True)


@eq_sources.handle()
async def _():
    enabled = earthquake_source.enabled_sources()
    if not enabled:
        await eq_sources.finish("当前未启用任何地震数据源")
    lines = ["当前启用地震源："]
    for src in enabled:
        lines.append(f"- {earthquake_source.source_label(src)} ({src})")
    await eq_sources.finish("\n".join(lines))


tf_current = on_command("当前台风", aliases={"台风动态", "最新台风"}, priority=5, block=True)


@tf_current.handle()
async def _():
    item = await typhoon_source.fetch_latest()
    if not item:
        await tf_current.finish("暂无最新台风信息")
    await tf_current.finish(format_tf_message(item))


weather_current = on_command("当前天气预警", aliases={"天气预警", "最新天气预警"}, priority=5, block=True)


@weather_current.handle()
async def _():
    items = await weather_warning_source.get_current()
    if not items:
        await weather_current.finish("暂无天气预警信息")
    await weather_current.finish(format_weather_message(items[0]))


tsunami_current = on_command("当前海啸", aliases={"海啸动态", "最新海啸"}, priority=5, block=True)


@tsunami_current.handle()
async def _():
    item = await tsunami_source.get_current()
    if not item:
        await tsunami_current.finish("暂无海啸预警信息")
    await tsunami_current.finish(format_tsunami_message(item))


# 定时监测任务


@scheduler.scheduled_job("interval", seconds=config.earthquake_monitor_interval, id="earthquake_monitor_job")
async def earthquake_monitor_job():
    new_items = await earthquake_source.get_new_earthquakes()
    if not new_items:
        return
    groups = whitelist_manager.get_all()
    if not groups:
        return
    for item in new_items:
        await _broadcast(groups, format_eq_message(item))


@scheduler.scheduled_job("interval", seconds=config.typhoon_monitor_interval, id="typhoon_monitor_job")
async def typhoon_monitor_job():
    new_items = await typhoon_source.get_new_updates()
    if not new_items:
        return
    groups = typhoon_whitelist.get_all()
    if not groups:
        return
    for item in new_items:
        await _broadcast(groups, format_tf_message(item))


@scheduler.scheduled_job("interval", seconds=config.weather_monitor_interval, id="weather_monitor_job")
async def weather_monitor_job():
    new_items = await weather_warning_source.get_new_warnings()
    if not new_items:
        return
    groups = weather_whitelist.get_all()
    if not groups:
        return
    for item in new_items:
        await _broadcast(groups, format_weather_message(item))


@scheduler.scheduled_job("interval", seconds=config.tsunami_monitor_interval, id="tsunami_monitor_job")
async def tsunami_monitor_job():
    new_items = await tsunami_source.get_new_updates()
    if not new_items:
        return
    groups = tsunami_whitelist.get_all()
    if not groups:
        return
    for item in new_items:
        await _broadcast(groups, format_tsunami_message(item))


# 格式化


def format_eq_message(eq: EarthquakeInfo) -> str:
    return (
        "[地震速报]\n"
        f"地点：{eq.location}\n"
        f"震级：M{eq.magnitude}\n"
        f"时间：{eq.time}\n"
        f"深度：{eq.depth} km\n"
        f"坐标：{eq.latitude}, {eq.longitude}\n"
        f"数据来源：{earthquake_source.source_label(eq.source)}"
    )


def format_tf_message(tf: TyphoonInfo) -> str:
    return (
        "[台风速报]\n"
        f"名称：{tf.name} ({tf.en_name})\n"
        f"编号：{tf.id}\n"
        f"报时：{tf.time}\n"
        f"强度：{tf.level}\n"
        f"风速：{tf.wind_speed}\n"
        f"气压：{tf.pressure}\n"
        f"位置：{tf.location}\n"
        f"参考：{tf.ref_pos}\n"
        "数据来源：中央气象台"
    )


def format_weather_message(item: WeatherWarningInfo) -> str:
    content = item.content[:120] + ("..." if len(item.content) > 120 else "")
    return (
        "[天气预警]\n"
        f"标题：{item.title}\n"
        f"级别：{item.level}\n"
        f"发布：{item.publish_time}\n"
        f"机构：{item.sender}\n"
        f"内容：{content}"
    )


def format_tsunami_message(item: TsunamiInfo) -> str:
    return (
        "[海啸预警]\n"
        f"时间：{item.time}\n"
        f"级别：{item.grade}\n"
        f"区域：{item.region}\n"
        f"来源：{item.title}"
    )
