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


# An entry nobody has read for this long isn't refreshed any more, and is dropped: refreshing every
# job type and day ever asked for kept the mock's capacity call (600 ms each, MEASURED) busy on
# every jobs tick, behind which a live call's find_slots waited.
IDLE_SECONDS = 600


class SlotCache:
    def __init__(
        self,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        background: bool = False,
    ):
        self.ttl = ttl_seconds
        self.clock = clock
        # Fetch again off the request after a booking clears the cache, so the next find_slots
        # finds it warm (on a server running its background jobs; tests leave it off).
        self.background = background
        # key: (fetched at, value, fetch, last read)
        self._entries: dict[Hashable, tuple[float, object, Callable[[], object], float]] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable, fetch: Callable[[], object]) -> object:
        with self._lock:
            entry = self._entries.get(key)
            if entry and self.clock() - entry[0] < self.ttl:
                self._entries[key] = (*entry[:3], self.clock())
                return entry[1]
        value = fetch()
        with self._lock:
            now = self.clock()
            self._entries[key] = (now, value, fetch, now)
        return value

    def invalidate(self) -> None:
        with self._lock:
            items = list(self._entries.items())
            self._entries.clear()
        if self.background and self.ttl > 0 and items:
            threading.Thread(target=self._refetch, args=(items,), daemon=True).start()

    def _refetch(self, items: list) -> None:
        for key, (_, _, fetch, used) in items:
            try:
                value = fetch()
            except Exception as exc:  # noqa: BLE001 - find_slots fetches it if this fails
                log.warning("slot cache refetch failed for %s: %s", key, exc)
                continue
            with self._lock:
                self._entries.setdefault(key, (self.clock(), value, fetch, used))

    def refresh(self) -> int:
        """Fetch again every entry read in the last IDLE_SECONDS, and drop the rest. Returns how
        many were refreshed."""
        with self._lock:
            now = self.clock()
            for key in [k for k, e in self._entries.items() if now - e[3] > IDLE_SECONDS]:
                del self._entries[key]
            items = list(self._entries.items())
        refreshed = 0
        for key, (_, _, fetch, used) in items:
            try:
                value = fetch()
            except Exception as exc:  # noqa: BLE001 - a failed refresh leaves the entry to expire
                log.warning("slot cache refresh failed for %s: %s", key, exc)
                continue
            with self._lock:
                self._entries[key] = (self.clock(), value, fetch, used)
            refreshed += 1
        return refreshed

    def __len__(self) -> int:
        return len(self._entries)
