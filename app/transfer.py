"""Warm transfer to a person (C-11). The server decides whether a flagged call goes to a person now
and to whom; the voice layer only dials, says the server's words, and reports what happened.

A call goes to a person only when all of these hold:
- the tenant has a transfer section and a number set for the role, in the server's environment;
- it isn't a safety call (the lines tell the caller to leave and call 911) or a commercial one;
- during office hours, the caller asked for a person on this call (their own words, matched here,
  not a flag the model sets); after hours, the server's own priority and after-hours cutoffs say
  the on-call technician is needed tonight (journey 11, `tools.needs_on_call_tonight`), never the
  priority the model put on the flag;
- the flag is one a person handles, and no transfer was tried on this call yet.

The flag and its callback are stored before anyone is dialed, so a transfer nobody answers falls
back to exactly what happens without one.
"""

import os
import sqlite3
from collections.abc import Mapping
from datetime import datetime

from . import records
from .db import iso, parse_iso, tx
from .envelope import Refused, Result, ok
from .tenant import Tenant
from .text import has_phrase, phone_spoken, words

# A caller asking for a person, rather than asking whether they are talking to one: "can I talk
# to a real person" counts, "are you a real person" doesn't.
ASK_VERBS = (
    "speak to",
    "speak with",
    "talk to",
    "talk with",
    "connect me to",
    "connect me with",
    "transfer me to",
    "put me through to",
)
ASK_WHO = (
    "a person",
    "a real person",
    "a live person",
    "an actual person",
    "a human",
    "someone",
    "somebody",
    "a representative",
    "an operator",
    "a manager",
    "a supervisor",
    "the office",
)
ASKED_PHRASES = tuple(f"{verb} {who}" for verb in ASK_VERBS for who in ASK_WHO) + (
    "transfer me",
    "put me through",
)
ROLE_NAMES = {"office": "office", "on_call": "on-call technician"}
OUTCOMES = ("connected", "no_answer", "not_accepted", "failed")


def asked_for_person(conn: sqlite3.Connection, call_id: str) -> bool:
    said = words(records.caller_text(conn, call_id))
    return any(has_phrase(said, phrase) for phrase in ASKED_PHRASES)


def number(tenant: Tenant, role: str, environ: Mapping[str, str] = os.environ) -> str:
    """The role's phone number from the environment variable the tenant names, or empty."""
    name = (tenant.transfer or {}).get("targets", {}).get(role, "")
    return environ.get(name, "").strip() if name else ""


def role_for(
    conn: sqlite3.Connection,
    tenant: Tenant,
    call: sqlite3.Row,
    needs_on_call: bool,
    environ: Mapping[str, str] = os.environ,
) -> str | None:
    """Who a person flag on this call goes to now, or None to keep the flag and callback alone.
    `needs_on_call` is the server's own after-hours judgement, never the flag's priority."""
    if not tenant.transfer:
        return None
    if call["safety_signal_at"] and call["safety_state"] != "cleared":
        return None
    if call["commercial"]:
        return None
    if call["after_hours"]:
        role = "on_call" if needs_on_call else None
    else:
        role = "office" if asked_for_person(conn, call["id"]) else None
    if role is None or not number(tenant, role, environ):
        return None
    return role


def _summary(tenant: Tenant, call: sqlite3.Row, flag: sqlite3.Row, role: str) -> str:
    # Urgent only when the server decided the on-call technician is needed, not from the flag.
    priority = "Urgent. " if role == "on_call" else ""
    return tenant.line(
        "transfer_summary",
        priority=priority,
        phone=phone_spoken(call["caller_id"]),
        reason=flag["reason"].rstrip("."),
    )


def plan(
    conn: sqlite3.Connection,
    tenant: Tenant,
    call: sqlite3.Row,
    flag_id: int,
    callback_id: int | None,
    now: datetime,
    needs_on_call: bool,
    environ: Mapping[str, str] = os.environ,
) -> Result:
    """Asked by the voice layer right after a person flag. `transfer` is null when the call stays
    with the agent; otherwise it carries the number to dial, how long to ring, the line the person
    hears when they pick up (no caller details, since it may be a voicemail greeting), the summary
    they hear once they press 1, and the handoff line the caller hears first."""
    flag = conn.execute(
        "SELECT * FROM flags WHERE id = ? AND call_id = ?", (flag_id, call["id"])
    ).fetchone()
    callback = (
        conn.execute(
            "SELECT id FROM callbacks WHERE id = ? AND call_id = ?", (callback_id, call["id"])
        ).fetchone()
        if callback_id
        else None
    )
    if flag is None or (callback_id and callback is None):
        raise Refused("invalid_state", "")
    tried = conn.execute("SELECT 1 FROM transfers WHERE call_id = ?", (call["id"],)).fetchone()
    role = None if tried or flag["kind"] != "person" else role_for(
        conn, tenant, call, needs_on_call, environ
    )
    if role is None:
        return ok(transfer=None)
    summary = _summary(tenant, call, flag, role)
    with tx(conn):
        transfer_id = conn.execute(
            "INSERT INTO transfers (call_id, flag_id, callback_id, role, summary, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (call["id"], flag_id, callback["id"] if callback else None, role, summary, iso(now)),
        ).lastrowid
    cfg = tenant.transfer
    return ok(
        tenant.line("transfer_handoff"),
        transfer={
            "id": transfer_id,
            "role": role,
            "phone": number(tenant, role, environ),
            "ring_seconds": cfg["ring_seconds"],
            "accept_seconds": cfg["accept_seconds"],
            "prompt": tenant.line("transfer_prompt", role=ROLE_NAMES.get(role, role)),
            "summary": summary,
        },
    )


def finish(
    conn: sqlite3.Connection,
    tenant: Tenant,
    call: sqlite3.Row,
    transfer_id: int,
    outcome: str,
    now: datetime,
) -> Result:
    """What happened on the dial. Connected closes the flag and callback, since a person has the
    caller. Anything else leaves them open, and `say` is the line the caller hears next, with the
    callback time stored when the flag was raised."""
    row = conn.execute(
        "SELECT * FROM transfers WHERE id = ? AND call_id = ?", (transfer_id, call["id"])
    ).fetchone()
    if row is None or row["status"] != "dialing":
        raise Refused("invalid_state", "")
    with tx(conn):
        conn.execute(
            "UPDATE transfers SET status = ?, finished_at = ? WHERE id = ?",
            (outcome, iso(now), transfer_id),
        )
        if outcome == "connected":
            conn.execute(
                "UPDATE callbacks SET status = 'done', updated_at = ? WHERE id = ? "
                "AND status = 'pending'",
                (iso(now), row["callback_id"]),
            )
            conn.execute("UPDATE flags SET status = 'done' WHERE id = ?", (row["flag_id"],))
    if outcome == "connected":
        return ok(status=outcome)
    callback = (
        conn.execute("SELECT due_at FROM callbacks WHERE id = ?", (row["callback_id"],)).fetchone()
        if row["callback_id"]
        else None
    )
    if callback is None:
        return ok(tenant.line("flag_stored_no_callback"), status=outcome)
    due = parse_iso(callback["due_at"])
    when = tenant.line("callback_by", time=tenant.time_spoken(due, now))
    return ok(tenant.line("transfer_no_answer", when=when), status=outcome)


def for_call(conn: sqlite3.Connection, call_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM transfers WHERE call_id = ?", (call_id,)).fetchone()
    return dict(row) if row else None
