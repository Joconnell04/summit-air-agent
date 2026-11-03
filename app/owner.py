"""The numbers and lists behind the owner view (spec S-44; C-04, C-31, C-42, C-45). Read-only:
nothing here writes. Each number is defined in its function's docstring and has a test in
tests/tool_server/test_ts_owner.py.

A day is the tenant's local calendar day. Booked-and-kept and out-of-area demand look back
LOOKBACK_DAYS from the end of that day, because a single day is too few to read.
"""

import json
import sqlite3
from collections import Counter
from datetime import date, datetime, time, timedelta

from .db import iso, parse_iso
from .tenant import Tenant

LOOKBACK_DAYS = 30
PRIORITY_ORDER = {"Urgent": 0, "High": 1, "Normal": 2, "Low": 3}
# Call types that aren't job types. A job type's label comes from the tenant's config.
TYPE_LABELS = {
    "safety": "Safety",
    "commercial": "Commercial",
    "out_of_area": "Out of area",
    "membership": "Membership lead",
    "install": "Install lead",
    "change": "Reschedule or cancel",
    "earlier_slot": "Earlier opening",
    "callback": "Callback",
    "other": "Other",
}


def day_bounds(tenant: Tenant, day: date) -> tuple[str, str]:
    start = datetime.combine(day, time.min, tenant.tz)
    return iso(start), iso(start + timedelta(days=1))


def type_label(tenant: Tenant, code: str) -> str:
    if code in tenant.job_types:
        return tenant.job_types[code]["name"]
    return TYPE_LABELS.get(code, code.replace("_", " ").capitalize())


def call_type(conn: sqlite3.Connection, call: sqlite3.Row) -> str:
    """What the call was about, from what the server recorded. Calls that can never book come
    first (safety, commercial, out of area), then the job type booked, then the job type the agent
    looked up times for, then a lead, a reschedule or cancel, and otherwise "other"."""

    def first(sql: str):
        return conn.execute(sql, (call["id"],)).fetchone()

    if call["safety_signal_at"] and call["safety_state"] != "cleared":
        return "safety"
    if call["commercial"]:
        return "commercial"
    if first("SELECT 1 FROM out_of_area WHERE call_id = ?"):
        return "out_of_area"
    booked = first(
        "SELECT job_type FROM bookings WHERE call_id = ? AND kind = 'book' ORDER BY id DESC LIMIT 1"
    )
    if booked and booked["job_type"]:
        return booked["job_type"]
    asked = first(
        "SELECT args_json FROM tool_calls WHERE call_id = ? AND tool = 'find_slots' "
        "ORDER BY id DESC LIMIT 1"
    )
    if asked:
        return json.loads(asked["args_json"]).get("job_type") or "other"
    lead = first("SELECT kind FROM leads WHERE call_id = ? ORDER BY id LIMIT 1")
    if lead:
        return lead["kind"]
    if first("SELECT 1 FROM bookings WHERE call_id = ? AND kind IN ('reschedule', 'cancel')"):
        return "change"
    return "other"


def _booked(conn: sqlite3.Connection, call_id: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM bookings WHERE call_id = ? AND kind = 'book' AND status = 'booked'",
            (call_id,),
        ).fetchone()
    )


def calls(conn: sqlite3.Connection, tenant: Tenant, day: date) -> list[dict]:
    """Every call that started on the day, newest first, with its type, outcome and open flags."""
    start, end = day_bounds(tenant, day)
    rows = conn.execute(
        "SELECT * FROM calls WHERE tenant = ? AND started_at >= ? AND started_at < ? "
        "ORDER BY started_at DESC, rowid DESC",
        (tenant.slug, start, end),
    ).fetchall()
    found = []
    for call in rows:
        code = call_type(conn, call)
        if call["direction"] == "outbound":
            placed = conn.execute(
                "SELECT kind FROM outbound WHERE placed_call_id = ?", (call["id"],)
            ).fetchone()
            code = placed["kind"] if placed else code
        seconds = None
        if call["ended_at"]:
            seconds = int(
                (parse_iso(call["ended_at"]) - parse_iso(call["started_at"])).total_seconds()
            )
        open_flags = conn.execute(
            "SELECT COUNT(*) FROM flags WHERE call_id = ? AND status = 'open'", (call["id"],)
        ).fetchone()[0]
        found.append(
            {
                "id": call["id"],
                "at": parse_iso(call["started_at"]),
                "caller": call["caller_id"],
                "direction": call["direction"],
                "type": code,
                "type_label": type_label(tenant, code),
                "outcome": call["outcome"] or ("in progress" if not call["ended_at"] else ""),
                "booked": _booked(conn, call["id"]),
                "seconds": seconds,
                "open_flags": open_flags,
            }
        )
    return found


def booking_rates(tenant: Tenant, day_calls: list[dict]) -> dict:
    """Booking rate by call type (C-31, C-42): of the day's inbound calls of each type, the share
    with a booking the FSM accepted. A queued booking counts once the retry job books it.

    The headline rate is over calls that wanted a visit (a job type), so wrong numbers, commercial
    and out-of-area calls don't pull it down; the table still shows them with their own rate."""
    inbound = [c for c in day_calls if c["direction"] == "inbound"]
    by_type: dict[str, dict] = {}
    for call in inbound:
        row = by_type.setdefault(
            call["type"],
            {"type": call["type"], "label": call["type_label"], "calls": 0, "booked": 0},
        )
        row["calls"] += 1
        row["booked"] += int(call["booked"])
    rows = sorted(by_type.values(), key=lambda r: (-r["calls"], r["label"]))
    for row in rows:
        row["rate"] = row["booked"] / row["calls"]
    wanted = [r for r in rows if r["type"] in tenant.job_types]
    calls_wanted = sum(r["calls"] for r in wanted)
    booked = sum(r["booked"] for r in wanted)
    return {
        "rows": rows,
        "calls": calls_wanted,
        "booked": booked,
        "rate": booked / calls_wanted if calls_wanted else None,
    }


def bookings(conn: sqlite3.Connection, tenant: Tenant, day: date, now: datetime) -> list[dict]:
    """Bookings made on the day, newest first: the window, priority with its reasons, whether an
    earlier slot is being sought and what came of the offer, and any safety override."""
    start, end = day_bounds(tenant, day)
    rows = conn.execute(
        "SELECT b.* FROM bookings b JOIN calls c ON c.id = b.call_id WHERE c.tenant = ? "
        "AND b.kind = 'book' AND b.created_at >= ? AND b.created_at < ? ORDER BY b.created_at DESC, b.id DESC",
        (tenant.slug, start, end),
    ).fetchall()
    found = []
    for row in rows:
        request = json.loads(row["request_json"])
        address = request["address"]
        offer = conn.execute(
            "SELECT status, result FROM outbound WHERE booking_id = ? ORDER BY id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        window = None
        if row["window_start"]:
            local = tenant.local(parse_iso(row["window_start"]))
            spec = next((w for w in tenant.windows if w.start == local.time()), None)
            label = spec.label() if spec else local.strftime("%-I:%M %p")
            window = {
                "day": tenant.day_spoken(local.date(), now),
                "label": "after hours" if spec and spec.after_hours else label,
            }
        found.append(
            {
                "call_id": row["call_id"],
                "at": parse_iso(row["created_at"]),
                "status": row["status"],
                "job_type": type_label(tenant, row["job_type"] or "other"),
                "address": address["street"] + (f" {address['unit']}" if address["unit"] else ""),
                "zip": address["zip"],
                "window": window,
                "priority": row["priority"],
                "reasons": row["priority_reasons"],
                "seek_earlier": bool(row["seek_earlier"]),
                "offer": (offer["result"] or offer["status"]) if offer else None,
                "safety_override": bool(row["safety_override"]),
                "fsm_job_id": row["fsm_job_id"],
            }
        )
    return found


def flags(conn: sqlite3.Connection, tenant: Tenant, day: date) -> list[dict]:
    """Flags raised on the day with their reasons: open ones first, most urgent first, then newest.
    Each carries whether the on-call email went out."""
    start, end = day_bounds(tenant, day)
    rows = conn.execute(
        "SELECT f.*, a.status AS alert FROM flags f JOIN calls c ON c.id = f.call_id "
        "LEFT JOIN alerts a ON a.flag_id = f.id "
        "WHERE c.tenant = ? AND f.created_at >= ? AND f.created_at < ?",
        (tenant.slug, start, end),
    ).fetchall()
    found = [
        {
            "id": r["id"],
            "call_id": r["call_id"],
            "at": parse_iso(r["created_at"]),
            "code": r["kind"],
            "kind": r["kind"].replace("_", " ").capitalize(),
            "reason": r["reason"],
            "priority": r["priority"],
            "open": r["status"] == "open",
            "alert": r["alert"],
        }
        for r in rows
    ]
    found.sort(key=lambda f: f["at"], reverse=True)
    found.sort(key=lambda f: (not f["open"], PRIORITY_ORDER.get(f["priority"], 9)))
    return found


def needs_person(day_flags: list[dict]) -> int:
    """Calls needing a person: distinct calls from the day with a flag still open, leaving out a
    callback the system is placing itself (it becomes a person's flag if the dial fails)."""
    return len({f["call_id"] for f in day_flags if f["open"] and f["code"] != "callback_requested"})


def callbacks(conn: sqlite3.Connection, tenant: Tenant, day: date) -> dict:
    """Callbacks promised on the day against done (C-45). A cancelled one (the caller asked not to
    be called) is left out of the rate; an overdue one counts as not done, and so does one whose
    time hasn't come yet, so early in the day the rate reads low and the overdue count is the
    number to watch."""
    start, end = day_bounds(tenant, day)
    rows = conn.execute(
        "SELECT cb.*, o.id AS dial_id, o.status AS dial_status, o.result AS dial_result "
        "FROM callbacks cb JOIN calls c ON c.id = cb.call_id "
        "LEFT JOIN outbound o ON o.callback_id = cb.id "
        "WHERE c.tenant = ? AND cb.created_at >= ? AND cb.created_at < ? ORDER BY cb.due_at",
        (tenant.slug, start, end),
    ).fetchall()
    items = [
        {
            "call_id": r["call_id"],
            "due": parse_iso(r["due_at"]),
            "owner": "system" if r["dial_id"] else r["owner"].replace("_", " "),
            "reason": r["reason"],
            "status": r["status"],
            "dial": r["dial_result"] or r["dial_status"],
        }
        for r in rows
    ]
    counts = Counter(i["status"] for i in items)
    promised = len(items) - counts["cancelled"]
    return {
        "items": items,
        "promised": promised,
        "done": counts["done"],
        "overdue": counts["overdue"],
        "pending": counts["pending"],
        "rate": counts["done"] / promised if promised else None,
    }


def booked_and_kept(conn: sqlite3.Connection, tenant: Tenant, day: date) -> dict:
    """Booked-and-kept (C-42): of bookings made in the LOOKBACK_DAYS up to the end of the day whose
    window has come, the share still on the schedule then (not cancelled). Bookings whose window
    hasn't come yet are counted as waiting."""
    _, end = day_bounds(tenant, day)
    since = iso(parse_iso(end) - timedelta(days=LOOKBACK_DAYS))
    rows = conn.execute(
        "SELECT k.kept FROM bookings b JOIN calls c ON c.id = b.call_id "
        "LEFT JOIN kept k ON k.booking_id = b.id WHERE c.tenant = ? AND b.kind = 'book' "
        "AND b.status = 'booked' AND b.created_at >= ? AND b.created_at < ?",
        (tenant.slug, since, end),
    ).fetchall()
    checked = [r["kept"] for r in rows if r["kept"] is not None]
    kept = sum(checked)
    return {
        "kept": kept,
        "checked": len(checked),
        "waiting": len(rows) - len(checked),
        "rate": kept / len(checked) if checked else None,
    }


def leads(conn: sqlite3.Connection, tenant: Tenant, day: date) -> list[dict]:
    """Membership, install and commercial leads taken on the day, with the call they came from."""
    start, end = day_bounds(tenant, day)
    rows = conn.execute(
        "SELECT l.*, cb.due_at FROM leads l JOIN calls c ON c.id = l.call_id "
        "LEFT JOIN callbacks cb ON cb.id = l.callback_id "
        "WHERE c.tenant = ? AND l.created_at >= ? AND l.created_at < ? ORDER BY l.created_at DESC, l.id DESC",
        (tenant.slug, start, end),
    ).fetchall()
    return [
        {
            "call_id": r["call_id"],
            "at": parse_iso(r["created_at"]),
            "kind": r["kind"],
            "summary": r["summary"],
            "status": "sent" if r["status"] == "stored" else r["status"],
            "callback": parse_iso(r["due_at"]) if r["due_at"] else None,
        }
        for r in rows
    ]


def out_of_area(conn: sqlite3.Connection, tenant: Tenant, day: date) -> list[dict]:
    """Out-of-area calls by ZIP over the LOOKBACK_DAYS up to the end of the day, most first (S-28),
    so demand outside the area can inform a decision to expand. A call that booked a visit is
    left out: the caller lives inside the area, and the ZIP they asked about was someone else's."""
    _, end = day_bounds(tenant, day)
    since = iso(parse_iso(end) - timedelta(days=LOOKBACK_DAYS))
    rows = conn.execute(
        "SELECT o.zip, COUNT(DISTINCT o.call_id) AS calls FROM out_of_area o "
        "JOIN calls c ON c.id = o.call_id WHERE c.tenant = ? AND o.at >= ? AND o.at < ? "
        "AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.call_id = o.call_id AND b.kind = 'book' "
        "AND b.status IN ('booked', 'queued', 'sending')) "
        "GROUP BY o.zip ORDER BY calls DESC, o.zip",
        (tenant.slug, since, end),
    ).fetchall()
    return [dict(r) for r in rows]


def refusals(conn: sqlite3.Connection, tenant: Tenant, day: date) -> dict:
    """Safety overrides and refused tool calls on the day (spec S-18, docs/architecture.md audit).
    Refusals are grouped by code, each with how many and the latest call, because most are the
    normal flow (a booking tried before the read-back) and the count is what shows a problem."""
    start, end = day_bounds(tenant, day)
    overrides = conn.execute(
        "SELECT b.call_id, b.created_at, b.fsm_job_id FROM bookings b JOIN calls c "
        "ON c.id = b.call_id WHERE c.tenant = ? AND b.safety_override = 1 "
        "AND b.created_at >= ? AND b.created_at < ? ORDER BY b.created_at DESC, b.id DESC",
        (tenant.slug, start, end),
    ).fetchall()
    rows = conn.execute(
        "SELECT t.refused, t.tool, COUNT(*) AS n, MAX(t.at) AS last_at, "
        "(SELECT t2.call_id FROM tool_calls t2 JOIN calls c2 ON c2.id = t2.call_id "
        " WHERE t2.refused = t.refused AND c2.tenant = ? AND t2.at >= ? AND t2.at < ? "
        " ORDER BY t2.id DESC LIMIT 1) AS last_call "
        "FROM tool_calls t JOIN calls c ON c.id = t.call_id "
        "WHERE c.tenant = ? AND t.refused IS NOT NULL AND t.at >= ? AND t.at < ? "
        "GROUP BY t.refused ORDER BY n DESC, t.refused",
        (tenant.slug, start, end, tenant.slug, start, end),
    ).fetchall()
    return {
        "overrides": [
            {"call_id": r["call_id"], "at": parse_iso(r["created_at"]), "job": r["fsm_job_id"]}
            for r in overrides
        ],
        "refused": [
            {"code": r["refused"], "tool": r["tool"], "count": r["n"], "call_id": r["last_call"]}
            for r in rows
        ],
    }


def page(conn: sqlite3.Connection, tenant: Tenant, day: date, now: datetime) -> dict:
    day_calls = calls(conn, tenant, day)
    day_flags = flags(conn, tenant, day)
    return {
        "tenant": tenant.name,
        "day": day,
        "is_today": day == tenant.local(now).date(),
        "calls": day_calls,
        "rates": booking_rates(tenant, day_calls),
        "bookings": bookings(conn, tenant, day, now),
        "flags": day_flags,
        "needs_person": needs_person(day_flags),
        "callbacks": callbacks(conn, tenant, day),
        "kept": booked_and_kept(conn, tenant, day),
        "leads": leads(conn, tenant, day),
        "out_of_area": out_of_area(conn, tenant, day),
        "refusals": refusals(conn, tenant, day),
    }
