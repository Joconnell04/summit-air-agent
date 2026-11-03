"""A short-lived cache of capacity answers, so offering times doesn't wait on the FSM every turn.

Entries expire after a few seconds, every booking, reschedule or cancel clears the cache, and the
background job refreshes what is cached on a timer. The FSM still decides every booking: a stale
entry can only make the agent offer a window that then comes back as taken.
"""

import logging
import threading
import time
from collections.abc import Callable, Hashable

log = logging.getLogger(__name__)


class SlotCache:
    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl_seconds
        self.clock = clock
        self._entries: dict[Hashable, tuple[float, object, Callable[[], object]]] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable, fetch: Callable[[], object]) -> object:
        with self._lock:
            entry = self._entries.get(key)
            if entry and self.clock() - entry[0] < self.ttl:
                return entry[1]
        value = fetch()
        with self._lock:
            self._entries[key] = (self.clock(), value, fetch)
        return value

    def invalidate(self) -> None:
        with self._lock:
            self._entries.clear()

    def refresh(self) -> int:
        """Fetch every cached entry again. Returns how many were refreshed."""
        with self._lock:
            items = list(self._entries.items())
        refreshed = 0
        for key, (_, _, fetch) in items:
            try:
                value = fetch()
            except Exception as exc:  # noqa: BLE001 - a failed refresh leaves the entry to expire
                log.warning("slot cache refresh failed for %s: %s", key, exc)
                continue
            with self._lock:
                self._entries[key] = (self.clock(), value, fetch)
            refreshed += 1
        return refreshed

    def __len__(self) -> int:
        return len(self._entries)
