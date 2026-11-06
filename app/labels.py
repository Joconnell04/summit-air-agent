"""One label per call for the owner: Urgent, Routine or No visit, each with its reason in a few
words, such as "Urgent: an older person or a baby at home, no heat" (DECISIONS.md 122, 128).

The label describes the visit the call left standing, not the call. Urgent is a visit booked or
dispatched at the server's priority High or Urgent, or a visit booked on a safety call (a gas
smell, a carbon monoxide alarm, sparks). Routine is a visit booked or moved below that. No visit is
every call that left no visit: a callback promised, a question answered, a cancellation, a safety
call where nothing was booked, a business, an address outside the area. Safety calls stay marked
as such on the owner view whatever their label.

The priority a booking tool decided is kept as the call goes (`mark`); the rest is read from the
call's own record when the label is asked for (`decide`), and stored when the call ends (`settle`).
"""

import re
import sqlite3
from datetime import datetime

from .db import iso, tx
from .tenant import Tenant

URGENT, ROUTINE, NO_VISIT = "Urgent", "Routine", "No visit"
LABELS = (URGENT, ROUTINE, NO_VISIT)
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
    """The label a priority gives: Urgent at High or Urgent, with its main reason; Routine below."""
    what = job_name.lower()
    if level in URGENT_LEVELS:
        return URGENT, f"{URGENT}: {', '.join([*_main_reason(tenant, reasons), what])}"
    return ROUTINE, f"{ROUTINE}: {what}"


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
    """Keep the label a booking tool decided. An Urgent label stays: a later, calmer lookup on the
    same call (a tune-up added after the no-heat visit) doesn't make the call Routine."""
    with tx(conn):
        conn.execute(
            "INSERT INTO call_labels (call_id, label, reason, at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (call_id) DO UPDATE SET label = excluded.label, reason = excluded.reason, "
            "at = excluded.at WHERE call_labels.label != ? OR excluded.label = ?",
            (call_id, label, reason, iso(now), URGENT, URGENT),
        )


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
    kept = conn.execute(
        "SELECT label, reason FROM call_labels WHERE call_id = ?", (call["id"],)
    ).fetchone()
    if heard:
        return URGENT, f"{URGENT}: {heard}"
    if kept and kept["label"] == URGENT:
        return kept["label"], kept["reason"]
    if kept and kept["label"] == ROUTINE:
        return kept["label"], kept["reason"]
    return ROUTINE, f"{ROUTINE}: visit booked or moved"


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

