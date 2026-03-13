import asyncio

from nonebot import get_driver, get_plugin_config, logger, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .cache import AsyncTTLCache
from .config import Config, StateStore
from .data_source import APIError, DownloadNotFound, NoGameFound, TouchGalAPI
from .formatter import format_downloads, format_search_item

__plugin_meta__ = PluginMetadata(
    name="TouchGal 查询",
    description="TouchGal Galgame 搜索与下载资源查询",
    usage=(
        "/查询gal <关键词>\n"
        "/下载gal <游戏ID>\n"
        "/gal nsfw 开启|关闭|状态 (仅超管)"
    ),
    config=Config,
)

plugin_config = get_plugin_config(Config)
state_store = StateStore(plugin_config.state_path)
api = TouchGalAPI(timeout=plugin_config.request_timeout)
game_cache = AsyncTTLCache(ttl_seconds=86400, max_size=1000)


async def _cleanup_loop() -> None:
    while True:
        try:
            await game_cache.cleanup()
        except Exception as e:
            logger.warning(f"[touchgal] cache cleanup failed: {e}")
        await asyncio.sleep(3600)


driver = get_driver()


@driver.on_startup
async def _startup() -> None:
    if not hasattr(driver, "_touchgal_cleanup_task"):
        setattr(driver, "_touchgal_cleanup_task", asyncio.create_task(_cleanup_loop()))


@driver.on_shutdown
async def _shutdown() -> None:
    task = getattr(driver, "_touchgal_cleanup_task", None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _nsfw_enabled() -> bool:
    return state_store.get_bool("enable_nsfw", plugin_config.enable_nsfw)


def _parse_args(args: Message) -> str:
    return args.extract_plain_text().strip()


async def _send_forward_or_fallback(
    bot: Bot, event: MessageEvent, blocks: list[Message], fallback: str
) -> None:
    nodes = [
        MessageSegment.node_custom(
            user_id=int(bot.self_id),
            nickname="TouchGal",
            content=block,
        )
        for block in blocks
    ]

    try:
        if isinstance(event, GroupMessageEvent):
            await bot.call_api(
                "send_group_forward_msg", group_id=event.group_id, messages=nodes
            )
            return
        if isinstance(event, PrivateMessageEvent):
            await bot.call_api(
                "send_private_forward_msg", user_id=event.user_id, messages=nodes
            )
            return
    except Exception as e:
        logger.warning(f"[touchgal] forward send failed: {e}")

    await bot.send(event=event, message=fallback)


search_cmd = on_command("查询gal", priority=plugin_config.touchgal_priority, block=True)
download_cmd = on_command("下载gal", priority=plugin_config.touchgal_priority, block=True)
nsfw_cmd = on_command(
    "gal nsfw", priority=plugin_config.touchgal_priority, block=True, permission=SUPERUSER
)


@search_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    keyword = _parse_args(args)
    if not keyword:
        await search_cmd.finish("用法: /查询gal <关键词>")

    await search_cmd.send(f"正在搜索: {keyword}")
    try:
        results = await api.search_game(keyword, plugin_config.search_limit, _nsfw_enabled())
    except NoGameFound as e:
        await search_cmd.finish(str(e))
    except APIError as e:
        logger.error(f"[touchgal] search failed: {e}")
        await search_cmd.finish("搜索失败，请稍后重试")

    blocks: list[Message] = []
    fallback_lines: list[str] = [f"找到 {len(results)} 个结果"]

    for idx, game in enumerate(results, 1):
        try:
            game_id = int(game.get("id"))
            await game_cache.add(game_id, game)
        except Exception:
            pass

        msg = Message(format_search_item(idx, game))
        cover = str(game.get("banner") or "").strip()
        if cover:
            img_bytes, fallback_url = await api.fetch_cover(cover)
            if img_bytes:
                msg.append(MessageSegment.image(img_bytes))
            elif fallback_url:
                msg.append(f"\n封面: {fallback_url}")

        blocks.append(msg)
        fallback_lines.append(format_search_item(idx, game))

    fallback_lines.append("使用 /下载gal <游戏ID> 获取下载地址")
    await _send_forward_or_fallback(bot, event, blocks, "\n\n".join(fallback_lines))


@download_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    game_id_raw = _parse_args(args)
    if not game_id_raw:
        await download_cmd.finish("用法: /下载gal <游戏ID>")
    if not game_id_raw.isdigit():
        await download_cmd.finish("游戏ID 必须是数字")

    game_id = int(game_id_raw)
    cached = await game_cache.get(game_id)

    await download_cmd.send(f"正在查询 ID={game_id} 的下载资源...")
    try:
        downloads = await api.get_downloads(game_id)
    except DownloadNotFound as e:
        await download_cmd.finish(str(e))
    except APIError as e:
        logger.error(f"[touchgal] download query failed: {e}")
        await download_cmd.finish("下载资源查询失败，请稍后重试")

    title = str(cached.get("name")) if cached else f"ID:{game_id}"
    text = (
        f"游戏: {title} (ID: {game_id})\n"
        f"找到 {len(downloads)} 个下载资源\n\n"
        f"{format_downloads(downloads)}"
    )

    msg = Message(text)
    banner = str((cached or {}).get("banner") or "").strip()
    if banner:
        img_bytes, fallback_url = await api.fetch_cover(banner)
        if img_bytes:
            msg = MessageSegment.image(img_bytes) + msg
        elif fallback_url:
            msg = Message(f"封面: {fallback_url}\n\n") + msg

    await bot.send(event=event, message=msg)


@nsfw_cmd.handle()
async def _(args: Message = CommandArg()) -> None:
    cmd = _parse_args(args)
    if cmd == "开启":
        state_store.set_bool("enable_nsfw", True)
        await nsfw_cmd.finish("已开启 NSFW 搜索")
    if cmd == "关闭":
        state_store.set_bool("enable_nsfw", False)
        await nsfw_cmd.finish("已关闭 NSFW 搜索")
    if cmd == "状态":
        await nsfw_cmd.finish(f"当前 NSFW 搜索: {'开启' if _nsfw_enabled() else '关闭'}")
    await nsfw_cmd.finish("用法: /gal nsfw 开启|关闭|状态")
