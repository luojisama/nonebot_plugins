import asyncio
import time
from typing import Any


class AsyncTTLCache:
    def __init__(self, ttl_seconds: int = 86400, max_size: int = 1000) -> None:
        self._cache: dict[int, dict[str, Any]] = {}
        self._expires_at: dict[int, float] = {}
        self._order: list[int] = []
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._lock = asyncio.Lock()

    async def add(self, key: int, value: dict[str, Any]) -> None:
        async with self._lock:
            now = time.time()
            if key not in self._cache and len(self._cache) >= self._max_size:
                oldest = self._order.pop(0)
                self._cache.pop(oldest, None)
                self._expires_at.pop(oldest, None)

            self._cache[key] = value
            self._expires_at[key] = now + self._ttl
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)

    async def get(self, key: int) -> dict[str, Any] | None:
        async with self._lock:
            now = time.time()
            exp = self._expires_at.get(key, 0)
            if exp <= now:
                self._cache.pop(key, None)
                self._expires_at.pop(key, None)
                if key in self._order:
                    self._order.remove(key)
                return None

            value = self._cache.get(key)
            if value is not None:
                if key in self._order:
                    self._order.remove(key)
                self._order.append(key)
            return value

    async def cleanup(self) -> int:
        async with self._lock:
            now = time.time()
            expired = [k for k, v in self._expires_at.items() if v <= now]
            for key in expired:
                self._cache.pop(key, None)
                self._expires_at.pop(key, None)
                if key in self._order:
                    self._order.remove(key)
            return len(expired)
