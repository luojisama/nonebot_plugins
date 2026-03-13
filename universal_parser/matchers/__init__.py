from typing import TypeVar

from nonebot import logger, get_driver, on_command
from nonebot.params import CommandArg
from nonebot.adapters import Message

from .rule import Searched, SearchResult, on_keyword_regex
from ..utils import LimitedSizeDict
from ..config import pconfig
from ..helper import UniHelper, UniMessage
from ..exception import ParseException
from ..parsers import BaseParser, ParseResult
from ..renders import get_renderer


def _get_enabled_parser_classes() -> list[type[BaseParser]]:
    disabled_platforms = set(pconfig.disabled_platforms)
    return [_cls for _cls in BaseParser.get_all_subclass() if _cls.platform.name not in disabled_platforms]


KEYWORD_PARSER_MAP: dict[str, BaseParser] = {}
T = TypeVar("T", bound=BaseParser)


def get_parser(keyword: str) -> BaseParser:
    return KEYWORD_PARSER_MAP[keyword]


def get_parser_by_type(parser_type: type[T]) -> T:
    for parser in KEYWORD_PARSER_MAP.values():
        if isinstance(parser, parser_type):
            return parser
    raise ValueError(f"Parser instance not found for type {parser_type}")


@UniHelper.with_reaction
async def parser_handler(sr: SearchResult = Searched()):
    cache_key = sr.searched.group(0)
    result = _RESULT_CACHE.get(cache_key)

    if result is None:
        parser = get_parser(sr.keyword)
        try:
            result = await parser.parse(sr.keyword, sr.searched)
            logger.debug(f"parse result: {result}")
        except ParseException as e:
            logger.warning(f"parse failed: {e.message}")
            await UniMessage(e.message).send()
            return
        except Exception:
            logger.exception("parse failed")
            return
    else:
        logger.debug(f"cache hit: {cache_key}, result: {result}")

    try:
        renderer = get_renderer(result.platform.name)
        async for message in renderer.render_messages(result):
            await message.send()
    except Exception:
        logger.exception("render or send failed")
        return

    _RESULT_CACHE[cache_key] = result


@get_driver().on_startup
def register_parser_matcher():
    enabled_classes = _get_enabled_parser_classes()
    KEYWORD_PARSER_MAP.clear()

    enabled_platforms: list[str] = []
    for parser_cls in enabled_classes:
        parser = parser_cls()
        enabled_platforms.append(parser.platform.display_name)
        for keyword, _ in parser_cls._key_patterns:
            KEYWORD_PARSER_MAP[keyword] = parser

    logger.info(f"enabled platforms: {', '.join(sorted(enabled_platforms))}")

    patterns = [pattern for parser_cls in enabled_classes for pattern in parser_cls._key_patterns]
    matcher = on_keyword_regex(*patterns)
    matcher.append_handler(parser_handler)


_RESULT_CACHE = LimitedSizeDict[str, ParseResult](max_size=50)


def clear_result_cache():
    _RESULT_CACHE.clear()


from ..download import YTDLP_DOWNLOADER

if YTDLP_DOWNLOADER is not None:
    from ..parsers import YouTubeParser

    @on_command("ym", priority=3, block=True).handle()
    @UniHelper.with_reaction
    async def _(message: Message = CommandArg()):
        text = message.extract_plain_text()

        try:
            parser = get_parser_by_type(YouTubeParser)
            _, matched = parser.search_url(text)
        except Exception:
            await UniMessage("Please send a valid YouTube link").finish()

        url = matched.group(0)
        audio_path = await YTDLP_DOWNLOADER.download_audio(url)
        await UniMessage(UniHelper.record_seg(audio_path)).send()

        if pconfig.need_upload:
            await UniMessage(UniHelper.file_seg(audio_path)).send()
