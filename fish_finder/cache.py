from __future__ import annotations

import time
from threading import Lock
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """Thread-safe in-memory cache with a fixed TTL per entry."""

    def __init__(self, ttl_seconds: float, max_entries: int = 256) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: dict[K, tuple[float, V]] = {}
        self._lock = Lock()

    def get(self, key: K) -> V | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return None
            return value

    def get_with_presence(self, key: K) -> tuple[bool, V | None]:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False, None
            expires_at, value = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return False, None
            return True, value

    def set(self, key: K, value: V) -> None:
        now = time.monotonic()
        expires_at = now + self.ttl_seconds
        with self._lock:
            if len(self._entries) >= self.max_entries:
                oldest_key = min(self._entries.items(), key=lambda item: item[1][0])[0]
                self._entries.pop(oldest_key, None)
            self._entries[key] = (expires_at, value)
