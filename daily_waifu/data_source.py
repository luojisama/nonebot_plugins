import json
import os
import time
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any


DEFAULT_GROUP_STATE: dict[str, Any] = {
    "config": {},
    "users": [],
    "draw_status": {},
    "last_draw": 0,
    "last_claim": {},
    "partners": {},
    "fav": {},
    "married_to": {},
    "wish_list": {},
    "wished_by": {},
    "custom_images": {},
    "harem_heats": {},
    "draw_messages": {},
    "exchange_requests": {},
}


class MudaeState:
    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = RLock()
        self._data: dict[str, Any] = {"groups": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            if "groups" not in self._data or not isinstance(self._data["groups"], dict):
                self._data = {"groups": {}}
        except Exception:
            self._data = {"groups": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _group(self, gid: str) -> dict[str, Any]:
        groups = self._data.setdefault("groups", {})
        if gid not in groups or not isinstance(groups[gid], dict):
            groups[gid] = deepcopy(DEFAULT_GROUP_STATE)
        grp = groups[gid]
        for key, val in DEFAULT_GROUP_STATE.items():
            if key not in grp:
                grp[key] = deepcopy(val)
        return grp

    def get_group_cfg(self, gid: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._group(gid).get("config", {}))

    def set_group_cfg(self, gid: str, config: dict[str, Any]) -> None:
        with self._lock:
            self._group(gid)["config"] = dict(config)
            self._save()

    def add_user(self, gid: str, uid: str) -> None:
        with self._lock:
            users = self._group(gid)["users"]
            if uid not in users:
                users.append(uid)
                self._save()

    def get_draw_status(self, gid: str, uid: str) -> tuple[str | None, int]:
        with self._lock:
            raw = self._group(gid)["draw_status"].get(uid)
            if not isinstance(raw, dict):
                return None, 0
            return raw.get("bucket"), int(raw.get("count", 0))

    def set_draw_status(self, gid: str, uid: str, bucket: str, count: int) -> None:
        with self._lock:
            self._group(gid)["draw_status"][uid] = {"bucket": bucket, "count": int(count)}
            self._save()

    def clear_draw_and_claim_cooldown(self, gid: str, uid: str) -> None:
        with self._lock:
            grp = self._group(gid)
            grp["draw_status"].pop(uid, None)
            grp["last_claim"].pop(uid, None)
            self._save()

    def get_last_draw(self, gid: str) -> float:
        with self._lock:
            return float(self._group(gid).get("last_draw", 0))

    def set_last_draw(self, gid: str, ts: float) -> None:
        with self._lock:
            self._group(gid)["last_draw"] = float(ts)
            self._save()

    def get_last_claim(self, gid: str, uid: str) -> float:
        with self._lock:
            return float(self._group(gid)["last_claim"].get(uid, 0))

    def set_last_claim(self, gid: str, uid: str, ts: float) -> None:
        with self._lock:
            self._group(gid)["last_claim"][uid] = float(ts)
            self._save()

    def get_partners(self, gid: str, uid: str) -> list[str]:
        with self._lock:
            lst = self._group(gid)["partners"].get(uid, [])
            return [str(x) for x in lst]

    def set_partners(self, gid: str, uid: str, partners: list[str]) -> None:
        with self._lock:
            self._group(gid)["partners"][uid] = [str(x) for x in partners]
            self._save()

    def get_fav(self, gid: str, uid: str) -> str | None:
        with self._lock:
            fav = self._group(gid)["fav"].get(uid)
            return str(fav) if fav is not None else None

    def set_fav(self, gid: str, uid: str, cid: str) -> None:
        with self._lock:
            self._group(gid)["fav"][uid] = str(cid)
            self._save()

    def clear_fav(self, gid: str, uid: str) -> None:
        with self._lock:
            self._group(gid)["fav"].pop(uid, None)
            self._save()

    def get_married_to(self, gid: str, cid: str) -> str | None:
        with self._lock:
            value = self._group(gid)["married_to"].get(str(cid))
            return str(value) if value is not None else None

    def set_married_to(self, gid: str, cid: str, uid: str) -> None:
        with self._lock:
            self._group(gid)["married_to"][str(cid)] = str(uid)
            self._save()

    def clear_married_to(self, gid: str, cid: str) -> None:
        with self._lock:
            self._group(gid)["married_to"].pop(str(cid), None)
            self._save()

    def get_wish_list(self, gid: str, uid: str) -> list[str]:
        with self._lock:
            lst = self._group(gid)["wish_list"].get(uid, [])
            return [str(x) for x in lst]

    def set_wish_list(self, gid: str, uid: str, value: list[str]) -> None:
        with self._lock:
            self._group(gid)["wish_list"][uid] = [str(x) for x in value]
            self._save()

    def get_wished_by(self, gid: str, cid: str) -> list[str]:
        with self._lock:
            lst = self._group(gid)["wished_by"].get(str(cid), [])
            return [str(x) for x in lst]

    def set_wished_by(self, gid: str, cid: str, users: list[str]) -> None:
        with self._lock:
            if users:
                self._group(gid)["wished_by"][str(cid)] = [str(x) for x in users]
            else:
                self._group(gid)["wished_by"].pop(str(cid), None)
            self._save()

    def get_custom_images(self, gid: str, cid: str) -> list[str]:
        with self._lock:
            lst = self._group(gid)["custom_images"].get(str(cid), [])
            return [str(x) for x in lst]

    def set_custom_images(self, gid: str, cid: str, paths: list[str]) -> None:
        with self._lock:
            if paths:
                self._group(gid)["custom_images"][str(cid)] = [str(x) for x in paths]
            else:
                self._group(gid)["custom_images"].pop(str(cid), None)
            self._save()

    def set_harem_heat(self, gid: str, uid: str, heat: int) -> None:
        with self._lock:
            self._group(gid)["harem_heats"][uid] = int(heat)
            self._save()

    def get_harem_heats(self, gid: str) -> dict[str, int]:
        with self._lock:
            raw = self._group(gid)["harem_heats"]
            return {str(k): int(v) for k, v in raw.items()}

    def clear_harem_heats(self, gid: str) -> None:
        with self._lock:
            self._group(gid)["harem_heats"] = {}
            self._save()

    def track_draw_message(self, gid: str, msg_id: str, char_id: str, ttl_sec: int) -> None:
        now = time.time()
        with self._lock:
            draw_map = self._group(gid)["draw_messages"]
            cutoff = now - ttl_sec
            for mid in list(draw_map.keys()):
                ts = float(draw_map[mid].get("ts", 0))
                if ts and ts < cutoff:
                    draw_map.pop(mid, None)
            draw_map[str(msg_id)] = {"char_id": str(char_id), "ts": now}
            self._save()

    def pop_draw_message(self, gid: str, msg_id: str) -> dict[str, Any] | None:
        with self._lock:
            draw_map = self._group(gid)["draw_messages"]
            value = draw_map.pop(str(msg_id), None)
            self._save()
            return value

    def get_draw_message(self, gid: str, msg_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._group(gid)["draw_messages"].get(str(msg_id))
            return dict(value) if isinstance(value, dict) else None

    def create_exchange_request(self, gid: str, msg_id: str, payload: dict[str, str], ttl_sec: int) -> None:
        now = time.time()
        with self._lock:
            reqs = self._group(gid)["exchange_requests"]
            cutoff = now - ttl_sec
            for mid in list(reqs.keys()):
                ts = float(reqs[mid].get("ts", 0))
                if ts and ts < cutoff:
                    reqs.pop(mid, None)
            reqs[str(msg_id)] = {**payload, "ts": now}
            self._save()

    def pop_exchange_request(self, gid: str, msg_id: str) -> dict[str, Any] | None:
        with self._lock:
            reqs = self._group(gid)["exchange_requests"]
            value = reqs.pop(str(msg_id), None)
            self._save()
            return value

    def get_exchange_request(self, gid: str, msg_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._group(gid)["exchange_requests"].get(str(msg_id))
            return dict(value) if isinstance(value, dict) else None

    def get_all_users(self, gid: str) -> list[str]:
        with self._lock:
            return [str(x) for x in self._group(gid).get("users", [])]

    def cleanup_user_harem_keep_fav(self, gid: str, uid: str) -> tuple[list[str], str | None]:
        with self._lock:
            grp = self._group(gid)
            fav = grp["fav"].get(uid)
            partners = [str(x) for x in grp["partners"].get(uid, [])]
            for cid in partners:
                if fav is not None and str(cid) == str(fav):
                    continue
                grp["married_to"].pop(str(cid), None)
            if fav is None or str(fav) not in partners:
                grp["partners"].pop(uid, None)
                grp["fav"].pop(uid, None)
                kept = []
                kept_fav = None
            else:
                grp["partners"][uid] = [str(fav)]
                kept = [str(fav)]
                kept_fav = str(fav)
            self._save()
            return kept, kept_fav


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except Exception:
        pass
