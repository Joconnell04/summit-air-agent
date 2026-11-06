"""A shifted clock for the eval and server-led worlds, so a run at night can play a daytime call
(the side-by-side's scripted runs were all after 8 PM, so the daytime booking path went untested).

SUMMIT_CLOCK_OFFSET_H moves "now" by that many hours, and only when SUMMIT_WORLD names a world that
isn't the live one ("eval" or "server-led"). Anywhere else, the live world included, it is ignored
with a warning: the live world's calls are real and its clock never moves. The mock ServiceTitan
reads the same two variables (servicetitan_mock/app.py), so both sides agree on the time.
"""

import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

SHIFTABLE_WORLDS = ("eval", "server-led")
log = logging.getLogger(__name__)


def shifted(env: dict[str, str] | None = None) -> Callable[[], datetime] | None:
    """The shifted clock, or None for the real one."""
    env = os.environ if env is None else env
    raw = env.get("SUMMIT_CLOCK_OFFSET_H", "").strip()
    if not raw:
        return None
    world = env.get("SUMMIT_WORLD", "").strip()
    if world not in SHIFTABLE_WORLDS:
        log.warning(
            "SUMMIT_CLOCK_OFFSET_H ignored: SUMMIT_WORLD is %r, not one of %s",
            world, ", ".join(SHIFTABLE_WORLDS),
        )
        return None
    offset = timedelta(hours=float(raw))
    return lambda: datetime.now(UTC) + offset
