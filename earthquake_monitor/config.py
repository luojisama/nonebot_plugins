import json
from pathlib import Path
from typing import Dict, List, Optional, Set

from pydantic import BaseModel


class Config(BaseModel):
    earthquake_monitor_interval: int = 20
    typhoon_monitor_interval: int = 600
    weather_monitor_interval: int = 300
    tsunami_monitor_interval: int = 300
    earthquake_sources: List[str] = [
        "ceic",
        "wolfx_cenc",
        "wolfx_jma",
        "p2p_jma",
        "usgs",
    ]
    earthquake_min_magnitude: float = 5.0
    earthquake_domestic_only: bool = True
    p2p_jma_history_limit: int = 30
    usgs_feed_url: str = (
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
    )

    earthquake_monitor_whitelist_path: str = "data/earthquake_monitor/whitelist.json"
    typhoon_monitor_whitelist_path: str = "data/earthquake_monitor/typhoon_whitelist.json"
    weather_monitor_whitelist_path: str = "data/earthquake_monitor/weather_whitelist.json"
    tsunami_monitor_whitelist_path: str = "data/earthquake_monitor/tsunami_whitelist.json"
    monitor_state_path: str = "data/earthquake_monitor/state.json"

    # Optional weather warning endpoint. If empty, weather warning monitor is disabled.
    weather_warning_api_url: str = "https://www.nmc.cn/rest/warning"


class JsonSetStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.items: Set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._save()
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self.items = {str(v) for v in data}
        except Exception:
            self.items = set()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(sorted(self.items), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add(self, group_id: str) -> bool:
        if group_id in self.items:
            return False
        self.items.add(group_id)
        self._save()
        return True

    def remove(self, group_id: str) -> bool:
        if group_id not in self.items:
            return False
        self.items.remove(group_id)
        self._save()
        return True

    def is_enabled(self, group_id: str) -> bool:
        return group_id in self.items

    def get_all(self) -> List[str]:
        return sorted(self.items)


class JsonStateStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.data: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data = {str(k): str(v) for k, v in raw.items()}
        except Exception:
            self.data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.data.get(key, default)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value
        self._save()


config = Config()

whitelist_manager = JsonSetStore(config.earthquake_monitor_whitelist_path)
typhoon_whitelist = JsonSetStore(config.typhoon_monitor_whitelist_path)
weather_whitelist = JsonSetStore(config.weather_monitor_whitelist_path)
tsunami_whitelist = JsonSetStore(config.tsunami_monitor_whitelist_path)
state_store = JsonStateStore(config.monitor_state_path)
