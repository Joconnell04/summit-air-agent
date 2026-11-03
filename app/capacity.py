"""The company's rules on top of ServiceTitan's capacity.

ServiceTitan answers with time frames and, for each, the named technicians who are free. It does
not know that Summit Air keeps some technicians back for urgent jobs, or that the on-call
technician's night window is for urgent jobs only. Those rules are applied here, to what
ServiceTitan returns, both when times are offered and again right before a job is created.
"""

import random
from datetime import date, datetime

from .db import parse_iso
from .tenant import Tenant, Window


def usable_technicians(
    tenant: Tenant, frame: dict, day: date, window: Window, level: str
) -> list[int]:
    """Ids of the technicians a job of this priority may take in the frame, in ServiceTitan's order.

    Urgent jobs may take any free technician with the skills. Others may not use the after-hours
    window, and leave the held technicians free: of the technicians free in the window (whatever
    their skills), the held number stay back, so the ones with the skills that remain are usable."""
    free = [t for t in frame["technicians"] if t["status"] == "Available"]
    fit = [t["id"] for t in free if t.get("hasRequiredSkills")]
    if level == "Urgent":
        return fit
    if window.after_hours:
        return []
    held = tenant.hold_for(day, window.id, frame["businessUnitIds"][0])
    return fit[: max(0, len(free) - held)]


def _window_of(tenant: Tenant, frame: dict) -> tuple[date, Window] | None:
    """The tenant's arrival window a time frame is, or None for a frame that is not one of them."""
    start, end = parse_iso(frame["startUtc"]), parse_iso(frame["endUtc"])
    day = tenant.local(start).date()
    for window in tenant.windows:
        if (start, end) == tenant.window_bounds(day, window):
            return day, window
    return None


def open_windows(
    tenant: Tenant, frames: list[dict], level: str, first_day: date, last_day: date
) -> list[dict]:
    """The windows a job of this priority could be booked into, earliest first."""
    found = []
    for frame in frames:
        matched = _window_of(tenant, frame)
        if not matched or not first_day <= matched[0] <= last_day:
            continue
        day, window = matched
        technicians = usable_technicians(tenant, frame, day, window, level)
        if technicians:
            found.append(
                {
                    "date": day.isoformat(),
                    "window": window.id,
                    "after_hours": window.after_hours,
                    "start": frame["startUtc"],
                    "end": frame["endUtc"],
                    "technician_ids": technicians,
                }
            )
    return sorted(found, key=lambda w: w["start"])


def pick_technician(
    tenant: Tenant,
    frames: list[dict],
    start: datetime,
    end: datetime,
    level: str,
    rng=random,
) -> int | None:
    """A technician to put on a job in exactly this window, or None when no one may take it. Any of
    the usable ones will do, so it picks at random: callers booking the same window at the same
    moment then rarely reach for the same technician, and spread the day's work as they go (the
    order ServiceTitan lists technicians in is not something to rely on)."""
    for frame in frames:
        if (parse_iso(frame["startUtc"]), parse_iso(frame["endUtc"])) != (start, end):
            continue
        matched = _window_of(tenant, frame)
        if matched:
            technicians = usable_technicians(tenant, frame, matched[0], matched[1], level)
            if technicians:
                return rng.choice(technicians)
    return None
