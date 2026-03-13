from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UserBinding:
    qq_id: str
    platform: str
    player_name: str
    domain: str
    uuid: str
    updated_at: int


class BindingStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._migrate_legacy_once()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_bindings (
                    qq_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    domain TEXT,
                    uuid TEXT,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (qq_id, platform)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plugin_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def upsert_binding(self, qq_id: str, platform: str, player_name: str, domain: str, uuid: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_bindings (qq_id, platform, player_name, domain, uuid, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(qq_id, platform)
                DO UPDATE SET
                    player_name=excluded.player_name,
                    domain=excluded.domain,
                    uuid=excluded.uuid,
                    updated_at=excluded.updated_at
                """,
                (qq_id, platform, player_name, domain, uuid, int(time.time())),
            )
            conn.commit()

    def get_binding(self, qq_id: str, platform: str) -> UserBinding | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT qq_id, platform, player_name, domain, uuid, updated_at
                FROM user_bindings
                WHERE qq_id=? AND platform=?
                """,
                (qq_id, platform),
            ).fetchone()
            if not row:
                return None
            return UserBinding(**dict(row))

    def get_default_platform(self, qq_id: str) -> str:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT platform FROM user_bindings
                WHERE qq_id=?
                ORDER BY updated_at DESC
                """,
                (qq_id,),
            ).fetchall()
            plats = [str(x["platform"]) for x in rows]
            if "5e" in plats:
                return "5e"
            if "pw" in plats:
                return "pw"
            if plats:
                return plats[0]
            return "5e"

    def get_all_bindings(self) -> list[UserBinding]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT qq_id, platform, player_name, domain, uuid, updated_at FROM user_bindings"
            ).fetchall()
        return [UserBinding(**dict(row)) for row in rows]

    def _meta_get(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM plugin_meta WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def _meta_set(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO plugin_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            conn.commit()

    def _migrate_legacy_once(self) -> None:
        if self._meta_get("legacy_migrated") == "1":
            return

        imported = 0
        imported += self._migrate_from_sqlite_candidates()
        imported += self._migrate_from_json_candidates()
        self._meta_set("legacy_migrated", "1")
        self._meta_set("legacy_imported", str(imported))

    def _migrate_from_sqlite_candidates(self) -> int:
        candidates = [
            Path("data/astrbot_plugin_csstats/user_data.db"),
            Path("data/csstats/user_data.db"),
            Path("data/cs_pro/user_data.db"),
        ]
        total = 0
        for db in candidates:
            if not db.exists() or db.resolve() == self.db_path.resolve():
                continue
            try:
                conn = sqlite3.connect(db)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT qq_id, platform, player_name, domain, uuid, updated_at FROM user_bindings"
                ).fetchall()
                conn.close()
                for row in rows:
                    self.upsert_binding(
                        qq_id=str(row["qq_id"]),
                        platform=str(row["platform"]),
                        player_name=str(row["player_name"] or ""),
                        domain=str(row["domain"] or ""),
                        uuid=str(row["uuid"] or ""),
                    )
                    total += 1
            except Exception:
                continue
        return total

    def _migrate_from_json_candidates(self) -> int:
        candidates = [
            Path("data/astrbot_plugin_csstats/user_data.json"),
            Path("data/csstats/user_data.json"),
            Path("data/cs_pro/user_data.json"),
        ]
        total = 0
        for js in candidates:
            if not js.exists():
                continue
            try:
                raw = json.loads(js.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    continue
                for qq_id, user_entry in raw.items():
                    qq = str(qq_id)
                    platform_data = user_entry.get("platform_data", {}) if isinstance(user_entry, dict) else {}
                    if isinstance(platform_data, dict):
                        for platform, bind in platform_data.items():
                            if not isinstance(bind, dict):
                                continue
                            self.upsert_binding(
                                qq_id=qq,
                                platform=str(platform),
                                player_name=str(bind.get("name") or ""),
                                domain=str(bind.get("domain") or ""),
                                uuid=str(bind.get("uuid") or ""),
                            )
                            total += 1
            except Exception:
                continue
        return total
