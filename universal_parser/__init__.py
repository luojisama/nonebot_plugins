import asyncio

from nonebot import logger, require
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

require("nonebot_plugin_alconna")
require("nonebot_plugin_uninfo")

from .utils import safe_unlink
from .config import Config, pconfig
from .matchers import clear_result_cache

__plugin_meta__ = PluginMetadata(
    name="Link Share Parser Alconna",
    description="Parse shared links from Kuaishou, Weibo, Xiaohongshu, XiaoHeiHe, YouTube, TikTok, Twitter, AcFun and NGA",
    usage=(
        "Send a supported link to parse it.\n"
        "Extra commands:\n"
        "  ym URL (download YouTube audio)"
    ),
    type="application",
    homepage="https://github.com/fllesser/nonebot-plugin-parser",
    config=Config,
    supported_adapters=inherit_supported_adapters("nonebot_plugin_alconna", "nonebot_plugin_uninfo"),
    extra={
        "author": "fllesser",
        "email": "fllessive@gmail.com",
        "homepage": "https://github.com/fllesser/nonebot-plugin-parser",
    },
)

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler


@scheduler.scheduled_job("cron", hour=1, minute=0, id="parser-clean-local-cache")
async def clean_plugin_cache():
    try:
        files = [f for f in pconfig.cache_dir.iterdir() if f.is_file()]
        if not files:
            logger.info("No cache files to clean")
            return

        tasks = [safe_unlink(file) for file in files]
        await asyncio.gather(*tasks)

        logger.success(f"Successfully cleaned {len(files)} cache files")
    except Exception:
        logger.exception("Error while cleaning cache files")

    clear_result_cache()
