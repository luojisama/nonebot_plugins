import re
from datetime import datetime, timezone
from typing import Any

from dateutil import parser


def relative_time(date_str: str) -> str:
    if not date_str:
        return "未知时间"

    cleaned = re.sub(r"\([^)]*\)", "", date_str).strip()
    try:
        dt = parser.parse(cleaned)
    except Exception:
        return "未知时间"

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(tz=dt.tzinfo)
    delta = now - dt
    seconds = max(0, int(delta.total_seconds()))

    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60}分钟前"
    if seconds < 86400:
        return f"{seconds // 3600}小时前"
    if seconds < 2592000:
        return f"{seconds // 86400}天前"
    if seconds < 31536000:
        return f"{seconds // 2592000}个月前"
    return f"{seconds // 31536000}年前"


def format_search_item(index: int, game: dict[str, Any]) -> str:
    game_id = game.get("id", "?")
    name = game.get("name", "未知")
    platforms = ", ".join(game.get("platform", [])) or "未知"
    languages = ", ".join(game.get("language", [])) or "未知"
    return (
        f"{index}. ID: {game_id} | {name}\n"
        f"   平台: {platforms}\n"
        f"   语言: {languages}"
    )


def format_downloads(downloads: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for idx, res in enumerate(downloads, 1):
        platforms = ", ".join(res.get("platform", [])) or "未知"
        languages = ", ".join(res.get("language", [])) or "未知"
        lines.extend(
            [
                f"{idx}. {res.get('name', '未知资源')}",
                f"   平台: {platforms}",
                f"   体积: {res.get('size', '未知')}",
                f"   链接: {res.get('content', '无')}",
                f"   提取码: {res.get('code') or '无'}",
                f"   解压码: {res.get('password') or '无'}",
                f"   语言: {languages}",
                f"   发布时间: {relative_time(str(res.get('created', '')))}",
                f"   备注: {res.get('note') or '无'}",
                "",
            ]
        )
    if lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)
