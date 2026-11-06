"""One label per call for the owner: Urgent, Routine or No visit, each with its reason in a few
words, such as "Urgent: an older person or a baby at home, no heat" (DECISIONS.md 121).

Urgent is the server's priority at High or Urgent, a hazard the safety route heard (a gas smell, a
carbon monoxide alarm, sparks), or a technician dispatched on the call. Routine is any other call
about a visit: one booked, moved, cancelled or asked about, or a callback promised. No visit is a
call that was never about one: a wrong number, a business, an address outside the area, a question
answered.

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

# Outcomes (app/lifecycle.py) that were about a visit, and how the reason says it.
VISIT_OUTCOMES = {
    "booked": "visit booked",
    "queued": "visit booked",
    "changed_booking": "visit moved or cancelled",
    "callback": "callback promised",
    "flagged": "passed to a person",
}
NO_VISIT_OUTCOMES = {
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


def decide(
    conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row, outcome: str, ended: bool
) -> tuple[str, str]:
    """The call's label and reason, from what the server recorded so far and the outcome the call
    ended with, or would end with now (app/lifecycle.py, `outcome_of`)."""
    kept = conn.execute(
        "SELECT label, reason FROM call_labels WHERE call_id = ?", (call["id"],)
    ).fetchone()
    if kept and kept["label"] == URGENT:
        return kept["label"], kept["reason"]
    kinds = tenant.safety.get("kinds", {})
    if call["safety_signal_at"] and call["safety_state"] != "cleared":
        heard = kinds.get(call["safety_kind"], {}).get("label", "a safety hazard")
        return URGENT, f"{URGENT}: {heard}"
    if kept:
        return kept["label"], kept["reason"]
    if outcome in VISIT_OUTCOMES:
        return ROUTINE, f"{ROUTINE}: {VISIT_OUTCOMES[outcome]}"
    if outcome in NO_VISIT_OUTCOMES:
        return NO_VISIT, f"{NO_VISIT}: {NO_VISIT_OUTCOMES[outcome]}"
    if not ended:
        return NO_VISIT, f"{NO_VISIT}: nothing booked yet"
    return NO_VISIT, f"{NO_VISIT}: no visit asked for"


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

