import json
from pathlib import Path
from typing import List, Dict, Optional, Any
from nonebot import logger

class PixivManager:
    def __init__(self):
        self.data_dir = Path(__file__).parent / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.blacklist_file = self.data_dir / "blacklist.json"
        self.subscriptions_file = self.data_dir / "subscriptions.json"
        self.watches_file = self.data_dir / "watches.json"
        
        self.blacklist: List[str] = self._load_json(self.blacklist_file, [])
        self.subscriptions: List[Dict] = self._load_json(self.subscriptions_file, [])
        self.watches: List[Dict] = self._load_json(self.watches_file, [])
        self.r18_groups: List[str] = self._load_json(self.data_dir / "r18_groups.json", [])
        
        self.history_file = self.data_dir / "history.json"
        self.history: Dict[str, List[int]] = self._load_json(self.history_file, {})

    def _load_json(self, path: Path, default: Any):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except:
                pass
        return default

    def _save_json(self, path: Path, data: Any):
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Blacklist (Default Allow) ---
    def is_group_enabled(self, group_id: str) -> bool:
        # Return True if NOT in blacklist
        return str(group_id) not in self.blacklist

    def add_blacklist(self, group_id: str):
        if str(group_id) not in self.blacklist:
            self.blacklist.append(str(group_id))
            self._save_json(self.blacklist_file, self.blacklist)

    def remove_blacklist(self, group_id: str):
        if str(group_id) in self.blacklist:
            self.blacklist.remove(str(group_id))
            self._save_json(self.blacklist_file, self.blacklist)

    # --- Subscriptions (Scheduled) ---
    def add_subscription(self, group_id: str, sub_type: str, keyword: str, schedule: str):
        sub_data = {
            "group_id": str(group_id),
            "type": sub_type,
            "keyword": keyword,
            "schedule": schedule
        }
        self.subscriptions.append(sub_data)
        self._save_json(self.subscriptions_file, self.subscriptions)
        
    def get_subscriptions(self, group_id: str) -> List[Dict]:
        return [s for s in self.subscriptions if s.get("group_id") == str(group_id)]

    def remove_subscription(self, group_id: str, index: int) -> bool:
        group_subs = self.get_subscriptions(group_id)
        if 0 <= index < len(group_subs):
            target = group_subs[index]
            self.subscriptions.remove(target)
            self._save_json(self.subscriptions_file, self.subscriptions)
            return True
        return False

    def remove_subscription_by_type(self, group_id: str, sub_type: str) -> bool:
        original_len = len(self.subscriptions)
        self.subscriptions = [s for s in self.subscriptions if not (s.get("group_id") == str(group_id) and s.get("type") == sub_type)]
        if len(self.subscriptions) != original_len:
            self._save_json(self.subscriptions_file, self.subscriptions)
            return True
        return False

    # --- Watches (Update Push) ---
    def add_follow(self, group_id: str, user_id: str, user_name: str) -> bool:
        # check duplicate
        for w in self.watches:
            if w.get("group_id") == str(group_id) and str(w.get("user_id")) == str(user_id):
                return False
        
        self.watches.append({
            "group_id": str(group_id),
            "user_id": str(user_id),
            "user_name": user_name,
            "last_illust_id": None
        })
        self._save_json(self.watches_file, self.watches)
        return True

    def get_follows(self, group_id: str) -> List[Dict]:
        return [w for w in self.watches if w.get("group_id") == str(group_id)]

    def remove_follow(self, group_id: str, user_id: int) -> bool:
        original_len = len(self.watches)
        self.watches = [w for w in self.watches if not (w.get("group_id") == str(group_id) and str(w.get("user_id")) == str(user_id))]
        if len(self.watches) != original_len:
            self._save_json(self.watches_file, self.watches)
            return True
        return False

    def update_watch_record(self, group_id: str, user_id: str, last_illust_id: str):
        for w in self.watches:
            if w.get("group_id") == str(group_id) and str(w.get("user_id")) == str(user_id):
                w["last_illust_id"] = str(last_illust_id)
                self._save_json(self.watches_file, self.watches)
                return

    # --- R18 Settings ---
    def set_r18(self, group_id: str, enabled: bool):
        group_id = str(group_id)
        if enabled:
            if group_id not in self.r18_groups:
                self.r18_groups.append(group_id)
                self._save_json(self.data_dir / "r18_groups.json", self.r18_groups)
        else:
            if group_id in self.r18_groups:
                self.r18_groups.remove(group_id)
                self._save_json(self.data_dir / "r18_groups.json", self.r18_groups)

    def is_r18_enabled(self, group_id: str) -> bool:
        return str(group_id) in self.r18_groups

    # --- History (Deduplication) ---
    def record_history(self, group_id: str, illust_id: int):
        if not group_id: return
        group_id = str(group_id)
        if group_id not in self.history:
            self.history[group_id] = []
        
        # Add to history if not exists
        if illust_id not in self.history[group_id]:
            self.history[group_id].append(illust_id)
            # Limit history size (e.g. 300)
            if len(self.history[group_id]) > 300:
                self.history[group_id] = self.history[group_id][-300:]
            self._save_json(self.history_file, self.history)

    def is_in_history(self, group_id: str, illust_id: int) -> bool:
        if not group_id: return False
        group_id = str(group_id)
        return group_id in self.history and illust_id in self.history[group_id]

manager = PixivManager()
