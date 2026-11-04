"""A heads-up call to the number on file after a change made from another number (issue #128).

When a cancel, a reschedule or an address change goes through for a customer the call's number
isn't on, after the name and address check passed (tools._identity_gate), the customer's own
number gets one short call once the caller has hung up: the fixed sentence from the tenant's
lines, said once, the same message on a voicemail, and no conversation. A known number never
triggers one. No texting in V1 (decision 007); a text is the production version (docs/later.md).

The mock's customers aren't real people, so a number is dialed only when it is on the allowlist
in the server's environment (HEADSUP_ALLOWLIST, comma-separated, empty by default). Every other
heads-up is stored as "would have called", shown the same way on the owner view and the job note,
and never dialed. The allowlist is checked when the heads-up is stored and again when it is
claimed, so a number taken off the list is never dialed.
"""

import logging
import os
import re
import sqlite3
from datetime import datetime

from . import records
from .db import iso, parse_iso, tx
from .fsm import FsmAdapter, FsmRejected, FsmUnavailable
from .tenant import Tenant
from .text import phone_display, phone_key, phone_said

log = logging.getLogger(__name__)


def us_number(value: str) -> str | None:
    """A US number as ten digits: exactly ten, or eleven starting with 1, once spaces and
    punctuation are dropped. Anything else (an extension, a country code other than 1, a typo) is
    None, so it can never pass for another number (PR #134 review)."""
    digits = re.sub(r"[\s().+-]", "", value or "")
    if not digits.isdigit():
        return None
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def allowlist(environ=os.environ) -> frozenset[str]:
    """The numbers a heads-up may dial, as ten-digit keys (us_number). A malformed entry is left
    out and logged, so it can never widen the list to a different number."""
    keys = set()
    for entry in (environ.get("HEADSUP_ALLOWLIST") or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        key = us_number(entry)
        if key:
            keys.add(key)
        else:
            log.warning("HEADSUP_ALLOWLIST entry ignored, not a ten-digit number: %r", entry)
    return frozenset(keys)


def may_dial(phone: str, allowed: frozenset[str]) -> bool:
    """The whole number, normalized the same way, is on the list: an international number never
    matches a US one by its last ten digits."""
    key = us_number(phone)
    return key is not None and key in allowed


def _when(tenant: Tenant, moment: datetime) -> str:
    return tenant.local(moment).strftime("%-I:%M %p")


def _note(fsm: FsmAdapter, row: sqlite3.Row, text: str) -> None:
    """The outcome on the job (or the account, for an address change). A note that fails leaves
    the outcome in our record and on the owner view."""
    try:
        fsm.add_note(f"heads_up:{row['id']}:{row['status']}", row["customer_id"], text,
                     row["job_id"])
    except (FsmUnavailable, FsmRejected):
        return


def queue(
    conn: sqlite3.Connection,
    tenant: Tenant,
    fsm: FsmAdapter,
    call: sqlite3.Row,
    customer_id: int,
    job_id: int | None,
    phone: str,
    change: str,
    allowed: frozenset[str],
    now: datetime,
) -> int | None:
    """Store the heads-up for after the call. `change` is what happened, as the sentence says it
    ("Your visit on Thursday between 8 and 12 was cancelled"). Returns its id, or None with no
    number on file."""
    # Never the number the change came from: the heads-up is for the account's own number.
    if len(phone_key(phone)) != 10 or phone_key(phone) == call["caller_key"]:
        return None
    line = tenant.line(
        "heads_up", change=change, phone=phone_said(tenant.agent["phone_numbers"][0])
    )
    if records.is_do_not_call(conn, phone):
        status, result = "skipped", "do_not_call"
    elif may_dial(phone, allowed):
        status, result = "queued", None
    else:
        status, result = "would_call", "not on the allowlist"
    with tx(conn):
        heads_up_id = conn.execute(
            """INSERT INTO heads_ups (tenant, call_id, customer_id, job_id, phone, line, status,
                                      result, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tenant.slug, call["id"], customer_id, job_id, phone_key(phone), line, status, result,
             iso(now), iso(now)),
        ).lastrowid
    row = conn.execute("SELECT * FROM heads_ups WHERE id = ?", (heads_up_id,)).fetchone()
    if status == "would_call":
        _note(fsm, row, f"Heads-up would have been sent to the number on file, "
                        f"{phone_display(phone)}, at {_when(tenant, now)} (not on the demo "
                        "allowlist, so not dialed).")
    return heads_up_id


def claim(
    conn: sqlite3.Connection, tenants: dict[str, Tenant], allowed: frozenset[str], now: datetime
) -> dict | None:
    """The next heads-up to dial: its call has ended, it is inside the outbound hours, and its
    number is still on the allowlist and not do-not-call. One that may no longer be dialed is
    recorded as would_call or skipped on the way."""
    with tx(conn):
        rows = conn.execute(
            "SELECT h.* FROM heads_ups h JOIN calls c ON c.id = h.call_id "
            "WHERE h.status = 'queued' AND c.ended_at IS NOT NULL ORDER BY h.id"
        ).fetchall()
        for row in rows:
            tenant = tenants[row["tenant"]]
            if not may_dial(row["phone"], allowed):
                _settle(conn, row["id"], "would_call", "not on the allowlist", now)
                continue
            if records.is_do_not_call(conn, row["phone"]):
                _settle(conn, row["id"], "skipped", "do_not_call", now)
                continue
            if not records.in_outbound_hours(tenant, now):
                continue
            placed = f"heads-up-{row['id']}"
            conn.execute(
                "UPDATE heads_ups SET status = 'dialing', placed_call_id = ?, updated_at = ? "
                "WHERE id = ?",
                (placed, iso(now), row["id"]),
            )
            return {
                "outbound_id": row["id"],
                "kind": "heads_up",
                "call_id": placed,
                "phone": row["phone"],
                "dialed_from": tenant.agent["phone_numbers"][0],
                "about_call_id": row["call_id"],
                "opening_line": row["line"],
            }
    return None


def _settle(conn, heads_up_id: int, status: str, result: str, now: datetime) -> None:
    conn.execute(
        "UPDATE heads_ups SET status = ?, result = ?, updated_at = ? WHERE id = ?",
        (status, result, iso(now), heads_up_id),
    )


def finish(
    conn: sqlite3.Connection,
    tenant: Tenant,
    fsm: FsmAdapter,
    heads_up_id: int,
    answered: bool,
    voicemail: bool,
    now: datetime,
) -> dict | None:
    """Record how the one attempt went and note it on the job. None for an unknown or settled id."""
    row = conn.execute("SELECT * FROM heads_ups WHERE id = ?", (heads_up_id,)).fetchone()
    if not row or row["status"] != "dialing":
        return None
    result = "voicemail" if voicemail else "answered" if answered else "no_answer"
    with tx(conn):
        _settle(conn, heads_up_id, "sent", result, now)
    row = conn.execute("SELECT * FROM heads_ups WHERE id = ?", (heads_up_id,)).fetchone()
    said = {"answered": "answered", "voicemail": "left as a voicemail", "no_answer": "no answer"}
    _note(fsm, row, f"Heads-up sent to the number on file, {phone_display(row['phone'])}, at "
                    f"{_when(tenant, now)} ({said[result]}).")
    return {"outbound_id": heads_up_id, "result": result, "outcome": None}


def for_call(conn: sqlite3.Connection, tenant: Tenant, call_id: str) -> list[str]:
    """The owner view's lines: sent, or would have been sent, to the number on file, and when."""
    lines = []
    for row in conn.execute("SELECT * FROM heads_ups WHERE call_id = ? ORDER BY id", (call_id,)):
        at = _when(tenant, parse_iso(row["updated_at"]))
        if row["status"] == "sent":
            lines.append(f"Heads-up sent to the number on file at {at} ({row['result']})")
        elif row["status"] == "would_call":
            lines.append(f"Heads-up would have been sent to the number on file at {at} (not on "
                         "the demo allowlist)")
        elif row["status"] in ("queued", "dialing"):
            lines.append("Heads-up to the number on file goes out after the call")
        else:
            lines.append(f"No heads-up: the number on file is {row['result'].replace('_', ' ')}")
    return lines
