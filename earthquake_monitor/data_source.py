from __future__ import annotations

import time
from datetime import datetime
from typing import Dict, Iterable, List

import httpx
from nonebot import logger
from pydantic import BaseModel

from .config import config, state_store


class EarthquakeInfo(BaseModel):
    id: str
    source: str
    time: str
    timestamp: int
    magnitude: str
    depth: str
    location: str
    latitude: str
    longitude: str


class EarthquakeSource:
    _SOURCE_LABELS: Dict[str, str] = {
        "ceic": "中国地震速报(CEIC)",
        "wolfx_cenc": "Wolfx CENC",
        "wolfx_jma": "Wolfx JMA",
        "p2p_jma": "P2P JMA",
        "usgs": "USGS",
    }

    def __init__(self):
        self._ceic_url = "http://api.dizhensubao.igexin.com/api.htm"
        self._wolfx_cenc_url = "https://api.wolfx.jp/cenc_eqlist.json"
        self._wolfx_jma_url = "https://api.wolfx.jp/jma_eqlist.json"
        self._p2p_jma_url = "https://api.p2pquake.net/v2/history"
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        self._domestic_keywords = [
            "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海", "江苏",
            "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西",
            "海南", "重庆", "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏",
            "新疆", "台湾", "香港", "澳门", "中国", "东海", "南海", "黄海", "渤海",
        ]

    def source_label(self, source: str) -> str:
        return self._SOURCE_LABELS.get(source, source)

    def enabled_sources(self) -> List[str]:
        enabled = []
        for src in config.earthquake_sources:
            if src in self._SOURCE_LABELS:
                enabled.append(src)
        return enabled

    def _state_key(self, source: str) -> str:
        return f"earthquake_last_id:{source}"

    @staticmethod
    def _source_cursor_key() -> str:
        return "earthquake_source_cursor"

    @staticmethod
    def _source_cursor_size_key() -> str:
        return "earthquake_source_cursor_size"

    @staticmethod
    def _window_seconds() -> int:
        return 12 * 3600

    @staticmethod
    def _min_magnitude() -> float:
        return max(5.0, float(config.earthquake_min_magnitude))

    def _is_domestic(self, location: str) -> bool:
        return any(k in location for k in self._domestic_keywords)

    def _allow_event(self, item: EarthquakeInfo) -> bool:
        try:
            mag = float(item.magnitude)
        except Exception:
            mag = 0.0
        if mag < self._min_magnitude():
            return False

        now_ts = int(time.time())
        if item.timestamp <= 0 or now_ts - item.timestamp > self._window_seconds():
            return False

        if not config.earthquake_domestic_only:
            return True
        return self._is_domestic(item.location)

    def _pick_source(self, enabled: List[str]) -> str | None:
        if not enabled:
            return None
        if len(enabled) == 1:
            return enabled[0]

        raw_size = state_store.get(self._source_cursor_size_key(), "")
        raw_cursor = state_store.get(self._source_cursor_key(), "0")
        try:
            last_size = int(raw_size)
        except Exception:
            last_size = 0
        try:
            cursor = int(raw_cursor)
        except Exception:
            cursor = 0
        if last_size != len(enabled):
            cursor = 0

        idx = cursor % len(enabled)
        chosen = enabled[idx]
        state_store.set(self._source_cursor_key(), str((idx + 1) % len(enabled)))
        state_store.set(self._source_cursor_size_key(), str(len(enabled)))
        return chosen

    @staticmethod
    def _parse_dt_to_epoch(time_text: str) -> int:
        if not time_text:
            return 0
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
        ]
        for fmt in formats:
            try:
                return int(datetime.strptime(time_text, fmt).timestamp())
            except Exception:
                continue
        try:
            # Best effort for ISO strings with timezone and microseconds.
            return int(datetime.fromisoformat(time_text.replace("Z", "+00:00")).timestamp())
        except Exception:
            return 0

    def _normalize_item(
        self,
        *,
        source: str,
        event_id: str,
        timestamp: int,
        magnitude: float,
        depth: str,
        location: str,
        latitude: str,
        longitude: str,
    ) -> EarthquakeInfo:
        time_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S") if timestamp else "未知"
        norm_id = event_id or f"{source}:{timestamp}:{location}:{magnitude:.1f}"
        return EarthquakeInfo(
            id=norm_id,
            source=source,
            time=time_str,
            timestamp=timestamp,
            magnitude=f"{magnitude:.1f}",
            depth=depth,
            location=location or "未知",
            latitude=latitude,
            longitude=longitude,
        )

    async def _fetch_ceic(self) -> List[EarthquakeInfo]:
        payload = {
            "action": "requestMonitorDataAction",
            "startTime": "0",
            "dataSource": "CEIC",
        }
        try:
            async with httpx.AsyncClient(timeout=12, headers=self._headers) as client:
                resp = await client.post(self._ceic_url, json=payload)
            if resp.status_code != 200:
                return []
            values = (resp.json() or {}).get("values", [])
        except Exception as e:
            logger.error(f"[earthquake_monitor] fetch ceic failed: {e}")
            return []

        items: List[EarthquakeInfo] = []
        for row in values:
            try:
                mag = float(row.get("mag", 0) or 0)
                if mag < self._min_magnitude():
                    continue
                ts = int((row.get("time") or 0) / 1000)
                items.append(
                    self._normalize_item(
                        source="ceic",
                        event_id=str(row.get("eqid", "")),
                        timestamp=ts,
                        magnitude=mag,
                        depth=str(row.get("depth", "")),
                        location=str(row.get("loc_name", "")),
                        latitude=str(row.get("latitude", "")),
                        longitude=str(row.get("longitude", "")),
                    )
                )
            except Exception:
                continue
        return items

    async def _fetch_wolfx_list(self, source: str, url: str) -> List[EarthquakeInfo]:
        try:
            async with httpx.AsyncClient(timeout=12, headers=self._headers) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            if not isinstance(data, dict):
                return []
        except Exception as e:
            logger.error(f"[earthquake_monitor] fetch {source} failed: {e}")
            return []

        items: List[EarthquakeInfo] = []
        for key, row in data.items():
            if not key.startswith("No") or not isinstance(row, dict):
                continue
            try:
                mag = float(row.get("magnitude", 0) or 0)
                if mag < self._min_magnitude():
                    continue
                ts = self._parse_dt_to_epoch(str(row.get("time", "")))
                items.append(
                    self._normalize_item(
                        source=source,
                        event_id=str(row.get("md5") or row.get("id") or ""),
                        timestamp=ts,
                        magnitude=mag,
                        depth=str(row.get("depth", "")),
                        location=str(row.get("location", "")),
                        latitude=str(row.get("latitude", "")),
                        longitude=str(row.get("longitude", "")),
                    )
                )
            except Exception:
                continue
        return items

    async def _fetch_p2p_jma(self) -> List[EarthquakeInfo]:
        params = {"codes": 551, "limit": max(5, min(100, config.p2p_jma_history_limit))}
        try:
            async with httpx.AsyncClient(timeout=12, headers=self._headers) as client:
                resp = await client.get(self._p2p_jma_url, params=params)
            if resp.status_code != 200:
                return []
            rows = resp.json()
            if not isinstance(rows, list):
                return []
        except Exception as e:
            logger.error(f"[earthquake_monitor] fetch p2p_jma failed: {e}")
            return []

        items: List[EarthquakeInfo] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                quake = row.get("earthquake") or {}
                hypo = quake.get("hypocenter") or {}
                mag = float(hypo.get("magnitude", -1) or -1)
                if mag < self._min_magnitude():
                    continue

                ts = self._parse_dt_to_epoch(str(quake.get("time") or row.get("time") or ""))
                max_scale = quake.get("maxScale")
                loc = str(hypo.get("name") or "")
                if max_scale not in (None, -1):
                    loc = f"{loc} (最大震度:{max_scale})"

                items.append(
                    self._normalize_item(
                        source="p2p_jma",
                        event_id=str(row.get("id") or ""),
                        timestamp=ts,
                        magnitude=mag,
                        depth=str(hypo.get("depth", "")),
                        location=loc,
                        latitude=str(hypo.get("latitude", "")),
                        longitude=str(hypo.get("longitude", "")),
                    )
                )
            except Exception:
                continue
        return items

    async def _fetch_usgs(self) -> List[EarthquakeInfo]:
        try:
            async with httpx.AsyncClient(timeout=12, headers=self._headers) as client:
                resp = await client.get(config.usgs_feed_url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            features = data.get("features", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.error(f"[earthquake_monitor] fetch usgs failed: {e}")
            return []

        items: List[EarthquakeInfo] = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            try:
                props = feature.get("properties") or {}
                geom = feature.get("geometry") or {}
                coords = geom.get("coordinates") or []
                mag = float(props.get("mag", 0) or 0)
                if mag < self._min_magnitude():
                    continue

                ts = int((props.get("time") or 0) / 1000)
                lon = str(coords[0]) if len(coords) > 0 else ""
                lat = str(coords[1]) if len(coords) > 1 else ""
                depth = str(coords[2]) if len(coords) > 2 else ""
                items.append(
                    self._normalize_item(
                        source="usgs",
                        event_id=str(feature.get("id") or ""),
                        timestamp=ts,
                        magnitude=mag,
                        depth=depth,
                        location=str(props.get("place") or ""),
                        latitude=lat,
                        longitude=lon,
                    )
                )
            except Exception:
                continue
        return items

    async def fetch_latest(self) -> List[EarthquakeInfo]:
        enabled = self.enabled_sources()
        source = self._pick_source(enabled)
        if not source:
            return []

        logger.debug(f"[earthquake_monitor] selected source: {source}")
        if source == "ceic":
            merged = await self._fetch_ceic()
        elif source == "wolfx_cenc":
            merged = await self._fetch_wolfx_list("wolfx_cenc", self._wolfx_cenc_url)
        elif source == "wolfx_jma":
            merged = await self._fetch_wolfx_list("wolfx_jma", self._wolfx_jma_url)
        elif source == "p2p_jma":
            merged = await self._fetch_p2p_jma()
        elif source == "usgs":
            merged = await self._fetch_usgs()
        else:
            merged = []

        # Remove duplicates from same source and id.
        unique: Dict[str, EarthquakeInfo] = {}
        for item in merged:
            unique[f"{item.source}:{item.id}"] = item

        out = list(unique.values())
        out.sort(key=lambda x: x.timestamp, reverse=True)
        return out

    def _partition_by_source(self, items: Iterable[EarthquakeInfo]) -> Dict[str, List[EarthquakeInfo]]:
        grouped: Dict[str, List[EarthquakeInfo]] = {}
        for item in items:
            grouped.setdefault(item.source, []).append(item)
        for source in grouped:
            grouped[source].sort(key=lambda x: x.timestamp, reverse=True)
        return grouped

    async def get_new_earthquakes(self) -> List[EarthquakeInfo]:
        latest = await self.fetch_latest()
        if not latest:
            return []

        grouped = self._partition_by_source(latest)
        new_items: List[EarthquakeInfo] = []

        for source, source_items in grouped.items():
            state_key = self._state_key(source)
            last_id = state_store.get(state_key)

            if not last_id:
                state_store.set(state_key, source_items[0].id)
                continue

            for item in source_items:
                if item.id == last_id:
                    break
                if self._allow_event(item):
                    new_items.append(item)

            state_store.set(state_key, source_items[0].id)

        new_items.sort(key=lambda x: x.timestamp)
        return new_items

    async def get_history(self, count: int = 5) -> List[EarthquakeInfo]:
        latest = await self.fetch_latest()
        filtered = [item for item in latest if self._allow_event(item)]
        return filtered[:count]


earthquake_source = EarthquakeSource()
