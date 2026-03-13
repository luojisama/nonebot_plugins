import re
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup
from nonebot import logger
from pydantic import BaseModel

from .config import state_store


class TyphoonInfo(BaseModel):
    id: str
    name: str
    en_name: str
    time: str
    level: str
    pressure: str
    wind_speed: str
    location: str
    ref_pos: str


class TyphoonSource:
    def __init__(self):
        self._url = "https://www.nmc.cn/publish/typhoon/typhoon_new.html"
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        self._last_id_key = "typhoon_last_id"
        self._last_time_key = "typhoon_last_time"

    @staticmethod
    def _extract(pattern: str, text: str) -> str:
        m = re.search(pattern, text, flags=re.MULTILINE)
        return m.group(1).strip() if m else "未知"

    async def fetch_latest(self) -> Optional[TyphoonInfo]:
        try:
            async with httpx.AsyncClient(timeout=12, headers=self._headers) as client:
                resp = await client.get(self._url)
            if resp.status_code != 200:
                return None
        except Exception as e:
            logger.error(f"[earthquake_monitor] fetch typhoon failed: {e}")
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        writing = soup.find("div", class_="writing")
        if not writing:
            return None

        text = writing.get_text("\n")
        name = self._extract(r"命\s*名[：:]\s*[“\"]?([^”\"，,\n]+)", text)
        en_name = self._extract(r"命\s*名[：:].*[，,]\s*([A-Za-z0-9-]+)", text)
        tf_id = self._extract(r"编\s*号[：:]\s*(\d+)", text)
        update_time = self._extract(r"时\s*间[：:]\s*([^\n]+)", text)
        location = self._extract(r"中心位置[：:]\s*([^\n]+)", text)
        level = self._extract(r"强度等级[：:]\s*([^\n]+)", text)
        wind = self._extract(r"最大风力[：:]\s*([^\n]+)", text)
        pressure = self._extract(r"中心气压[：:]\s*([^\n]+)", text)
        ref_pos = self._extract(r"参考位置[：:]\s*([^\n]+)", text)

        return TyphoonInfo(
            id=tf_id,
            name=name,
            en_name=en_name,
            time=update_time,
            level=level,
            pressure=pressure,
            wind_speed=wind,
            location=location,
            ref_pos=ref_pos,
        )

    async def get_new_updates(self) -> List[TyphoonInfo]:
        latest = await self.fetch_latest()
        if not latest:
            return []

        last_id = state_store.get(self._last_id_key)
        last_time = state_store.get(self._last_time_key)

        if not last_id:
            state_store.set(self._last_id_key, latest.id)
            state_store.set(self._last_time_key, latest.time)
            return []

        if latest.id == last_id and latest.time == last_time:
            return []

        state_store.set(self._last_id_key, latest.id)
        state_store.set(self._last_time_key, latest.time)
        return [latest]


typhoon_source = TyphoonSource()
