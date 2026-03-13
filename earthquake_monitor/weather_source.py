from typing import List

import httpx
from nonebot import logger
from pydantic import BaseModel

from .config import config, state_store


class WeatherWarningInfo(BaseModel):
    id: str
    title: str
    level: str
    sender: str
    publish_time: str
    content: str


class WeatherWarningSource:
    def __init__(self):
        self._state_key = "weather_last_id"

    async def fetch_latest(self) -> List[WeatherWarningInfo]:
        if not config.weather_warning_api_url:
            return []

        try:
            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.get(config.weather_warning_api_url)
            if resp.status_code != 200:
                return []
            data = resp.json()
        except Exception as e:
            logger.error(f"[earthquake_monitor] fetch weather warning failed: {e}")
            return []

        rows = []
        if isinstance(data, dict):
            for key in ("data", "list", "items"):
                v = data.get(key)
                if isinstance(v, list):
                    rows = v
                    break
        elif isinstance(data, list):
            rows = data

        result: List[WeatherWarningInfo] = []
        for row in rows[:20]:
            if not isinstance(row, dict):
                continue
            wid = str(
                row.get("warningid")
                or row.get("id")
                or row.get("identifier")
                or ""
            )
            if not wid:
                continue
            result.append(
                WeatherWarningInfo(
                    id=wid,
                    title=str(row.get("headline") or row.get("title") or "气象预警"),
                    level=str(row.get("severity") or row.get("level") or "未知"),
                    sender=str(row.get("sender") or row.get("source") or "未知"),
                    publish_time=str(
                        row.get("sendTime")
                        or row.get("pubtime")
                        or row.get("publish_time")
                        or "未知"
                    ),
                    content=str(
                        row.get("description")
                        or row.get("text")
                        or row.get("content")
                        or ""
                    ),
                )
            )
        return result

    async def get_new_warnings(self) -> List[WeatherWarningInfo]:
        latest = await self.fetch_latest()
        if not latest:
            return []

        last_id = state_store.get(self._state_key)
        if not last_id:
            state_store.set(self._state_key, latest[0].id)
            return []

        out: List[WeatherWarningInfo] = []
        for item in latest:
            if item.id == last_id:
                break
            out.append(item)

        state_store.set(self._state_key, latest[0].id)
        return list(reversed(out))

    async def get_current(self) -> List[WeatherWarningInfo]:
        return await self.fetch_latest()


weather_warning_source = WeatherWarningSource()
