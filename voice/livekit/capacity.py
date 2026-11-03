"""The capacity cap: how many calls this server takes at once (docs/scaling.md, spec S-47).

LiveKit runs each call in its own process, so the count lives on disk: one file per call in a
folder, taken and freed under a file lock. A call that dies without freeing its line is cleared
after `stale_s`.
"""

import fcntl
import time
from pathlib import Path


class Lines:
    def __init__(self, folder: Path, cap: int, stale_s: float = 3600.0):
        self.folder = Path(folder)
        self.cap = cap
        self.stale_s = stale_s
        self.folder.mkdir(parents=True, exist_ok=True)

    def _locked(self):
        handle = open(self.folder / ".lock", "w")  # noqa: SIM115 (closed in take and free)
        fcntl.flock(handle, fcntl.LOCK_EX)
        return handle

    def _active(self, now: float) -> list[Path]:
        active = []
        for line in self.folder.glob("call-*"):
            if now - line.stat().st_mtime > self.stale_s:
                line.unlink(missing_ok=True)
            else:
                active.append(line)
        return active

    def take(self, call_id: str) -> bool:
        """True if the call got a line; False means every line is busy."""
        handle = self._locked()
        try:
            if len(self._active(time.time())) >= self.cap:
                return False
            (self.folder / f"call-{_safe(call_id)}").touch()
            return True
        finally:
            handle.close()

    def free(self, call_id: str) -> None:
        handle = self._locked()
        try:
            (self.folder / f"call-{_safe(call_id)}").unlink(missing_ok=True)
        finally:
            handle.close()

    def in_use(self) -> int:
        handle = self._locked()
        try:
            return len(self._active(time.time()))
        finally:
            handle.close()


def _safe(call_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in call_id)
