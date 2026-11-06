"""One label per call for the owner: Emergency dispatch, Scheduled or No visit, each with its
reason in a few words (DECISIONS.md 122, 128, 130).

The label describes the visit the call left, not the call. Emergency dispatch is a technician sent
right away (app/tools.py `_dispatch`), always at priority Urgent. Scheduled is every other visit
booked or moved, with its priority (Low, Normal, High or Urgent) in the reason. No visit is every
call that left no visit: a callback promised, a question answered, a cancellation, a safety call
where nothing was booked, a business, an address outside the area. Safety calls stay marked as
such on the owner view whatever their label.

The priority a booking tool decided is kept as the call goes (`mark`); the rest is read from the
call's own record when the label is asked for (`decide`), and stored when the call ends (`settle`).
"""

import re
import sqlite3
from datetime import datetime

from .db import iso, tx
from .tenant import Tenant

EMERGENCY, SCHEDULED, NO_VISIT = "Emergency dispatch", "Scheduled", "No visit"
LABELS = (EMERGENCY, SCHEDULED, NO_VISIT)
URGENT_LEVELS = ("High", "Urgent")

# Why a call left no visit, by its outcome (app/lifecycle.py), as the reason says it.
NO_VISIT_OUTCOMES = {
    "changed_booking": "visit cancelled or address changed",
    "callback": "callback promised",
    "flagged": "passed to a person",
    "transferred": "put through to the office",
    "declined_commercial": "a business, turned away",
    "declined_out_of_area": "outside the service area",
}


def for_priority(
    tenant: Tenant, level: str, reasons: list[str], job_name: str
) -> tuple[str, str]:
    """What a booking tool keeps for the label: the priority and its main reason, such as
    "High: an older person or a baby at home, no heat". Whether the visit is an emergency
    dispatch is read from the call's record when the label is decided."""
    what = job_name.lower()
    shown = _main_reason(tenant, reasons) if level in URGENT_LEVELS else []
    return SCHEDULED, f"{level}: {', '.join([*shown, what])}"


def _main_reason(tenant: Tenant, reasons: list[str]) -> list[str]:
    """The one reason an owner reads first: who or what is at risk, before the temperature bracket
    the call started in, and never the quoted caller line a reason carries for the dispatcher."""
    brackets = {
        b["reason"] for rows in (tenant.priority.get("brackets") or {}).values() for b in rows
    }
    said = [QUOTE.sub("", r) for r in reasons]
    said = [r for r in said if r]
    return ([r for r in said if r not in brackets] or said)[:1]


# The caller's line a priority reason quotes (app/priority.py, `quoted`).
QUOTE = re.compile(r',?\s*caller said ".*"$')


def mark(
    conn: sqlite3.Connection, call_id: str, label: str, reason: str, now: datetime
) -> None:
    """Keep the priority and reason a booking tool decided. A higher priority stays: a later,
    calmer lookup on the same call (a tune-up added after the no-heat visit) doesn't replace it."""
    kept = conn.execute("SELECT reason FROM call_labels WHERE call_id = ?", (call_id,)).fetchone()
    if kept and _rank(kept["reason"]) > _rank(reason):
        return
    with tx(conn):
        conn.execute(
            "INSERT INTO call_labels (call_id, label, reason, at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (call_id) DO UPDATE SET label = excluded.label, reason = excluded.reason, "
            "at = excluded.at",
            (call_id, label, reason, iso(now)),
        )


LEVELS = ("Low", "Normal", "High", "Urgent")


def _rank(reason: str) -> int:
    level = reason.split(":", 1)[0].strip()
    return LEVELS.index(level) if level in LEVELS else -1


def has_visit(conn: sqlite3.Connection, call_id: str) -> bool:
    """A visit this call booked, dispatched or moved that still stands."""
    return bool(conn.execute(
        "SELECT 1 FROM bookings WHERE call_id = ? AND ((kind = 'book' AND status IN "
        "('booked', 'queued')) OR (kind = 'reschedule' AND status = 'done'))",
        (call_id,),
    ).fetchone())


def decide(
    conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row, outcome: str, ended: bool
) -> tuple[str, str]:
    """The call's label and reason, from what the server recorded so far and the outcome the call
    ended with, or would end with now (app/lifecycle.py, `outcome_of`)."""
    kinds = tenant.safety.get("kinds", {})
    hazard = call["safety_signal_at"] and call["safety_state"] != "cleared"
    heard = kinds.get(call["safety_kind"], {}).get("label", "a safety hazard") if hazard else ""
    if not has_visit(conn, call["id"]):
        if heard:
            return NO_VISIT, f"{NO_VISIT}: {heard}, no visit booked"
        if outcome in NO_VISIT_OUTCOMES:
            return NO_VISIT, f"{NO_VISIT}: {NO_VISIT_OUTCOMES[outcome]}"
        if not ended:
            return NO_VISIT, f"{NO_VISIT}: nothing booked yet"
        return NO_VISIT, f"{NO_VISIT}: no visit asked for"
    kept = conn.execute("SELECT reason FROM call_labels WHERE call_id = ?", (call["id"],)).fetchone()
    level, _, core = (kept["reason"] if kept else "").partition(":")
    level, core = level.strip(), core.strip()
    if level not in LEVELS:
        level, core = "", ""
    dispatched = conn.execute(
        "SELECT 1 FROM dispatches WHERE call_id = ? AND replaced_at IS NULL", (call["id"],)
    ).fetchone()
    if dispatched:
        return EMERGENCY, f"{EMERGENCY}: {core or heard or 'technician sent right away'}"
    if heard:
        # The hazard says why; the job name says what. The priority rule's own safety reason would
        # say the hazard twice.
        what = core.rsplit(", ", 1)[-1] if core else ""
        core = f"{heard}, {what}" if what else heard
    if not level:
        row = conn.execute(
            "SELECT priority FROM bookings WHERE call_id = ? AND priority IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (call["id"],),
        ).fetchone()
        level = row["priority"] if row else ""
    shown = f"{level} priority, " if level else ""
    return SCHEDULED, f"{SCHEDULED}: {shown}{core or 'visit booked or moved'}"


def settle(
    conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row, outcome: str, now: datetime
) -> tuple[str, str]:
    """Store the label the call ended with. Run inside the call's end transaction."""
    label, reason = decide(conn, tenant, call, outcome, ended=True)
    conn.execute(
        "INSERT INTO call_labels (call_id, label, reason, at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (call_id) DO UPDATE SET label = excluded.label, reason = excluded.reason, "
        "at = excluded.at",
        (call["id"], label, reason, iso(now)),
    )
    return label, reason

