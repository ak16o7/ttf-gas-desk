"""Tiny async TTL cache with single-flight and best-effort disk persistence.

Render instances restart often (free tier spin-down, deploys). Persisting to CACHE_DIR means a
worker restart inside the same container does not re-hit every upstream API. On failure the
last good value is served (stale) instead of an error, flagged via `stale=True`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .config import CACHE_DIR

log = logging.getLogger("gasdesk.cache")


@dataclass
class Entry:
    value: Any
    ts: float
    ttl: float

    @property
    def fresh(self) -> bool:
        return time.time() - self.ts < self.ttl


class TTLCache:
    def __init__(self, directory: str = CACHE_DIR) -> None:
        self._mem: dict[str, Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._dir = directory
        try:
            os.makedirs(self._dir, exist_ok=True)
        except OSError:
            self._dir = ""

    def _path(self, key: str) -> str:
        return os.path.join(self._dir, hashlib.sha1(key.encode()).hexdigest() + ".json")

    def _load(self, key: str) -> Entry | None:
        if not self._dir:
            return None
        try:
            with open(self._path(key), encoding="utf-8") as f:
                raw = json.load(f)
            return Entry(raw["value"], raw["ts"], raw["ttl"])
        except (OSError, ValueError, KeyError):
            return None

    def _save(self, key: str, e: Entry) -> None:
        if not self._dir:
            return
        tmp = self._path(key) + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"value": e.value, "ts": e.ts, "ttl": e.ttl}, f, separators=(",", ":"))
            os.replace(tmp, self._path(key))
        except (OSError, TypeError, ValueError) as ex:
            log.debug("cache save failed for %s: %s", key, ex)

    def peek(self, key: str) -> Entry | None:
        e = self._mem.get(key)
        if e is None:
            e = self._load(key)
            if e is not None:
                self._mem[key] = e
        return e

    async def get(self, key: str, ttl: float, fn: Callable[[], Awaitable[Any]]) -> tuple[Any, dict]:
        """Return (value, meta). meta = {cached_at, stale, error}."""
        e = self.peek(key)
        if e is not None and e.fresh:
            return e.value, {"cached_at": e.ts, "stale": False, "error": None}
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            e = self.peek(key)
            if e is not None and e.fresh:
                return e.value, {"cached_at": e.ts, "stale": False, "error": None}
            try:
                value = await fn()
            except Exception as ex:  # noqa: BLE001 - upstream failures are expected
                log.warning("fetch failed for %s: %s", key, ex)
                if e is not None:
                    return e.value, {"cached_at": e.ts, "stale": True, "error": str(ex)[:300]}
                raise
            ne = Entry(value, time.time(), ttl)
            self._mem[key] = ne
            self._save(key, ne)
            return value, {"cached_at": ne.ts, "stale": False, "error": None}


cache = TTLCache()
