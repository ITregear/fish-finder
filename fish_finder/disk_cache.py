from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from threading import Lock
from typing import Generic, TypeVar

V = TypeVar("V")

_CACHE_DIR = Path(".fish_finder_cache")


class PersistentTTLCache(Generic[V]):
    """JSON-backed cache that persists values across CLI runs."""

    def __init__(self, namespace: str, ttl_seconds: float, max_entries: int = 512) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.path = _CACHE_DIR / f"{namespace}.json"
        self._lock = Lock()
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, dict] = self._load()

    def get(self, key: str) -> V | None:
        hashed = _hash(key)
        now = time.time()
        with self._lock:
            entry = self._entries.get(hashed)
            if entry is None:
                return None
            if entry["expires_at"] <= now:
                self._entries.pop(hashed, None)
                self._flush()
                return None
            return entry["value"]

    def set(self, key: str, value: V) -> None:
        hashed = _hash(key)
        now = time.time()
        with self._lock:
            self._entries[hashed] = {
                "expires_at": now + self.ttl_seconds,
                "value": value,
            }
            self._prune(now)
            self._flush()

    def _prune(self, now: float) -> None:
        expired = [k for k, v in self._entries.items() if v["expires_at"] <= now]
        for k in expired:
            self._entries.pop(k, None)

        if len(self._entries) <= self.max_entries:
            return
        ordered = sorted(self._entries.items(), key=lambda item: item[1]["expires_at"])
        to_drop = len(self._entries) - self.max_entries
        for k, _ in ordered[:to_drop]:
            self._entries.pop(k, None)

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _flush(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._entries))
        tmp.replace(self.path)


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
