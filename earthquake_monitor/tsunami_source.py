from typing import List, Optional

import httpx
from nonebot import logger
from pydantic import BaseModel

from .config import state_store


class TsunamiInfo(BaseModel):
    id: str
    time: str
    grade: str
    title: str
    region: str


class TsunamiSource:
    def __init__(self):
        self._url = "https://api.p2pquake.net/v2/history?codes=552&limit=5"
        self._state_key = "tsunami_last_id"

    async def fetch_latest(self) -> List[TsunamiInfo]:
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.get(self._url)
            if resp.status_code != 200:
                return []
            rows = resp.json()
            if not isinstance(rows, list):
                return []
        except Exception as e:
            logger.error(f"[earthquake_monitor] fetch tsunami failed: {e}")
            return []

        result: List[TsunamiInfo] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            issue = row.get("issue") or {}
            area = row.get("areas") or []
            area_name = "未知区域"
            grade = "未知"
            if isinstance(area, list) and area:
                first = area[0] if isinstance(area[0], dict) else {}
                area_name = str(first.get("name") or area_name)
                grade = str(first.get("grade") or grade)

            event_id = str(issue.get("sourceId") or row.get("id") or "")
            if not event_id:
                event_id = str(row.get("time") or "")
            if not event_id:
                continue

            result.append(
                TsunamiInfo(
                    id=event_id,
                    time=str(row.get("time") or "未知"),
                    grade=grade,
                    title=str(issue.get("source") or "海啸预警"),
                    region=area_name,
                )
            )
        return result

    async def get_new_updates(self) -> List[TsunamiInfo]:
        latest = await self.fetch_latest()
        if not latest:
            return []

        last_id = state_store.get(self._state_key)
        if not last_id:
            state_store.set(self._state_key, latest[0].id)
            return []

        out: List[TsunamiInfo] = []
        for item in latest:
            if item.id == last_id:
                break
            out.append(item)

        state_store.set(self._state_key, latest[0].id)
        return list(reversed(out))

    async def get_current(self) -> Optional[TsunamiInfo]:
        latest = await self.fetch_latest()
        return latest[0] if latest else None


tsunami_source = TsunamiSource()
