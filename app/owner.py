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
LEAD_PHRASES = {
    "commercial": "a commercial lead",
    "membership": "a membership lead",
    "install": "an install lead",
}
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


# How each recorded outcome reads to an owner.
OUTCOMES = {
    "booked": "Job booked",
    "changed_booking": "Job changed",
    "safety": "Safety lines said",
    "declined_commercial": "Turned away: commercial",
    "declined_out_of_area": "Turned away: outside the area",
    "callback": "Callback promised",
    "no_booking": "No booking needed",
}


def outcome_label(outcome: str | None, ended: bool) -> str:
    if not outcome:
        return "Ended" if ended else "In progress"
    return OUTCOMES.get(outcome, outcome.replace("_", " ").capitalize())


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
                "outcome_label": outcome_label(call["outcome"], bool(call["ended_at"])),
                "booked": _booked(conn, call["id"]),
                "seconds": seconds,
                "open_flags": open_flags,
                "safety_kind": call["safety_kind"] if call["safety_signal_at"] else None,
                "safety_lines": bool(call["safety_lines_at"]),
                "after_hours": bool(call["after_hours"]),
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


def _window_of(tenant: Tenant, window_start: str | None, now: datetime) -> dict | None:
    if not window_start:
        return None
    local = tenant.local(parse_iso(window_start))
    spec = next((w for w in tenant.windows if w.start == local.time()), None)
    label = spec.label() if spec else local.strftime("%-I:%M %p")
    return {
        "day": tenant.day_spoken(local.date(), now),
        "label": "after hours" if spec and spec.after_hours else label,
        "id": spec.id if spec else None,
        "on": local.date(),
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
        window = _window_of(tenant, row["window_start"], now)
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
                "name": request.get("details", {}).get("name", ""),
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
    day_bookings = bookings(conn, tenant, day, now)
    day_callbacks = callbacks(conn, tenant, day)
    needs = needs_person(day_flags)
    return {
        "board": board(conn, tenant, day, now, day_calls, day_flags, day_bookings),
        "summary": summary(day_calls, day_bookings, needs, day_callbacks["overdue"]),
        "turned_away": turned_away(day_calls),
        "callers": {c["id"]: c["caller"] for c in day_calls},
        "flag_titles": {
            f["id"]: flag_title(
                tenant,
                f,
                next((c["safety_kind"] for c in day_calls if c["id"] == f["call_id"]), None),
            )
            for f in day_flags
        },
        "tenant": tenant.name,
        "day": day,
        "is_today": day == tenant.local(now).date(),
        "calls": day_calls,
        "rates": booking_rates(tenant, day_calls),
        "bookings": day_bookings,
        "flags": day_flags,
        "needs_person": needs,
        "callbacks": day_callbacks,
        "kept": booked_and_kept(conn, tenant, day),
        "leads": leads(conn, tenant, day),
        "out_of_area": out_of_area(conn, tenant, day),
        "refusals": refusals(conn, tenant, day),
    }


# ---- the dispatch board (O-1, O-2, O-4) --------------------------------------------------------

# The board's default span in local hours. It widens to the hour for any call outside it.
BOARD_HOURS = (7, 21)
FLAG_LABELS = {
    "overdue_callback": "Late callback",
    "seek_earlier": "Earlier slot",
    "queue_failed": "Booking stuck",
    "limit": "Limit reached",
}
LABEL_CHARS = 24


def position(minute: int, start: int, end: int) -> float:
    """Where a minute of the day sits on a board from start to end, as a percent, kept on it."""
    return round(min(max((minute - start) / (end - start) * 100, 0.0), 100.0), 2)


def _minute(tenant: Tenant, moment: datetime) -> int:
    local = tenant.local(moment)
    return local.hour * 60 + local.minute


# The heading a flag gets in the "Needs attention" table, where the reason is shown under it.
FLAG_TITLES = {
    "overdue_callback": "Late callback",
    "seek_earlier": "Looking for an earlier slot",
    "queue_failed": "Booking didn't reach ServiceTitan",
    "person": "Asked for a person",
    "callback_requested": "Asked to be called back",
    "limit": "Hit a per-call limit",
}


def safety_label(tenant: Tenant, kind: str | None) -> str:
    """The tenant's own name for a safety kind, such as "Carbon monoxide"."""
    label = tenant.safety["kinds"].get(kind or "", {}).get("label", "")
    return label[:1].upper() + label[1:] if label else "Safety call"


def flag_title(tenant: Tenant, flag: dict, safety_kind: str | None) -> str:
    if flag["code"] == "safety":
        return safety_label(tenant, safety_kind)
    return FLAG_TITLES.get(flag["code"], flag["kind"])


def flag_label(tenant: Tenant, flag: dict, safety_kind: str | None) -> str:
    """The few words a flag shows on the board; the full reason is its tooltip."""
    if flag["code"] == "safety":
        return safety_label(tenant, safety_kind)
    if flag["code"] in FLAG_LABELS:
        return FLAG_LABELS[flag["code"]]
    reason = flag["reason"]
    return reason if len(reason) <= LABEL_CHARS else reason[: LABEL_CHARS - 1].rstrip(" ,:;") + "…"


def scheduled(conn: sqlite3.Connection, tenant: Tenant, day: date) -> list[dict]:
    """Booked jobs whose arrival window starts on the day, whenever they were booked."""
    start, end = day_bounds(tenant, day)
    rows = conn.execute(
        "SELECT b.window_start, b.priority FROM bookings b JOIN calls c ON c.id = b.call_id "
        "WHERE c.tenant = ? AND b.kind = 'book' AND b.status IN ('booked', 'queued', 'sending') "
        "AND b.window_start >= ? AND b.window_start < ?",
        (tenant.slug, start, end),
    ).fetchall()
    return [{"at": parse_iso(r["window_start"]), "priority": r["priority"]} for r in rows]


def board(
    conn: sqlite3.Connection,
    tenant: Tenant,
    day: date,
    now: datetime,
    day_calls: list[dict],
    day_flags: list[dict],
    day_bookings: list[dict],
) -> dict:
    """The day as a dispatch board. Three lanes, each a list of marks at a minute of the day:
    what needs a person (flags, open ones bright and handled ones kept for the "handled" filter),
    jobs booked (when the call booked them), and every other call. Under them, the arrival windows
    with how many jobs land in each. A call can be in two lanes: a safety call that booked a check
    is both a flag and a job. A callback the system places itself is not a person's flag."""
    kinds = {c["id"]: c["safety_kind"] for c in day_calls}
    person_flags = [f for f in day_flags if f["code"] != "callback_requested"]
    needs = [
        {
            "minute": _minute(tenant, f["at"]),
            "label": flag_label(tenant, f, kinds.get(f["call_id"])),
            "tone": {"Urgent": "urgent", "High": "high"}.get(f["priority"], "normal"),
            "handled": not f["open"],
            "call_id": f["call_id"],
            "tip": f["reason"],
        }
        for f in person_flags
    ]
    jobs = [
        {
            "minute": _minute(tenant, b["at"]),
            "tone": "booked",
            "call_id": b["call_id"],
            "tip": f"{b['job_type']}, {b['address']}",
        }
        for b in day_bookings
    ]
    flagged = {f["call_id"] for f in person_flags}
    booked = {b["call_id"] for b in day_bookings}
    others = [
        {
            "minute": _minute(tenant, c["at"]),
            "tone": "other",
            "call_id": c["id"],
            "tip": c["type_label"],
        }
        for c in day_calls
        if c["id"] not in booked and c["id"] not in flagged
    ]
    for lane in (needs, jobs, others):
        lane.sort(key=lambda m: m["minute"])

    minutes = [m["minute"] for lane in (needs, jobs, others) for m in lane]
    start = min([BOARD_HOURS[0] * 60] + [m // 60 * 60 for m in minutes])
    end = max([BOARD_HOURS[1] * 60] + [(m // 60 + 1) * 60 for m in minutes])
    is_today = day == tenant.local(now).date()
    now_minute = _minute(tenant, now) if is_today else None

    on_day = scheduled(conn, tenant, day)
    windows = []
    for w in tenant.windows:
        w_start = w.start.hour * 60 + w.start.minute
        w_end = 24 * 60 if w.end <= w.start else w.end.hour * 60 + w.end.minute
        landing = [j for j in on_day if tenant.local(j["at"]).time() == w.start]
        past = day < tenant.local(now).date() or (now_minute is not None and now_minute >= w_end)
        windows.append(
            {
                "name": "After hours" if w.after_hours else w.label(),
                "start": w_start,
                "end": w_end,
                "jobs": len(landing),
                "urgent": sum(1 for j in landing if j["priority"] == "Urgent"),
                "past": past,
            }
        )
    later = sum(1 for b in day_bookings if b["window"] and b["window"]["on"] > day)
    return {
        "start": start,
        "end": end,
        "now": now_minute,
        "lanes": {"needs": needs, "jobs": jobs, "others": others},
        "open": sum(1 for m in needs if not m["handled"]),
        "windows": windows,
        "scheduled": len(on_day),
        "later": later,
    }


# ---- the status line and the summary sentence (O-1, O-5) ---------------------------------------


def status(conn: sqlite3.Connection, tenant: Tenant, now: datetime, stale_seconds: int) -> dict:
    """Whether the agent is answering, read from the voice workers' heartbeats, and when the last
    call came in. With no heartbeat ever (a development server, the demo) it says only the last
    call, since it can't tell either way."""
    last = conn.execute(
        "SELECT MAX(started_at) FROM calls WHERE tenant = ? AND direction = 'inbound'",
        (tenant.slug,),
    ).fetchone()[0]
    beats = [parse_iso(r[0]) for r in conn.execute("SELECT at FROM heartbeats")]
    last_call = tenant.time_spoken(parse_iso(last), now) if last else None
    if not beats:
        return {"state": "unknown", "last_call": last_call}
    newest = max(beats)
    if newest >= now - timedelta(seconds=stale_seconds):
        return {"state": "ok", "last_call": last_call}
    return {"state": "down", "last_call": last_call, "since": tenant.time_spoken(newest, now)}


def summary(day_calls: list[dict], day_bookings: list[dict], needs: int, overdue: int) -> list:
    """The page's first sentence as parts, each (text, style) with style "", "b" or "alarm"."""
    if not day_calls:
        return [("No calls yet.", "")]
    inbound = sum(1 for c in day_calls if c["direction"] == "inbound")
    parts = [(f"{inbound} call{'s' * (inbound != 1)} came in. ", "")]
    booked = sum(1 for b in day_bookings if b["status"] == "booked")
    parts.append((f"{booked} job{'s' * (booked != 1)} booked.", "b"))
    safety = sum(1 for c in day_calls if c["safety_kind"])
    if safety:
        parts.append((f" {safety} safety call{'s' * (safety != 1)} handled.", ""))
    if needs:
        parts.append((f" {needs} need{'s' * (needs == 1)} a person.", "alarm"))
    if overdue:
        parts.append((f" {overdue} callback{'s' * (overdue != 1)} late.", "alarm"))
    return parts


def turned_away(day_calls: list[dict]) -> list[dict]:
    """Calls the agent turned away on the day: commercial callers and ZIPs outside the area."""
    return [c for c in day_calls if c["outcome"].startswith("declined")]


# ---- Results: a period against the one before it (O-9, O-10) ----------------------------------

PERIODS = (1, 7, 30)


def _period(conn: sqlite3.Connection, tenant: Tenant, end_day: date, days: int, now: datetime):
    """The calls, bookings and callbacks of the days ending with end_day."""
    period_calls, period_bookings, period_callbacks, period_leads = [], [], [], []
    for back in range(days):
        day = end_day - timedelta(days=back)
        period_calls += calls(conn, tenant, day)
        period_bookings += bookings(conn, tenant, day, now)
        period_callbacks += callbacks(conn, tenant, day)["items"]
        period_leads += leads(conn, tenant, day)
    return period_calls, period_bookings, period_callbacks, period_leads


def measures(conn: sqlite3.Connection, tenant: Tenant, end_day: date, days: int, now: datetime):
    """The Results measures for the days ending with end_day. Each is None when there is nothing
    to measure, so an empty earlier period shows a dash rather than a zero that reads as a drop."""
    period_calls, period_bookings, period_callbacks, period_leads = _period(
        conn, tenant, end_day, days, now
    )
    inbound = [c for c in period_calls if c["direction"] == "inbound"]
    rates = booking_rates(tenant, period_calls)
    promised = [c for c in period_callbacks if c["status"] != "cancelled"]
    safety = [c for c in inbound if c["safety_kind"]]
    kept = booked_and_kept(conn, tenant, end_day)
    return {
        "calls": len(inbound) if inbound else None,
        "after_hours": sum(1 for c in inbound if c["after_hours"]),
        "booked": sum(1 for b in period_bookings if b["status"] == "booked") if inbound else None,
        "rate": rates["rate"],
        "rate_of": (rates["booked"], rates["calls"]),
        "callbacks": (sum(1 for c in promised if c["status"] == "done"), len(promised)),
        "callback_rate": (
            sum(1 for c in promised if c["status"] == "done") / len(promised) if promised else None
        ),
        "leads": len(period_leads) if inbound else None,
        "safety": (sum(1 for c in safety if c["safety_lines"]), len(safety)),
        "kept": kept,
    }


def results(conn: sqlite3.Connection, tenant: Tenant, end_day: date, days: int, now: datetime):
    """Everything on the Results page for a period of days ending with end_day: the measures
    beside the period before, every inbound call's hour and type for the chart and its filter,
    booking rate by type, and what was turned away or held back (O-10)."""
    period_calls, _, _, period_leads = _period(conn, tenant, end_day, days, now)
    inbound = [c for c in period_calls if c["direction"] == "inbound"]
    hours = [
        {
            "hour": tenant.local(c["at"]).hour,
            "type": c["type"],
            "booked": c["booked"],
            "safety": bool(c["safety_kind"]),
        }
        for c in inbound
    ]
    types = sorted(
        {(c["type"], c["type_label"]) for c in inbound}, key=lambda t: (t[0] != "safety", t[1])
    )
    held = [c for c in inbound if c["safety_kind"]]
    start_day = end_day - timedelta(days=days - 1)
    overrides, refused = [], {}
    for back in range(days):
        found = refusals(conn, tenant, end_day - timedelta(days=back))
        overrides += found["overrides"]
        for r in found["refused"]:
            refused.setdefault(r["code"], {"code": r["code"], "count": 0, "call_id": r["call_id"]})
            refused[r["code"]]["count"] += r["count"]
    since = iso(datetime.combine(start_day, time.min, tenant.tz))
    _, until = day_bounds(tenant, end_day)
    zips = conn.execute(
        "SELECT o.zip, COUNT(DISTINCT o.call_id) AS calls FROM out_of_area o "
        "JOIN calls c ON c.id = o.call_id WHERE c.tenant = ? AND o.at >= ? AND o.at < ? "
        "AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.call_id = o.call_id AND b.kind = 'book' "
        "AND b.status IN ('booked', 'queued', 'sending')) GROUP BY o.zip ORDER BY calls DESC, o.zip",
        (tenant.slug, since, until),
    ).fetchall()
    return {
        "days": days,
        "start_day": start_day,
        "end_day": end_day,
        "now": measures(conn, tenant, end_day, days, now),
        "before": measures(conn, tenant, start_day - timedelta(days=1), days, now),
        "hours": hours,
        "types": [{"code": code, "label": label} for code, label in types],
        "rates": booking_rates(tenant, period_calls),
        "commercial": [lead for lead in period_leads if lead["kind"] == "commercial"],
        "zips": [dict(r) for r in zips],
        "held": {
            "calls": len(held),
            "lines_first": sum(1 for c in held if c["safety_lines"]),
            "booked": sum(1 for c in held if c["booked"]),
        },
        "overrides": overrides,
        "refused": sorted(refused.values(), key=lambda r: -r["count"]),
    }


# ---- a call's page (O-6, O-7, O-8) -------------------------------------------------------------


def call_summary(tenant: Tenant, trace: dict, kind: str, now: datetime) -> str:
    """What happened on the call in a sentence or two, from what the server recorded."""
    call = trace["call"]
    booked = [b for b in trace["bookings"] if b["kind"] == "book" and b["status"] == "booked"]
    job = booked[-1] if booked else None
    window = ""
    if job and job["window_start"]:
        local = tenant.local(parse_iso(job["window_start"]))
        spec = next((w for w in tenant.windows if w.start == local.time()), None)
        when = "after hours" if spec and spec.after_hours else (spec.label() if spec else "")
        window = f" for {tenant.day_spoken(local.date(), now)}{', ' + when if when else ''}"
    parts = []
    if call["safety_kind"]:
        what = safety_label(tenant, call["safety_kind"])
        parts.append(f"Safety call: {what.lower()}. The agent said the safety lines first.")
        if job and job["safety_override"]:
            parts.append(
                f"The caller asked to book anyway, so it booked a safety check{window} "
                "and logged a safety override."
            )
        elif not job:
            parts.append("Nothing was booked; the call ended so they could get out.")
    elif job:
        parts.append(f"Booked: {type_label(tenant, kind)}{window}, {job['priority']} priority.")
        if job["priority_reasons"]:
            parts.append(f"Priority because {job['priority_reasons']}.")
    elif call["commercial"]:
        parts.append("A commercial caller. Turned away politely and saved as a lead for sales.")
    elif trace["out_of_area"]:
        zips = ", ".join(z["zip"] for z in trace["out_of_area"])
        parts.append(f"Outside the service area (ZIP {zips}). Turned away politely.")
    for lead in trace["leads"]:
        if lead["kind"] != "commercial":
            parts.append(f"Saved {LEAD_PHRASES.get(lead['kind'], 'a lead')} for sales.")
    for cb in trace["callbacks"]:
        due = tenant.local(parse_iso(cb["due_at"])).strftime("%-I:%M %p")
        parts.append(f"Promised a call back by {due} ({cb['owner'].replace('_', ' ')}).")
        break
    person = [f for f in trace["flags"] if f["kind"] == "person"]
    if person and not parts:
        parts.append(f"Flagged for a person: {person[0]['reason']}.")
    if not parts:
        parts.append((call["outcome"] or "In progress").replace("_", " ").capitalize() + ".")
    return " ".join(parts)


def call_page(conn: sqlite3.Connection, tenant: Tenant, trace: dict, now: datetime) -> dict:
    """The facts around one call: where it sits in its day, the job and customer it touched, and
    the reports filed about it."""
    call = trace["call"]
    started = parse_iso(call["started_at"])
    day = tenant.local(started).date()
    day_calls = list(reversed(calls(conn, tenant, day)))
    index = next(i for i, c in enumerate(day_calls) if c["id"] == call["id"])
    this = day_calls[index]
    request = {}
    row = conn.execute(
        "SELECT request_json FROM bookings WHERE call_id = ? AND kind = 'book' ORDER BY id DESC",
        (call["id"],),
    ).fetchone()
    if row:
        request = json.loads(row["request_json"])
    readback = json.loads(call["readback_json"]) if call["readback_json"] else {}
    address = request.get("address") or readback.get("address") or {}
    booked = [b for b in trace["bookings"] if b["kind"] == "book"]
    ended = parse_iso(call["ended_at"]) if call["ended_at"] else None
    return {
        "day": day,
        "type": this["type"],
        "type_label": this["type_label"],
        "position": index + 1,
        "of": len(day_calls),
        "previous": day_calls[index - 1] if index > 0 else None,
        "next": day_calls[index + 1] if index + 1 < len(day_calls) else None,
        "started": started,
        "seconds": int((ended - started).total_seconds()) if ended else None,
        "summary": call_summary(tenant, trace, this["type"], now),
        "safety_label": safety_label(tenant, call["safety_kind"]) if call["safety_kind"] else "",
        "name": request.get("details", {}).get("name", ""),
        "job_type": type_label(tenant, request["job_type"]) if request.get("job_type") else "",
        "address": address,
        "details": request.get("details", {}),
        "job": _window_of(tenant, booked[-1]["window_start"], now) if booked else None,
        "booking": booked[-1] if booked else None,
        "open_flags": [f for f in trace["flags"] if f["status"] == "open"],
    }


def ledger(now: dict, before: dict) -> list[dict]:
    """The Results table: each measure for the period and the one before, with the change. Rates
    change in percentage points; a dash when either side has nothing to measure."""

    def pct(value):
        return None if value is None else round(value * 100)

    def row(label, sub, current, previous, unit=""):
        delta = None if current is None or previous is None else current - previous
        return {
            "label": label,
            "sub": sub,
            "now": None if current is None else f"{current}{unit}",
            "before": None if previous is None else f"{previous}{unit}",
            "delta": None if delta is None else f"{delta:+d}{' pts' if unit == '%' else ''}",
            "direction": None
            if delta is None
            else ("up" if delta > 0 else "down" if delta < 0 else "flat"),
        }

    booked, wanted = now["rate_of"]
    done, promised = now["callbacks"]
    lines, safety = now["safety"]
    kept = now["kept"]
    return [
        row("Calls answered", f"{now['after_hours']} after hours", now["calls"], before["calls"]),
        row("Jobs booked", "in ServiceTitan", now["booked"], before["booked"]),
        row(
            "Booking rate",
            f"{booked} of {wanted} calls wanting a visit",
            pct(now["rate"]),
            pct(before["rate"]),
            "%",
        ),
        row(
            "Callbacks on time",
            f"{done} of {promised} promised",
            pct(now["callback_rate"]),
            pct(before["callback_rate"]),
            "%",
        ),
        row("Leads to sales", "membership, install and commercial", now["leads"], before["leads"]),
        row(
            "Safety lines said first",
            f"{lines} of {safety} safety calls",
            lines if safety else None,
            before["safety"][0] if before["safety"][1] else None,
        ),
        row(
            "Booked and kept",
            f"{kept['kept']} of {kept['checked']}, last 30 days",
            pct(kept["rate"]),
            pct(before["kept"]["rate"]),
            "%",
        ),
    ]
