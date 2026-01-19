from pydantic import BaseModel, Field
from typing import List, Set
import json
import os
from pathlib import Path

class Config(BaseModel):
    earthquake_monitor_interval: int = 60  # 地震监测间隔，单位秒
    typhoon_monitor_interval: int = 600   # 台风监测间隔，单位秒 (台风更新较慢，10分钟一次即可)
    earthquake_monitor_whitelist_path: str = "data/earthquake_monitor/whitelist.json"
    typhoon_monitor_whitelist_path: str = "data/earthquake_monitor/typhoon_whitelist.json"

class WhitelistManager:
    def __init__(self, path: str):
        self.path = Path(path)
        self.whitelist: Set[str] = set()
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.whitelist = set(data)
            except Exception:
                self.whitelist = set()
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._save()

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(list(self.whitelist), f, ensure_ascii=False, indent=4)

    def add(self, group_id: str) -> bool:
        if group_id not in self.whitelist:
            self.whitelist.add(group_id)
            self._save()
            return True
        return False

    def remove(self, group_id: str) -> bool:
        if group_id in self.whitelist:
            self.whitelist.remove(group_id)
            self._save()
            return True
        return False

    def is_whitelisted(self, group_id: str) -> bool:
        return group_id in self.whitelist

    def get_all(self) -> List[str]:
        return list(self.whitelist)

config = Config()
whitelist_manager = WhitelistManager(config.earthquake_monitor_whitelist_path)
typhoon_whitelist = WhitelistManager(config.typhoon_monitor_whitelist_path)
