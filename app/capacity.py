"""The company's rules on top of ServiceTitan's capacity.

ServiceTitan answers with time frames and, for each, the named technicians who are free. Here the
frames are the company's 30-minute blocks (tenants/<name>/config.yaml scheduling), and an arrival
window is any run of them: a technician can take the window when free in every block of it. It
does not know that Summit Air keeps some technicians back for urgent jobs, or that the on-call
technician's night window is for urgent jobs only. Those rules are applied here, to what
ServiceTitan returns, both when times are offered and again right before a job is created.
"""

import random
from datetime import date, datetime, time, timedelta

from .db import iso, parse_iso
from .tenant import Tenant, Window, window_of


def usable_technicians(
    tenant: Tenant, frame: dict, day: date, window: Window, level: str
) -> list[int]:
    """Ids of the technicians a job of this priority may take in the frame, in ServiceTitan's order.

    Urgent jobs may take any free technician with the skills. Others may not use the after-hours
    window, and leave the held technicians free: of the technicians free in the frame (whatever
    their skills), the held number stay back, so the ones with the skills that remain are usable."""
    free = [t for t in frame["technicians"] if t["status"] == "Available"]
    fit = [t["id"] for t in free if t.get("hasRequiredSkills")]
    if level == "Urgent":
        return fit
    if window.after_hours:
        return []
    held = tenant.hold_for(day, window.id, frame["businessUnitIds"][0])
    return fit[: max(0, len(free) - held)]


def _night(tenant: Tenant, frame: dict) -> tuple[date, Window] | None:
    """The night window a frame is, or None."""
    start, end = parse_iso(frame["startUtc"]), parse_iso(frame["endUtc"])
    day = tenant.local(start).date()
    for window in tenant.windows:
        if window.after_hours and (start, end) == tenant.window_bounds(day, window):
            return day, window
    return None


def _blocks_by_day(tenant: Tenant, frames: list[dict]) -> dict[date, list[dict]]:
    """The daytime block frames, by local day, in time order."""
    days: dict[date, list[dict]] = {}
    for frame in frames:
        if _night(tenant, frame):
            continue
        start = tenant.local(parse_iso(frame["startUtc"]))
        days.setdefault(start.date(), []).append(frame)
    for blocks in days.values():
        blocks.sort(key=lambda f: f["startUtc"])
    return days


def open_windows(
    tenant: Tenant,
    frames: list[dict],
    level: str,
    first_day: date,
    last_day: date,
    minutes: int | None = None,
    start_at: time | None = None,
) -> list[dict]:
    """The windows a job of this priority could be booked into, earliest first: every run of
    `minutes` (the tenant's window length by default) starting on a block, or with `start_at`, the
    run starting then, cut short at closing time ("7:30" gives 7:30 to 8). The night window too."""
    length = minutes or tenant.window_minutes
    found = []
    for frame in frames:
        night = _night(tenant, frame)
        if not night or not first_day <= night[0] <= last_day:
            continue
        technicians = usable_technicians(tenant, frame, night[0], night[1], level)
        if technicians:
            found.append(_found(night[0], night[1], frame["startUtc"], frame["endUtc"], technicians))
    for day, blocks in _blocks_by_day(tenant, frames).items():
        if not first_day <= day <= last_day:
            continue
        for i, first in enumerate(blocks):
            begin = tenant.local(parse_iso(first["startUtc"]))
            if start_at is not None and begin.time() != start_at:
                continue
            run = _run(blocks, i, begin + timedelta(minutes=length))
            if not run or (start_at is None and tenant.local(parse_iso(run[-1]["endUtc"]))
                           < begin + timedelta(minutes=length)):
                continue
            end = tenant.local(parse_iso(run[-1]["endUtc"]))
            window = window_of(begin.time(), end.time())
            technicians = None
            for block in run:
                usable = usable_technicians(tenant, block, day, window, level)
                technicians = usable if technicians is None else [
                    t for t in technicians if t in usable
                ]
            if technicians:
                found.append(_found(day, window, first["startUtc"], run[-1]["endUtc"], technicians))
    return sorted(found, key=lambda w: (w["start"], w["end"]))


def _run(blocks: list[dict], i: int, until: datetime) -> list[dict]:
    """Consecutive blocks from blocks[i] up to `until`, stopping at a gap."""
    run = [blocks[i]]
    for block in blocks[i + 1:]:
        if parse_iso(block["startUtc"]) != parse_iso(run[-1]["endUtc"]):
            break
        if parse_iso(block["endUtc"]) > until:
            break
        run.append(block)
    return run


def _found(day: date, window: Window, start: str, end: str, technicians: list[int]) -> dict:
    return {
        "date": day.isoformat(),
        "window": window.id,
        "after_hours": window.after_hours,
        "start": iso(parse_iso(start)),
        "end": iso(parse_iso(end)),
        "technician_ids": technicians,
    }


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
    local = tenant.local(start)
    minutes = int((end - start).total_seconds() // 60)
    for window in open_windows(
        tenant, frames, level, local.date(), local.date(), minutes, local.time()
    ):
        if (parse_iso(window["start"]), parse_iso(window["end"])) == (start, end):
            return rng.choice(window["technician_ids"])
    # The night window starts the evening before the morning it ends.
    for window in open_windows(tenant, frames, level, local.date() - timedelta(days=1),
                               local.date()):
        if window["after_hours"] and (parse_iso(window["start"]), parse_iso(window["end"])) == (
            start, end
        ):
            return rng.choice(window["technician_ids"])
    return None
