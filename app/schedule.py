"""The schedule as ServiceTitan holds it, and the edits a person makes to it from the owner view or
the bench (issue #152): block a window, add an event or a job, change a job, change a customer's
name or phone number, and from the bench only, cancel a job (issue #191).

Everything here reads and writes through the FSM adapter the tools use, never the mock's database,
so the next find_slots, find_customer or call start sees the change. Each write clears the slot
cache, which otherwise keeps capacity for up to SLOT_CACHE_SECONDS (app/fsm/cache.py); the call
start and find_customer read ServiceTitan live and have no cache to clear.

Every write, taken or refused, is logged in schedule_edits with who made it. Nothing here deletes a
record or changes more than the one window, job or customer asked for: a block is taken off the
schedule and a job is cancelled, neither deleted, and a whole-shop block is one block per
technician on shift in that window.
Who may make which edit is checked here as well as in the routes.
"""

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from . import tools
from .db import iso, parse_iso, tx
from .fsm import FsmAdapter, FsmRejected, FsmUnavailable, SlotCache
from .lookup import OPEN_JOB_STATUSES
from .tenant import Tenant, Window, window_of
from .text import phone_display, phone_key

NAME_MAX = 60
NOTES_MAX = 1000
BLOCK_NAME = "Blocked"
REF_LINE = re.compile(r"^Ref: [0-9a-f]{12}$", re.MULTILINE)
# The priorities the agent's offers are read at for the schedule's "open to book" line.
AGENT_LEVELS = ("Normal", "Urgent")


class EditError(Exception):
    """An edit that was refused, with the words to show the person who made it. code names a
    refusal the page offers a next step for; pending is what that step needs."""

    def __init__(self, message: str, code: str | None = None, pending: dict | None = None):
        super().__init__(message)
        self.code = code
        self.pending = pending


@dataclass
class Who:
    user_id: int | None
    name: str
    role: str
    surface: str  # "owner" or "bench"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# ---- reading the day ---------------------------------------------------------------------------


def _day_range(tenant: Tenant, day: date) -> tuple[datetime, datetime]:
    """From midnight to the end of the day's last window (the night window ends the next morning)."""
    start = datetime.combine(day, time(0), tenant.tz)
    end = max(tenant.window_bounds(day, w)[1] for w in tenant.windows)
    return start, max(end, start + timedelta(days=1))


def _window_at(tenant: Tenant, day: date, start: str, end: str) -> Window | None:
    """The window an appointment is in: a configured one, or the run of blocks it covers."""
    for window in tenant.windows:
        if tenant.window_bounds(day, window) == (parse_iso(start), parse_iso(end)):
            return window
    local_start, local_end = tenant.local(parse_iso(start)), tenant.local(parse_iso(end))
    if local_start.date() == day == local_end.date() and local_start < local_end:
        return window_of(local_start.time(), local_end.time())
    return None


def _frames(fsm: FsmAdapter, tenant: Tenant, day: date) -> list[dict]:
    """ServiceTitan's time frames for the day, one per window and business unit."""
    start, end = _day_range(tenant, day)
    units: dict[int, int] = {}
    for job_type in tenant.job_types.values():
        for unit in job_type["businessUnitIds"]:
            units.setdefault(unit, job_type["id"])
    frames = []
    for unit, job_type_id in sorted(units.items()):
        frames += fsm.capacity(start, end, unit, job_type_id)
    return frames


def _bounds(frame: dict) -> tuple[datetime, datetime]:
    return parse_iso(frame["startUtc"]), parse_iso(frame["endUtc"])


def _inside(frames: list[dict], start: datetime, end: datetime) -> list[dict]:
    """The frames inside a window: its 30-minute blocks, or the night window's own frame."""
    return [f for f in frames if start <= _bounds(f)[0] and _bounds(f)[1] <= end]


def _free_all_through(frames: list[dict], skills: bool = False) -> list[int]:
    """Technicians available in every one of their business unit's frames (with the job's skills,
    if asked)."""
    by_unit: dict[int, set[int] | None] = {}
    for frame in frames:
        unit = frame["businessUnitIds"][0]
        here = {
            t["id"] for t in frame["technicians"]
            if t["status"] == "Available" and (not skills or t.get("hasRequiredSkills"))
        }
        seen = by_unit.get(unit)
        by_unit[unit] = here if seen is None else seen & here
    return sorted(set().union(*(free for free in by_unit.values() if free)))


def typical_job_type(tenant: Tenant, level: str) -> dict | None:
    """The first job type whose own priority is this level, standing for every job at it."""
    return next((j for j in tenant.job_types.values() if j.get("priority") == level), None)


def notes_of(summary: str) -> str:
    """A job's summary without the reference lines the adapter finds it again by."""
    return REF_LINE.sub("", summary or "").strip()


def _with_refs(old_summary: str, notes: str) -> str:
    refs = REF_LINE.findall(old_summary or "")
    return "\n".join([notes.strip(), *refs]).strip()


def day(
    fsm: FsmAdapter, tenant: Tenant, cache: SlotCache, on: date, now: datetime
) -> dict:
    """The day as a grid: a row per technician on shift, a column per arrival window, and in each
    cell the jobs and blocks ServiceTitan has there. Also what the agent would offer for that day
    right now, read the way find_slots reads it."""
    frames = _frames(fsm, tenant, on)
    windows, staff, on_shift = [], {}, set()
    for window in tenant.windows:
        start, end = tenant.window_bounds(on, window)
        # The 30-minute blocks inside the window (app/capacity.py), or the night window's frame.
        frame_list = [f for f in frames if start <= _bounds(f)[0] and _bounds(f)[1] <= end]
        if not frame_list:
            continue
        windows.append(
            {
                "id": window.id,
                "label": window.label(),
                "after_hours": window.after_hours,
                "start": start,
                "end": end,
                "started": start <= now,
            }
        )
        for frame in frame_list:
            for tech in frame["technicians"]:
                staff[tech["id"]] = tech["name"]
                on_shift.add((tech["id"], window.id))

    day_start, day_end = _day_range(tenant, on)
    appointments = [
        a for a in fsm.appointments_between(day_start, day_end) if a["status"] != "Canceled"
    ]
    assigned = fsm.assignments([a["id"] for a in appointments]) if appointments else []
    tech_of = {a["appointmentId"]: a["technicianId"] for a in assigned}
    customers: dict[int, dict] = {}
    cells: dict[tuple[int, str], dict] = {}

    def cell(tech_id: int, window_id: str) -> dict:
        return cells.setdefault((tech_id, window_id), {"jobs": [], "blocks": []})

    for appt in appointments:
        a_start, a_end = parse_iso(appt["arrivalWindowStart"]), parse_iso(appt["arrivalWindowEnd"])
        tech_id = tech_of.get(appt["id"])
        # A visit shows in every column its arrival window overlaps: a booking can be any run of
        # 30-minute blocks, so "8 to 12" fills two 2-hour columns and "7:30 to 8" part of one.
        covered = [w for w in windows if a_start < w["end"] and a_end > w["start"]]
        if not covered or tech_id is None:
            continue
        job = fsm.job(appt["jobId"])
        if job["customerId"] not in customers:
            customers[job["customerId"]] = fsm.customer(job["customerId"])
        staff.setdefault(tech_id, f"Technician {tech_id}")
        for window in covered:
            cell(tech_id, window["id"])["jobs"].append(
                {
                    "job_id": job["id"],
                    "number": job["jobNumber"],
                    "type": tenant.job_type_codes.get(job["jobTypeId"], ""),
                    "priority": job["priority"],
                    "customer": customers[job["customerId"]]["name"],
                    "status": job["jobStatus"],
                }
            )

    for block in fsm.non_job_appointments(day_start, day_end):
        b_start = parse_iso(block["start"])
        b_end = b_start + _span(block["duration"])
        staff.setdefault(block["technicianId"], f"Technician {block['technicianId']}")
        for window in windows:
            if b_start < window["end"] and b_end > window["start"]:
                cell(block["technicianId"], window["id"])["blocks"].append(
                    {
                        "id": block["id"],
                        "name": block["name"],
                        "ref": _ref(block["summary"]),
                        "ours": MADE_HERE in (block["summary"] or ""),
                    }
                )

    rows = []
    for tech_id, name in sorted(staff.items(), key=lambda t: t[1]):
        row = []
        for window in windows:
            found = cells.get((tech_id, window["id"]), {"jobs": [], "blocks": []})
            state = (
                "job" if found["jobs"] else
                "block" if found["blocks"] else
                "free" if (tech_id, window["id"]) in on_shift else
                "off"
            )
            row.append({"window": window["id"], "state": state, **found})
        rows.append({"id": tech_id, "name": name, "cells": row})

    # find_slots offers arrival windows of the company's window length (half an hour for Summit
    # Air), not the grid's columns: each column counts the offered windows that start inside it
    # and the technician openings across them.
    for window in windows:
        window["offered"], window["openings"] = {}, {}
    for level in AGENT_LEVELS:
        job_type = typical_job_type(tenant, level)
        if job_type is None:
            continue
        for w in tools.open_windows(tenant, fsm, cache, now, job_type, level):
            if w["date"] != on.isoformat():
                continue
            begins = parse_iso(w["start"])
            column = next((c for c in windows if c["start"] <= begins < c["end"]), None)
            if column is None:
                continue
            column["offered"][level] = column["offered"].get(level, 0) + 1
            column["openings"][level] = (
                column["openings"].get(level, 0) + len(w["technician_ids"])
            )
    groups: dict[str, int] = {}
    for row in rows:
        for c in row["cells"]:
            for b in c["blocks"]:
                if b["ref"]:
                    groups[b["ref"]] = groups.get(b["ref"], 0) + 1
    return {"day": on, "windows": windows, "rows": rows, "groups": groups}


def offers(g: dict) -> dict:
    """What find_slots would offer on the grid's day at normal priority: the arrival windows it
    offers, and the technician openings across them (a window two technicians could take is two)."""
    # A column that has started can still hold windows later in it; find_slots drops the past.
    return {
        "windows": sum(w["offered"].get("Normal", 0) for w in g["windows"]),
        "openings": sum(w["openings"].get("Normal", 0) for w in g["windows"]),
    }

def _span(value: str) -> timedelta:
    """ServiceTitan's time span, [d.]hh:mm:ss."""
    days, clock = value.split(".", 1) if "." in value else ("0", value)
    hours, minutes, *seconds = (int(p) for p in clock.split(":"))
    return timedelta(days=int(days), hours=hours, minutes=minutes, seconds=seconds[0] if seconds else 0)


def _ref(summary: str) -> str:
    found = REF_LINE.search(summary or "")
    return found.group(0)[5:] if found else ""


def job_detail(fsm: FsmAdapter, tenant: Tenant, job_id: int, now: datetime) -> dict:
    """A job with what the edit forms need: its window, location and customer with phone numbers."""
    job = fsm.job(job_id)
    appointments = [a for a in fsm.appointments(job_id) if a["status"] != "Canceled"]
    appt = appointments[-1] if appointments else None
    location = fsm.location(job["locationId"])
    customer = fsm.customer(job["customerId"])
    phones = [c for c in fsm.contacts(job["customerId"]) if c["type"] in ("Phone", "MobilePhone")]
    window_id, window_day = None, None
    if appt:
        start = parse_iso(appt["arrivalWindowStart"])
        window_day = tenant.local(start).date()
        found = _window_at(tenant, window_day, appt["arrivalWindowStart"], appt["arrivalWindowEnd"])
        window_id = found.id if found else None
    today = tenant.local(now).date()
    choices = [
        {"value": f"{d.isoformat()}|{w.id}", "label": f"{d.strftime('%a %b %-d')}, {w.label()}"}
        for d in (today + timedelta(days=n) for n in range(tools.BOOKING_DAYS_AHEAD + 1))
        for w in tenant.windows
        if tenant.window_bounds(d, w)[0] > now or (d == window_day and w.id == window_id)
    ]
    return {
        "job_id": job["id"],
        "number": job["jobNumber"],
        "status": job["jobStatus"],
        "open": job["jobStatus"] in OPEN_JOB_STATUSES,
        "type": tenant.job_type_codes.get(job["jobTypeId"], ""),
        "priority": job["priority"],
        "notes": notes_of(job["summary"]),
        "window": f"{window_day.isoformat()}|{window_id}" if window_id else "",
        "appointment_id": appt["id"] if appt else None,
        "choices": choices,
        "address": location["address"],
        "location_id": location["id"],
        "customer": {
            "id": customer["id"],
            "name": customer["name"],
            "phones": [
                {"id": p["id"], "value": phone_display(p["value"]), "memo": p.get("memo") or ""}
                for p in phones
            ],
        },
    }


def customer_detail(fsm: FsmAdapter, customer_id: int) -> dict:
    customer = fsm.customer(customer_id)
    phones = [c for c in fsm.contacts(customer_id) if c["type"] in ("Phone", "MobilePhone")]
    return {
        "id": customer["id"],
        "name": customer["name"],
        "address": customer.get("address"),
        "phones": [
            {"id": p["id"], "value": phone_display(p["value"]), "memo": p.get("memo") or ""}
            for p in phones
        ],
    }


# ---- the edit log ------------------------------------------------------------------------------


def _log(
    conn: sqlite3.Connection,
    tenant: Tenant,
    who: Who,
    now: datetime,
    action: str,
    target: str,
    detail: str,
    error: str | None = None,
    modified: str | None = None,
    code: str | None = None,
    pending: dict | None = None,
) -> None:
    with tx(conn):
        conn.execute(
            """INSERT INTO schedule_edits (tenant, at, user_id, author, role, surface, action,
                   target, detail, ok, error, code, pending_json, fsm_modified_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tenant.slug, iso(now), who.user_id, who.name, who.role, who.surface, action, target,
             detail, int(error is None), error, code, json.dumps(pending) if pending else None,
             modified),
        )


def changes(conn: sqlite3.Connection, tenant: Tenant, limit: int = 40) -> list[dict]:
    """What changed in ServiceTitan, newest first: edits made here and the agent's bookings,
    reschedules and cancels, each with when it happened and who made it."""
    edits = [
        {
            "at": r["at"],
            "by": r["author"],
            "role": r["role"],
            "where": r["surface"],
            "what": r["detail"],
            "ok": bool(r["ok"]),
            "error": r["error"],
            "landed": r["fsm_modified_on"],
            "call_id": None,
        }
        for r in conn.execute(
            "SELECT * FROM schedule_edits WHERE tenant = ? ORDER BY id DESC LIMIT ?",
            (tenant.slug, limit),
        )
    ]
    verbs = {"book": "Booked", "reschedule": "Moved", "cancel": "Cancelled"}
    agent = [
        {
            "at": r["updated_at"],
            "by": "The agent",
            "role": "agent",
            "where": "call",
            "what": f"{verbs[r['kind']]} job {r['fsm_job_id']}"
            + (f", {r['job_type']}" if r["job_type"] else ""),
            "ok": True,
            "error": None,
            "landed": None,
            "call_id": r["call_id"],
        }
        for r in conn.execute(
            """SELECT b.* FROM bookings b JOIN calls c ON c.id = b.call_id
               WHERE c.tenant = ? AND b.fsm_job_id IS NOT NULL AND b.status IN ('booked', 'done')
               ORDER BY b.updated_at DESC LIMIT ?""",
            (tenant.slug, limit),
        )
    ]
    return sorted(edits + agent, key=lambda c: c["at"], reverse=True)[:limit]


# ---- edits -------------------------------------------------------------------------------------


def _key() -> str:
    return f"edit:{uuid.uuid4().hex}"


def _plain(exc: Exception) -> str:
    if isinstance(exc, FsmUnavailable):
        return "ServiceTitan isn't answering. Try again in a minute."
    if isinstance(exc, FsmRejected):
        return {
            "slot_taken": "That window is full now.",
            "not_found": "ServiceTitan doesn't have that record.",
            "unauthorized": "ServiceTitan refused the sign-in.",
        }.get(exc.code, exc.detail or "ServiceTitan refused the change.")
    return str(exc)


def _when(tenant: Tenant, on: date, window_id: str) -> str:
    """A window as the log and the page say it, for any input, even one about to be refused."""
    window = tenant.window(window_id)
    label = window.label() if window else window_id or "no window"
    return f"{on.strftime('%a %b %-d')}, {label}" if on != date.min else label


def _window_from(
    tenant: Tenant, on: date, window_id: str, now: datetime
) -> tuple[Window, datetime, datetime]:
    if on == date.min:
        raise EditError("Pick a day.")
    window = tenant.window(window_id)
    if window is None:
        raise EditError("No such arrival window.")
    start, end = tenant.window_bounds(on, window)
    if start <= now:
        raise EditError("That window has already started.")
    return window, start, end


def _text(value: str, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def _run(conn, tenant, cache, who, now, action, target, detail, write):
    """Make one edit: run the writes, clear the slot cache, log what happened. write returns its
    result, the time ServiceTitan says the record changed and, optionally, a fuller detail for the
    log. Raises EditError with plain words when ServiceTitan refused or didn't answer."""
    try:
        result, modified, *fuller = write()
    except EditError as exc:
        _log(conn, tenant, who, now, action, target, detail, str(exc), None, exc.code, exc.pending)
        raise
    except (FsmRejected, FsmUnavailable) as exc:
        cache.invalidate()  # a write may have gone through before the one that failed
        _log(conn, tenant, who, now, action, target, detail, _plain(exc))
        raise EditError(_plain(exc)) from exc
    cache.invalidate()
    _log(conn, tenant, who, now, action, target, fuller[0] if fuller else detail, None, modified)
    return result


def block(
    conn, fsm: FsmAdapter, cache: SlotCache, tenant: Tenant, who: Who, now: datetime,
    on: date, window_id: str, technician_id: int | None, name: str = "",
) -> int:
    """Take one technician, or every technician free in the window, off jobs for that window. A
    name makes it an event ("Team meeting"); without one it reads "Blocked". A technician with a
    visit or a block there is refused; for a whole window, the visits stay and the log says how
    many. Returns how many technicians it took."""
    title = _text(name, NAME_MAX) or BLOCK_NAME
    when = _when(tenant, on, window_id)
    whom = f"technician {technician_id}" if technician_id else "every free technician"
    verb = "Blocked" if title == BLOCK_NAME else f"Added {title} for"
    detail = f"{verb} {whom}, {when}"

    def write():
        _, start, end = _window_from(tenant, on, window_id, now)
        frames = _inside(_frames(fsm, tenant, on), start, end)
        names = {t["id"]: t["name"] for f in frames for t in f["technicians"]}
        free = _free_all_through(frames)
        visits = _visits(fsm, start, end)
        if technician_id is not None:
            name_of = names.get(technician_id, f"Technician {technician_id}")
            if technician_id not in names:
                raise EditError("That technician isn't working then.")
            if technician_id in visits:
                raise EditError(f"{name_of} has a visit then. Move the job first.")
            if technician_id not in free:
                raise EditError(f"{name_of} is already blocked then.")
            wanted = [technician_id]
        else:
            wanted = free
        if not wanted:
            raise EditError("Nobody is free in that window.")
        key = _key()
        made = [
            fsm.create_non_job_appointment(key, tech, start, end, title, _made_here(who))
            for tech in wanted
        ]
        if technician_id is not None:
            fuller = f"{verb} {names.get(technician_id)}, {when}"
        else:
            staying = len(set(names) & set(visits))
            fuller = f"{verb} {len(made)} free technicians, {when}" + (
                f"; {staying} booked visit{'s' if staying != 1 else ''} stay" if staying else ""
            )
        return len(made), made[-1].get("modifiedOn"), fuller

    return _run(conn, tenant, cache, who, now, "block", when, detail, write)


def _visits(fsm: FsmAdapter, start: datetime, end: datetime) -> set[int]:
    """Technicians with a live appointment overlapping this window."""
    appointments = [
        a for a in fsm.appointments_between(start, end)
        if a["status"] != "Canceled"
        and parse_iso(a["arrivalWindowStart"]) < end
        and parse_iso(a["arrivalWindowEnd"]) > start
    ]
    if not appointments:
        return set()
    return {a["technicianId"] for a in fsm.assignments([a["id"] for a in appointments])}


MADE_HERE = "Added on the"


def _made_here(who: Who) -> str:
    """The summary a block made here carries, so unblock can tell it from one made in ServiceTitan."""
    return f"{MADE_HERE} {'bench' if who.surface == 'bench' else 'owner view'} by {who.name}"


def unblock(
    conn, fsm: FsmAdapter, cache: SlotCache, tenant: Tenant, who: Who, now: datetime,
    non_job_id: int, whole_group: bool = False, on: date | None = None,
) -> int:
    """Take a block made here off the schedule; with whole_group, every block the same edit made
    that day. A block made in ServiceTitan itself (a technician's training, say) is left alone."""
    detail = f"Took block {non_job_id} off the schedule" + (" with its group" if whole_group else "")

    def write():
        if on is None or on == date.min:
            raise EditError("Pick a day.")
        start, end = _day_range(tenant, on)
        blocks = fsm.non_job_appointments(start, end)
        mine = next((b for b in blocks if b["id"] == non_job_id), None)
        if mine is None:
            raise EditError("That block isn't on this day's schedule.")
        ref = _ref(mine["summary"])
        if not ref or MADE_HERE not in (mine["summary"] or ""):
            raise EditError("That block was made in ServiceTitan. Change it there.")
        ids = [non_job_id]
        if whole_group:
            ids = [b["id"] for b in blocks if _ref(b["summary"]) == ref]
        ended = [fsm.end_non_job_appointment(_key(), i) for i in ids]
        return len(ended), ended[-1].get("modifiedOn")

    return _run(conn, tenant, cache, who, now, "unblock", f"block {non_job_id}", detail, write)


def add_job(
    conn, fsm: FsmAdapter, cache: SlotCache, tenant: Tenant, who: Who, now: datetime,
    on: date, window_id: str, phone: str, job_type_code: str, notes: str,
    technician_id: int | None = None, customer_id: int | None = None,
) -> int:
    """Put a job for the customer with this phone number in the window, at their first service
    location. When the number is on more than one customer, the person picks one (customer_id);
    the first is never taken for them. The person chose the window, so the company's urgent
    holds (app/capacity.py) don't apply: any free technician with the skills may take it.
    Returns the job id."""
    job_type = tenant.job_types.get(job_type_code)
    when = _when(tenant, on, window_id)
    detail = f"Added a {job_type['name'].lower() if job_type else 'new'} job, {when}"
    digits = phone_key(phone)

    def write():
        if job_type is None:
            raise EditError("Pick a job type.")
        _, start, end = _window_from(tenant, on, window_id, now)
        if customer_id is not None:
            customer = fsm.customer(customer_id)
        else:
            if len(digits) != 10:
                raise EditError("Enter the customer's 10-digit phone number.")
            found = fsm.customers_by_phone(digits)
            if not found:
                raise EditError("No customer has that phone number.")
            if len(found) > 1:
                pending = {
                    "day": on.isoformat(), "window": window_id, "technician": technician_id,
                    "job_type": job_type_code, "notes": _text(notes, NOTES_MAX),
                    "customers": [
                        {"id": c["id"], "name": c["name"],
                         "street": (c.get("address") or {}).get("street", "")}
                        for c in found[:10]
                    ],
                }
                raise EditError(
                    f"{len(found)} customers have that number. Pick one.", "pick_customer", pending
                )
            customer = found[0]
        locations = fsm.locations(customer["id"])
        if not locations:
            raise EditError("That customer has no service location.")
        unit = job_type["businessUnitIds"][0]
        frames = fsm.capacity(start, end, unit, job_type["id"])
        free = _free_all_through(_inside(frames, start, end), skills=True)
        if technician_id is not None:
            if technician_id not in free:
                raise EditError("That technician isn't free for this job then.")
            free = [technician_id]
        if not free:
            raise EditError("Nobody with the skills is free in that window.")
        summary = "; ".join(p for p in (_text(notes, NOTES_MAX), f"Added by {who.name}") if p)
        job = fsm.create_job(
            _key(), customer["id"], locations[0]["id"], unit, job_type["id"],
            job_type.get("priority", "Normal"), tenant.fsm["campaign_id"], summary, start, end,
            free[:1],
        )
        return (
            job["id"],
            job.get("modifiedOn"),
            f"Added a {job_type['name'].lower()} job for {customer['name']}, {when}",
        )

    return _run(conn, tenant, cache, who, now, "add_job", when, detail, write)


def edit_job(
    conn, fsm: FsmAdapter, cache: SlotCache, tenant: Tenant, who: Who, now: datetime,
    job_id: int, window: str = "", job_type_code: str = "", notes: str | None = None,
    address: dict | None = None,
) -> list[str]:
    """Change a job's arrival window ("2026-11-03|morning"), job type, notes or service location
    address, whichever differ from what ServiceTitan has. Returns what changed."""
    target = f"job {job_id}"

    def write():
        current = job_detail(fsm, tenant, job_id, now)
        if not current["open"]:
            raise EditError("A closed job can't change.")
        # Everything is checked before anything is sent, so a refused edit changes nothing.
        changed, patch, move, new_address = [], {}, None, None
        if job_type_code and job_type_code != current["type"]:
            job_type = tenant.job_types.get(job_type_code)
            if job_type is None:
                raise EditError("Pick a job type.")
            patch["jobTypeId"] = job_type["id"]
            changed.append(f"type to {job_type['name'].lower()}")
        if notes is not None and _text(notes, NOTES_MAX) != _text(current["notes"], NOTES_MAX):
            patch["summary"] = _with_refs(fsm.job(job_id)["summary"], notes[:NOTES_MAX])
            changed.append("notes")
        if address is not None:
            new = {k: _text(address.get(k, ""), 80) for k in ("street", "unit", "city", "zip")}
            old = {k: (current["address"].get(k) or "") for k in new}
            if new != old:
                if not new["street"] or not re.fullmatch(r"\d{5}", new["zip"]):
                    raise EditError("A service location needs a street and a 5-digit ZIP.")
                new_address = new
                changed.append("service location")
        if window and window != current["window"]:
            on, _, window_id = window.partition("|")
            try:
                day_of = date.fromisoformat(on)
            except ValueError:
                raise EditError("Pick an arrival window.") from None
            spec, start, end = _window_from(tenant, day_of, window_id, now)
            if current["appointment_id"] is None:
                raise EditError("This job has no appointment to move.")
            move = (start, end)
            changed.append(f"window to {day_of.strftime('%a %b %-d')}, {spec.label()}")
        if not changed:
            raise EditError("Nothing to change.")
        # The move goes first: it is the one ServiceTitan can still refuse (a full window).
        modified = None
        if move:
            fsm.reschedule(_key(), current["appointment_id"], *move)
        if new_address:
            modified = fsm.correct_location_address(_key(), current["location_id"], new_address).get(
                "modifiedOn"
            )
        if patch:
            modified = fsm.change_job(_key(), job_id, patch).get("modifiedOn")
        return changed, modified, f"Changed job {job_id}: " + ", ".join(changed)

    return _run(conn, tenant, cache, who, now, "edit_job", target, f"Changed job {job_id}", write)


def cancel_job(
    conn, fsm: FsmAdapter, cache: SlotCache, tenant: Tenant, who: Who, now: datetime, job_id: int
) -> str:
    """Cancel an open job in ServiceTitan with the company's cancel reason, so its window opens up
    again for find_slots. Admins only, from the bench: it is a test of the schedule, not something
    the owner view offers."""
    target = f"job {job_id}"

    def write():
        if not who.is_admin:
            raise EditError("Only an admin can cancel a job.")
        current = job_detail(fsm, tenant, job_id, now)
        if not current["open"]:
            raise EditError("That job is already closed.")
        done = fsm.cancel_job(
            _key(), job_id, tenant.fsm["cancel_reason_id"], f"Cancelled on the bench by {who.name}"
        )
        name = tenant.job_types.get(current["type"], {}).get("name", "job").lower()
        return (
            job_id,
            done.get("modifiedOn"),
            f"Cancelled job {current['number']}, {name} for {current['customer']['name']}",
        )

    return _run(conn, tenant, cache, who, now, "cancel_job", target, f"Cancelled job {job_id}", write)

def edit_customer(
    conn, fsm: FsmAdapter, cache: SlotCache, tenant: Tenant, who: Who, now: datetime,
    customer_id: int, name: str | None = None, contact_id: int | None = None,
    phone: str | None = None, confirm: bool = False,
) -> list[str]:
    """Rename a customer or change one of their phone numbers. Admins only: a wrong number sends
    the customer's calls to a stranger's record. A number already on another customer is refused
    unless confirm, since a call from it would then match both."""
    target = f"customer {customer_id}"

    def write():
        if not who.is_admin:
            raise EditError("Only an admin can change a customer.")
        current = customer_detail(fsm, customer_id)
        changed, new_phone = [], None
        new_name = _text(name or "", NAME_MAX)
        rename = name is not None and bool(new_name) and new_name != current["name"]
        if rename:
            changed.append("name")
        if phone is not None and contact_id is not None:
            mine = {p["id"]: p for p in current["phones"]}
            if contact_id not in mine:
                raise EditError("That number isn't this customer's.")
            digits = phone_key(phone)
            if len(digits) != 10:
                raise EditError("A phone number needs 10 digits.")
            if digits != phone_key(mine[contact_id]["value"]):
                others = [c for c in fsm.customers_by_phone(digits) if c["id"] != customer_id]
                if others and not confirm:
                    raise EditError(
                        f"That number is on {others[0]['name']}'s record"
                        + (f" and {len(others) - 1} more" if len(others) > 1 else "")
                        + ". Calls from it would match both. Tick Use anyway to save it.",
                        "phone_taken",
                        {"contact": contact_id, "phone": phone_display(digits)},
                    )
                new_phone = digits
                changed.append(f"phone to {phone_display(digits)}")
        if not changed:
            raise EditError("Nothing to change.")
        modified = None
        if rename:
            modified = fsm.rename_customer(_key(), customer_id, new_name).get("modifiedOn")
        if new_phone:
            fsm.change_phone(_key(), customer_id, contact_id, phone_display(new_phone))
        return changed, modified, f"Changed customer {customer_id}: " + ", ".join(changed)

    return _run(
        conn, tenant, cache, who, now, "edit_customer", target,
        f"Changed customer {customer_id}", write,
    )
