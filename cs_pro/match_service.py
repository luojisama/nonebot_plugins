from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .binding_store import BindingStore, UserBinding

PLATFORM_ALIASES = {
    "5e": "5e",
    "fivee": "5e",
    "pw": "pw",
    "wanmei": "pw",
    "perfectworld": "pw",
    "mm": "mm",
    "official": "mm",
}


@dataclass
class PlayerStats:
    name: str
    uuid: str
    win: int
    elo_change: float
    rating: float
    adr: float
    rws: float
    kill: int
    death: int
    headshot_rate: float


@dataclass
class MatchResult:
    platform: str
    map_name: str
    match_type: str
    start_time: int
    duration_min: int
    result_text: str
    match_id: str
    player: PlayerStats
    teammates: list[PlayerStats]
    opponents: list[PlayerStats]

    def llm_context(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "map": self.map_name,
            "match_type": self.match_type,
            "start_time": self.start_time,
            "duration_min": self.duration_min,
            "result": self.result_text,
            "player": self.player.__dict__,
            "teammates": [x.__dict__ for x in self.teammates],
            "opponents": [x.__dict__ for x in self.opponents],
        }


class MatchService:
    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout
        self._pw_default_appversion = "3.5.4.172"
        self._pw_default_token = "3dbb2485ah77nn99950bf805bbr20ff13f5d355"
        self._pw_default_my_steam_id = 76561198929215155
        self._pw_session_file = Path("data/cs_pro/pw_session.json")

    @staticmethod
    def normalize_platform(raw: str | None) -> str | None:
        if not raw:
            return None
        return PLATFORM_ALIASES.get(raw.strip().lower())

    async def bind_player(self, store: BindingStore, qq_id: str, platform: str, player_name: str) -> UserBinding:
        if platform == "5e":
            domain, uuid, canonical = await self._bind_5e(player_name)
        elif platform == "pw":
            domain, uuid, canonical = await self._bind_pw(player_name)
        else:
            raise ValueError("绑定仅支持 5e 或 pw")

        store.upsert_binding(qq_id, platform, canonical, domain, uuid)
        bound = store.get_binding(qq_id, platform)
        if not bound:
            raise RuntimeError("绑定存储失败")
        return bound

    async def bind_5e_domain(self, store: BindingStore, qq_id: str, domain: str, canonical_name: str | None = None) -> UserBinding:
        uuid = await self._resolve_5e_uuid(domain)
        name = (canonical_name or domain).strip()
        store.upsert_binding(qq_id, "5e", name, domain.strip(), uuid)
        bound = store.get_binding(qq_id, "5e")
        if not bound:
            raise RuntimeError("5E绑定存储失败")
        return bound

    async def fetch_match(self, store: BindingStore, qq_id: str, platform: str | None, round_index: int) -> MatchResult:
        p = self.normalize_platform(platform) if platform else None
        if p is None:
            p = store.get_default_platform(qq_id)

        binding = store.get_binding(qq_id, p)
        if p == "mm" and not binding:
            binding = store.get_binding(qq_id, "pw")
        if not binding:
            raise ValueError(f"未绑定平台 {p}，请先使用 /bind")

        if p == "5e":
            match_id = await self._get_5e_match_id(binding.uuid, round_index)
            raw = await self._get_5e_match_detail(match_id)
            return self._parse_5e(raw, binding, match_id)

        # PW/MM: allow username-only binding, resolve IDs lazily before query.
        if not str(binding.uuid).strip():
            domain, uuid, canonical = await self._resolve_pw_identity(binding.player_name)
            store.upsert_binding(binding.qq_id, "pw", canonical, domain, uuid)
            if p == "mm":
                store.upsert_binding(binding.qq_id, "mm", canonical, domain, uuid)
            refreshed = store.get_binding(binding.qq_id, p) or store.get_binding(binding.qq_id, "pw")
            if not refreshed or not str(refreshed.uuid).strip():
                raise ValueError("未能根据完美用户名解析到SteamID，请检查用户名")
            binding = refreshed

        ds = 3 if p == "pw" else 1
        match_id = await self._get_pw_match_id(binding.uuid, round_index, ds)
        raw = await self._get_pw_match_detail(match_id, ds, binding.uuid)
        return self._parse_pw_mm(raw, binding, p, match_id)

    async def _bind_5e(self, player_name: str) -> tuple[str, str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://arena.5eplay.com/search?keywords={player_name}",
        }
        url = "https://arena.5eplay.com/api/search/player/1/16"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params={"keywords": player_name}, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        users = data.get("data", {}).get("user", {}).get("list", [])
        if not users:
            raise ValueError("未找到5E玩家，请检查昵称")

        target = None
        key = player_name.strip().lower()
        for u in users:
            if str(u.get("username", "")).strip().lower() == key:
                target = u
                break
        if target is None:
            target = users[0]

        domain = str(target.get("domain") or "")
        canonical = str(target.get("username") or player_name)
        if not domain:
            raise ValueError("5E玩家域名解析失败")

        uuid = await self._resolve_5e_uuid(domain)
        return domain, uuid, canonical

    async def _resolve_5e_uuid(self, domain: str) -> str:
        id_url = "https://gate.5eplay.com/userinterface/http/v1/userinterface/idTransfer"
        payload = {"trans": {"domain": domain}}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(id_url, json=payload, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            id_data = resp.json()

        uuid = str(id_data.get("data", {}).get("uuid") or "")
        if not uuid:
            raise ValueError("5E UUID 获取失败")
        return uuid

    async def _bind_pw(self, player_name: str) -> tuple[str, str, str]:
        # Binding rule: only username is required. Resolve IDs best-effort.
        # If ID fields are missing in search result, binding still succeeds.
        try:
            domain, uuid, canonical = await self._resolve_pw_identity(player_name)
            return domain, uuid, canonical
        except Exception:
            return "", "", player_name.strip()

    async def _resolve_pw_identity(self, player_name: str) -> tuple[str, str, str]:
        session = self._load_pw_session()
        url = "https://appengine.wmpvp.com/steamcn/app/search/user"
        headers = {
            "appversion": session["appversion"],
            "token": session["token"],
            "platform": "android",
            "Content-Type": "application/json",
        }
        payload = {"keyword": player_name, "page": 1}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 1:
            raise ValueError(data.get("description") or "完美搜索接口返回异常")
        users = data.get("result", [])
        if not users:
            raise ValueError("未找到完美玩家，请检查用户名")

        target = None
        key = player_name.strip().lower()
        for u in users:
            nick = str(u.get("pvpNickName") or u.get("name") or "").strip().lower()
            if nick == key:
                target = u
                break
        if target is None:
            target = users[0]

        pvp_user_id = target.get("pvpUserId")
        steam_id = target.get("steamId")
        canonical = str(target.get("pvpNickName") or target.get("name") or player_name)

        domain = str(pvp_user_id).strip()
        uuid = str(steam_id).strip()
        if not domain or not uuid:
            raise ValueError("完美用户名已找到，但缺少 pvpUserId 或 steamId")

        return domain, uuid, canonical

    async def _get_5e_match_id(self, uuid: str, round_index: int) -> str:
        url = f"https://gate.5eplay.com/crane/http/api/data/player_match?uuid={uuid}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            data = resp.json()

        items = data.get("data", {}).get("match_data", [])
        if not items or round_index <= 0 or round_index > len(items):
            raise ValueError(f"未找到倒数第 {round_index} 把5E对局")
        match_id = str(items[round_index - 1].get("match_id") or "")
        if not match_id:
            raise ValueError("5E match_id 解析失败")
        return match_id

    async def _get_5e_match_detail(self, match_id: str) -> dict:
        url = f"https://gate.5eplay.com/crane/http/api/data/match/{match_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            data = resp.json()
        payload = data.get("data", {})
        if not payload:
            raise ValueError("5E对局详情为空")
        return payload

    async def _get_pw_match_id(self, steam_id: str, round_index: int, data_source: int) -> str:
        session = self._load_pw_session()
        url = "https://api.wmpvp.com/api/csgo/home/match/list"
        headers = self._pw_headers(session)
        payload = {
            "toSteamId": int(steam_id),
            "mySteamId": int(session["my_steam_id"]),
            "dataSource": data_source,
            "page": 1,
            "pageSize": 20,
            "csgoSeasonId": "recent",
            "pvpType": -1,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        if data.get("statusCode") != 0:
            raise ValueError(data.get("errorMessage") or "完美/官匹对局列表获取失败")
        items = data.get("data", {}).get("matchList", [])
        if not items or round_index <= 0 or round_index > len(items):
            raise ValueError(f"未找到倒数第 {round_index} 把对局")
        match_id = str(items[round_index - 1].get("matchId") or "")
        if not match_id:
            raise ValueError("match_id 解析失败")
        return match_id

    async def _get_pw_match_detail(self, match_id: str, data_source: int, steam_id: str) -> dict:
        session = self._load_pw_session()
        headers = self._pw_headers(session)

        # Primary endpoints (API collection style), then fallback to v1.
        candidates = [
            (
                "https://api.wmpvp.com/api/csgo/home/match/detailStats",
                {
                    "matchId": match_id,
                    "toSteamId": int(steam_id),
                    "mySteamId": int(session["my_steam_id"]),
                    "dataSource": data_source,
                },
            ),
            (
                "https://api.wmpvp.com/api/csgo/home/match/detail",
                {
                    "matchId": match_id,
                    "toSteamId": int(steam_id),
                    "mySteamId": int(session["my_steam_id"]),
                    "dataSource": data_source,
                },
            ),
            (
                "https://api.wmpvp.com/api/v1/csgo/match",
                {
                    "matchId": match_id,
                    "platform": "admin",
                    "dataSource": str(data_source),
                },
            ),
        ]

        last_error = "完美/官匹对局详情获取失败"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for url, payload in candidates:
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    if data.get("statusCode") != 0:
                        last_error = str(data.get("errorMessage") or last_error)
                        continue
                    body = data.get("data", {})
                    if body:
                        return body
                except Exception as exc:
                    last_error = str(exc)
                    continue

        raise ValueError(last_error or "完美/官匹对局详情为空")

    def _parse_5e(self, data: dict, binding: UserBinding, match_id: str) -> MatchResult:
        base = data.get("main", {})
        g1 = data.get("group_1", [])
        g2 = data.get("group_2", [])
        target, team, opp = self._pick_target_5e(g1, g2, binding)
        if not target:
            raise ValueError("5E对局中未找到绑定玩家")

        player = self._extract_5e_player(target)
        teammates = [self._extract_5e_player(x) for x in team if x is not target]
        opponents = [self._extract_5e_player(x) for x in opp]

        start_time = int(base.get("start_time") or 0)
        end_time = int(base.get("end_time") or 0)
        duration = max(1, (end_time - start_time) // 60) if end_time and start_time else 30
        result_text = "胜利" if player.win == 1 else "失败"

        return MatchResult(
            platform="5e",
            map_name=str(base.get("map_desc") or "未知地图"),
            match_type="5E排位",
            start_time=start_time or int(dt.datetime.now().timestamp()),
            duration_min=duration,
            result_text=result_text,
            match_id=match_id,
            player=player,
            teammates=teammates,
            opponents=opponents,
        )

    def _pick_target_5e(self, g1: list, g2: list, binding: UserBinding):
        def hit(item: dict) -> bool:
            u = item.get("user_info", {}).get("user_data", {})
            name = str(u.get("username") or "")
            uid = str(u.get("uid") or "")
            uuid = str(u.get("uuid") or "")
            return (
                name.lower() == binding.player_name.lower()
                or uid == binding.uuid
                or uuid == binding.uuid
            )

        for p in g1:
            if hit(p):
                return p, g1, g2
        for p in g2:
            if hit(p):
                return p, g2, g1
        return None, None, None

    def _extract_5e_player(self, row: dict) -> PlayerStats:
        u = row.get("user_info", {}).get("user_data", {})
        f = row.get("fight", {})
        s = row.get("sts", {})
        k = int(f.get("kill") or 0)
        hs = 0.0 if k == 0 else float(f.get("headshot") or 0) / max(k, 1)
        return PlayerStats(
            name=str(u.get("username") or "未知"),
            uuid=str(u.get("uuid") or u.get("uid") or ""),
            win=int(f.get("is_win") or 0),
            elo_change=float(s.get("change_elo") or 0),
            rating=float(f.get("rating2") or 0),
            adr=float(f.get("adr") or 0),
            rws=float(f.get("rws") or 0),
            kill=k,
            death=int(f.get("death") or 0),
            headshot_rate=hs,
        )

    def _parse_pw_mm(self, data: dict, binding: UserBinding, platform: str, match_id: str) -> MatchResult:
        base = data.get("base") or {}
        players = data.get("players") or []
        target = self._pick_target_pw(players, binding)
        if not target:
            raise ValueError("对局中未找到绑定玩家")

        target_team = self._resolve_team(target, base)
        if target_team <= 0:
            raise ValueError("无法识别玩家队伍")

        player = self._extract_pw_player(target, base)
        teammates: list[PlayerStats] = []
        opponents: list[PlayerStats] = []
        for row in players:
            if row is target:
                continue
            ps = self._extract_pw_player(row, base)
            team = self._resolve_team(row, base)
            if team == target_team:
                teammates.append(ps)
            else:
                opponents.append(ps)

        start_ts = self._parse_time(base.get("startTime"))
        end_ts = self._parse_time(base.get("endTime"))
        if start_ts <= 0:
            start_ts = int(dt.datetime.now().timestamp())
        duration = max(1, (end_ts - start_ts) // 60) if end_ts > start_ts else int(base.get("duration") or 30)

        map_name = str(base.get("map") or base.get("mapEn") or "未知地图")
        match_type = str(base.get("mode") or base.get("mode2") or base.get("matchType") or ("官匹" if platform == "mm" else "完美"))
        result_text = "胜利" if player.win == 1 else "失败"

        return MatchResult(
            platform=platform,
            map_name=map_name,
            match_type=match_type,
            start_time=start_ts,
            duration_min=duration,
            result_text=result_text,
            match_id=match_id,
            player=player,
            teammates=teammates,
            opponents=opponents,
        )

    def _pick_target_pw(self, players: list[dict], binding: UserBinding) -> dict | None:
        name_key = binding.player_name.strip().lower()
        uuid_key = str(binding.uuid).strip()
        for p in players:
            pid = str(p.get("playerId") or "").strip()
            name = str(p.get("nickName") or "").strip().lower()
            if uuid_key and pid == uuid_key:
                return p
            if name_key and name == name_key:
                return p
        return None

    def _extract_pw_player(self, row: dict, base: dict) -> PlayerStats:
        team = self._resolve_team(row, base)
        win_team = int(base.get("winTeam") or 0)
        hs = float(row.get("headShotRatio") or 0.0)
        if hs > 1:
            hs /= 100
        return PlayerStats(
            name=str(row.get("nickName") or row.get("playerId") or "未知"),
            uuid=str(row.get("playerId") or ""),
            win=1 if team == win_team and win_team > 0 else 0,
            elo_change=float(row.get("pvpScoreChange") or 0),
            rating=float(row.get("pwRating") or row.get("rating") or 0),
            adr=float(row.get("adpr") or 0),
            rws=float(row.get("rws") or 0),
            kill=int(row.get("kill") or 0),
            death=int(row.get("death") or 0),
            headshot_rate=hs,
        )

    def _resolve_team(self, row: dict, base: dict) -> int:
        team = int(row.get("team") or 0)
        if team in (1, 2):
            return team

        pid = str(row.get("playerId") or "")
        t1 = {x.strip() for x in str(base.get("team1Info") or "").split(",") if x.strip()}
        t2 = {x.strip() for x in str(base.get("team2Info") or "").split(",") if x.strip()}
        if pid in t1:
            return 1
        if pid in t2:
            return 2
        return 0

    @staticmethod
    def _parse_time(text: Any) -> int:
        if not text:
            return 0
        val = str(text)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                return int(dt.datetime.strptime(val, fmt).timestamp())
            except Exception:
                pass
        if re.fullmatch(r"\d+", val):
            n = int(val)
            if n > 10_000_000_000:
                n //= 1000
            return n
        return 0

    def _load_pw_session(self) -> dict[str, Any]:
        data = {
            "token": self._pw_default_token,
            "my_steam_id": self._pw_default_my_steam_id,
            "appversion": self._pw_default_appversion,
        }
        if not self._pw_session_file.exists():
            return data
        try:
            raw = json.loads(self._pw_session_file.read_text(encoding="utf-8"))
            token = str(raw.get("token") or "").strip()
            sid = raw.get("steam_id")
            if token:
                data["token"] = token
            if sid is not None:
                data["my_steam_id"] = int(sid)
        except Exception:
            pass
        return data

    @staticmethod
    def _pw_headers(session: dict[str, Any]) -> dict[str, str]:
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "okhttp/4.11.0",
            "appversion": str(session.get("appversion") or "3.5.4.172"),
            "platform": "android",
            "token": str(session.get("token") or ""),
        }


def parse_match_args(raw: str) -> tuple[str | None, int]:
    tokens = [x for x in re.split(r"\s+", (raw or "").strip()) if x]
    platform = None
    round_index = 1
    for t in tokens:
        p = MatchService.normalize_platform(t)
        if p:
            platform = p
            continue
        if t.isdigit():
            round_index = max(1, int(t))
    return platform, round_index


def parse_bind_args(raw: str, default_platform: str = "5e") -> tuple[str, str]:
    tokens = [x for x in re.split(r"\s+", (raw or "").strip()) if x]
    if not tokens:
        raise ValueError("参数为空")

    platform = None
    if tokens and MatchService.normalize_platform(tokens[0]) in ("5e", "pw", "mm"):
        platform = MatchService.normalize_platform(tokens[0])
        tokens = tokens[1:]
    elif tokens and MatchService.normalize_platform(tokens[-1]) in ("5e", "pw", "mm"):
        platform = MatchService.normalize_platform(tokens[-1])
        tokens = tokens[:-1]

    if platform is None:
        platform = default_platform
    if platform == "mm":
        raise ValueError("官匹(mm)复用 pw 绑定，请使用 /bind pw <用户名>")

    if not tokens:
        raise ValueError("缺少玩家名")
    return platform, " ".join(tokens).strip()
