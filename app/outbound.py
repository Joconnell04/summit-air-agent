"""Calls the system places (journey 12). The background jobs decide what to dial: an earlier
opening for a flagged urgent booking, or a callback the caller asked for once its time comes. The
voice layer claims the next one, dials it as an outbound call through the normal lifecycle, and
reports whether it was answered. The server then reads what happened on that call: a reschedule of
the booking means the opening was accepted.

The rules are the same as every outbound call (spec S-42): only the person who called about it,
inside the outbound hours, one attempt, never a do-not-call number. They are checked again at
claim time, because a number can be marked do-not-call between scheduling and dialing.

A callback counts as done only when ServiceTitan's call log shows a call back that resolved it
(docs/decisions/26-callback-done.md). ServiceTitan has no outcome field on a call, so the outcome
is a tag on the call, one of the tenant's callback tags. A person at the office puts one on the
calls they make; for the calls placed here, finish() decides the tag from what the server saw on
the call and applies it, and jobs.confirm_callbacks marks the callback done from the log.
"""

import json
import re
import sqlite3
from datetime import date, datetime, timedelta

from . import records
from .db import iso, parse_iso, tx
from .fsm import FsmAdapter, FsmRejected, FsmUnavailable
from .settings import STUCK_DIALING_MINUTES, TAG_RETRY_MINUTES
from .tenant import Tenant
from .text import has_phrase, words

# What a person says to "Am I speaking with Pat?" when it is them, and when it is not. A reply of
# more than a few words ("yeah, hold on, let me get him") is not a confirmation.
YES_WORDS = ("yes", "yeah", "yep", "yup", "speaking", "thats me", "that is me", "correct")
NO_WORDS = ("no", "not", "wrong", "nope", "isnt", "dont know")
NAME_REPLY_MAX_WORDS = 8


def add_callback_dial(
    conn: sqlite3.Connection,
    tenant: Tenant,
    call: sqlite3.Row,
    callback_id: int,
    due: datetime,
    now: datetime,
) -> int:
    """A callback the system will place itself. It waits until its time (jobs.schedule_callbacks)."""
    return conn.execute(
        """INSERT INTO outbound (tenant, call_id, kind, phone, callback_id, due_at, status,
                                 created_at, updated_at)
           VALUES (?, ?, 'callback', ?, ?, ?, 'waiting', ?, ?)""",
        (tenant.slug, call["id"], call["caller_id"], callback_id, iso(due), iso(now), iso(now)),
    ).lastrowid


def add_offer(
    conn: sqlite3.Connection, tenant: Tenant, booking: sqlite3.Row, phone: str, offer: dict, now
) -> int:
    return conn.execute(
        """INSERT INTO outbound (tenant, call_id, kind, phone, booking_id, offer_json, due_at,
                                 status, created_at, updated_at)
           VALUES (?, ?, 'earlier_slot', ?, ?, ?, ?, 'queued', ?, ?)""",
        (
            tenant.slug,
            booking["call_id"],
            phone,
            booking["id"],
            json.dumps(offer),
            iso(now),
            iso(now),
            iso(now),
        ),
    ).lastrowid


def allowed(
    conn: sqlite3.Connection, tenant: Tenant, row: sqlite3.Row, now: datetime
) -> tuple[bool, str]:
    if row["kind"] == "earlier_slot" and not _offer_current(conn, row, now):
        return False, "stale"
    if row["callback_id"]:
        callback = conn.execute(
            "SELECT * FROM callbacks WHERE id = ?", (row["callback_id"],)
        ).fetchone()
        return records.outbound_allowed(conn, tenant, callback, now)
    return records.dial_allowed(conn, tenant, row["call_id"], row["phone"], now)


def _offer_current(conn: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> bool:
    """The booking is still booked and still waiting for an earlier slot, and the opening is both
    ahead of now and earlier than the booking. A caller who moved or cancelled meanwhile gets no
    call about it."""
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (row["booking_id"],)).fetchone()
    offer = json.loads(row["offer_json"])
    return bool(
        booking
        and booking["status"] == "booked"
        and booking["seek_earlier"]
        and parse_iso(offer["start"]) > now
        and parse_iso(offer["start"]) < parse_iso(booking["window_start"])
    )


def skip(conn: sqlite3.Connection, row: sqlite3.Row, reason: str, now: datetime) -> None:
    """Not allowed to dial. A callback still owed to the caller goes to a person instead."""
    conn.execute(
        "UPDATE outbound SET status = 'skipped', result = ?, updated_at = ? WHERE id = ?",
        (reason, iso(now), row["id"]),
    )
    if row["kind"] == "callback" and reason in ("outside_hours", "not_the_caller", "not_claimed"):
        to_person(conn, row, f"the system couldn't place it ({reason})", now)


def to_person(conn: sqlite3.Connection, row: sqlite3.Row, why: str, now: datetime) -> None:
    """A callback the system couldn't complete is overdue at once and flagged with the reason, so
    a person picks it up and records.mark_overdue doesn't flag it a second time."""
    conn.execute(
        "UPDATE callbacks SET status = 'overdue', updated_at = ? WHERE id = ? AND status = 'pending'",
        (iso(now), row["callback_id"]),
    )
    records.add_flag(
        conn,
        row["call_id"],
        "callback_needs_person",
        f"Callback {row['callback_id']} needs a person: {why}",
        now,
        "High",
    )


def claim(conn: sqlite3.Connection, tenants: dict[str, Tenant], now: datetime) -> dict | None:
    """The next queued call to dial, marked as dialing, with what the voice layer needs to start it.
    Rows that may no longer be dialed are skipped on the way."""
    with tx(conn):
        rows = conn.execute(
            "SELECT * FROM outbound WHERE status = 'queued' AND due_at <= ? ORDER BY due_at, id",
            (iso(now),),
        ).fetchall()
        for row in rows:
            tenant = tenants[row["tenant"]]
            ok, reason = allowed(conn, tenant, row, now)
            if not ok:
                skip(conn, row, reason, now)
                continue
            placed = f"out-{row['id']}"
            conn.execute(
                "UPDATE outbound SET status = 'dialing', placed_call_id = ?, updated_at = ? "
                "WHERE id = ?",
                (placed, iso(now), row["id"]),
            )
            if row["callback_id"]:
                records.record_attempt(conn, row["callback_id"], now)
            return _describe(conn, tenant, row, placed, now)
    return None


def _describe(conn, tenant: Tenant, row: sqlite3.Row, placed: str, now: datetime) -> dict:
    data = {
        "outbound_id": row["id"],
        "kind": row["kind"],
        "call_id": placed,
        "phone": row["phone"],
        "dialed_from": tenant.agent["phone_numbers"][0],
        "about_call_id": row["call_id"],
    }
    if row["kind"] == "earlier_slot":
        offer = json.loads(row["offer_json"])
        booking = conn.execute(
            "SELECT * FROM bookings WHERE id = ?", (row["booking_id"],)
        ).fetchone()
        window = tenant.window(offer["window"])
        day = tenant.day_spoken(date.fromisoformat(offer["date"]), now)
        data["booking_id"] = booking["fsm_job_id"]
        data["offer"] = {"date": offer["date"], "window": offer["window"]}
        data["opening_line"] = tenant.line(
            "earlier_slot_offer", day=day, window=window.label() if window else offer["window"]
        )
    else:
        callback = conn.execute(
            "SELECT * FROM callbacks WHERE id = ?", (row["callback_id"],)
        ).fetchone()
        data["reason"] = callback["reason"]
        data["opening_line"] = tenant.line("callback_opening")
    return data


def finish(
    conn: sqlite3.Connection,
    tenant: Tenant,
    fsm: FsmAdapter,
    outbound_id: int,
    answered: bool,
    voicemail: bool,
    now: datetime,
) -> dict | None:
    """Settle a dialed call from what the voice layer reports. Returns None for an unknown or
    settled id.

    The voice layer reports as soon as the line is picked up, before anyone has spoken, so for a
    callback this only records that it was answered. How the callback went is decided when the
    call ends (settle_answered), from what the server saw on it. A voicemail, no answer or a failed
    call is settled here, since there is nothing more to see. Nothing in this module marks a
    callback done: the outcome goes on the call in ServiceTitan as a tag, and the callbacks job
    marks the callback done once the call log shows a call tagged resolved."""
    row = conn.execute("SELECT * FROM outbound WHERE id = ?", (outbound_id,)).fetchone()
    if not row or row["status"] != "dialing":
        return None
    answered = answered and not voicemail  # a voicemail is never a person
    result = "answered" if answered else "voicemail" if voicemail else "no_answer"
    with tx(conn):
        if row["kind"] == "earlier_slot" and answered:
            result = "accepted" if _accepted(conn, row, now) else "declined"
        conn.execute(
            "UPDATE outbound SET status = 'done', result = ?, updated_at = ? WHERE id = ?",
            (result, iso(now), outbound_id),
        )
    outcome = None
    if row["kind"] == "callback":
        outcome = settle_callback(conn, tenant, fsm, outbound_id, now)
    return {"outbound_id": outbound_id, "result": result, "outcome": outcome}


def settle_callback(
    conn: sqlite3.Connection,
    tenant: Tenant,
    fsm: FsmAdapter,
    outbound_id: int,
    now: datetime,
    force: bool = False,
) -> str | None:
    """Decide how a reported callback went and put it on the call as a tag. An answered callback
    waits for its call to end, unless force says the end is not coming. Returns the outcome, or
    None while it is still waiting or was settled before."""
    row = conn.execute("SELECT * FROM outbound WHERE id = ?", (outbound_id,)).fetchone()
    if (
        row["status"] != "done"
        or conn.execute(
            "SELECT 1 FROM call_outcomes WHERE outbound_id = ?", (outbound_id,)
        ).fetchone()
    ):
        return None
    answered = row["result"] == "answered"
    placed = records.get_call(conn, row["placed_call_id"])
    if answered and not force and (placed is None or not placed["ended_at"]):
        return None
    outcome, why = callback_outcome(conn, fsm, row, placed, answered, row["result"])
    with tx(conn):
        conn.execute(
            "INSERT INTO call_outcomes (outbound_id, sid, outcome, why, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (outbound_id, row["placed_call_id"], outcome, why, iso(now), iso(now)),
        )
        callback = conn.execute(
            "SELECT status FROM callbacks WHERE id = ?", (row["callback_id"],)
        ).fetchone()
        # A caller who said not to call again has no callback left to hand to anyone.
        if outcome != "resolved" and callback["status"] in ("pending", "overdue"):
            to_person(conn, row, f"{why} ({outcome.replace('_', ' ')})", now)
    apply_tag(conn, tenant, fsm, outbound_id, now)
    return outcome


def settle_answered(
    conn: sqlite3.Connection,
    tenant: Tenant,
    fsm: FsmAdapter,
    now: datetime,
    call_id: str | None = None,
) -> int:
    """Settle the answered callbacks whose call has ended. Given a call id, the call that has just
    ended; without one, every answered callback whose call ended, or whose end never came (the
    voice worker died) once it has been waiting as long as a dial may go unreported."""
    stuck = iso(now - timedelta(minutes=STUCK_DIALING_MINUTES))
    rows = conn.execute(
        "SELECT o.id, o.updated_at FROM outbound o WHERE o.tenant = ? AND o.kind = 'callback' "
        "AND o.status = 'done' AND o.result = 'answered' AND NOT EXISTS "
        "(SELECT 1 FROM call_outcomes c WHERE c.outbound_id = o.id) "
        "AND (? IS NULL OR o.placed_call_id = ?)",
        (tenant.slug, call_id, call_id),
    ).fetchall()
    settled = 0
    for row in rows:
        waited_too_long = call_id is None and row["updated_at"] < stuck
        settled += (
            settle_callback(conn, tenant, fsm, row["id"], now, force=waited_too_long) is not None
        )
    return settled


def callback_outcome(
    conn: sqlite3.Connection,
    fsm: FsmAdapter,
    row: sqlite3.Row,
    placed: sqlite3.Row | None,
    answered: bool,
    result: str,
) -> tuple[str, str]:
    """How a callback the system placed went, as (outcome, why). Resolved needs all three: a person
    answered (not a voicemail or no answer), the person who called is the one reached, and what the
    callback was for was handled on the call. Anything less is open, or no contact when nobody
    answered."""
    if not answered:
        return "no_contact", result
    callback = conn.execute(
        "SELECT * FROM callbacks WHERE id = ?", (row["callback_id"],)
    ).fetchone()
    if callback["status"] == "cancelled":
        return "open", "the caller asked not to be called"
    if placed is None or placed["caller_key"] != callback["phone_key"]:
        return "open", "the call was not to the number that asked"
    if not person_confirmed(conn, fsm, placed):
        return "open", "the person was not confirmed"
    if not reason_handled(conn, placed):
        return "open", "the reason for the callback was not handled"
    return "resolved", "the right person answered and the reason was handled"


def person_confirmed(conn: sqlite3.Connection, fsm: FsmAdapter, placed: sqlite3.Row) -> bool:
    """Did the person who answered confirm who they are? Either the agent's read-back was confirmed
    on the call, or the agent asked for a customer's name (matched on this number) and the next
    caller line, a short one, said yes. The number matching is checked by the caller."""
    if placed["readback_confirmed_at"]:
        return True
    names = []
    for customer_id in records.scoped_customers(conn, placed["id"]):
        try:
            names.append(fsm.customer(customer_id)["name"].split()[0].lower())
        except (FsmUnavailable, FsmRejected, KeyError, IndexError):
            continue
    turns = conn.execute(
        "SELECT speaker, text FROM turns WHERE call_id = ? ORDER BY seq", (placed["id"],)
    ).fetchall()
    for i, turn in enumerate(turns[:-1]):
        asked = turn["speaker"] == "agent" and "?" in turn["text"]
        if asked and any(re.search(rf"\b{re.escape(n)}\b", turn["text"].lower()) for n in names):
            reply = turns[i + 1]
            if reply["speaker"] == "caller":
                said = words(reply["text"])
                if (
                    len(said.split()) <= NAME_REPLY_MAX_WORDS
                    and any(has_phrase(said, w) for w in YES_WORDS)
                    and not any(has_phrase(said, w) for w in NO_WORDS)
                ):
                    return True
    return False


def reason_handled(conn: sqlite3.Connection, placed: sqlite3.Row) -> bool:
    """Was what the callback was for dealt with on the call? A booking, a reschedule or a cancel
    went through, or the agent ended the call saying it was resolved and the server saw nothing
    against that: the caller spoke, no person was flagged, and no further callback was promised."""
    call_id = placed["id"]
    if conn.execute(
        "SELECT 1 FROM bookings WHERE call_id = ? AND ((kind = 'book' AND status = 'booked') "
        "OR (kind IN ('reschedule', 'cancel') AND status = 'done'))",
        (call_id,),
    ).fetchone():
        return True
    said_resolved = conn.execute(
        "SELECT 1 FROM tool_calls WHERE call_id = ? AND tool = 'end_call' AND ok = 1 "
        "AND json_extract(args_json, '$.reason') = 'resolved'",
        (call_id,),
    ).fetchone()
    if not said_resolved or placed["safety_signal_at"]:
        return False
    spoke = conn.execute(
        "SELECT 1 FROM turns WHERE call_id = ? AND speaker = 'caller'", (call_id,)
    ).fetchone()
    escalated = conn.execute(
        "SELECT 1 FROM flags WHERE call_id = ? UNION SELECT 1 FROM callbacks WHERE call_id = ?",
        (call_id, call_id),
    ).fetchone()
    return bool(spoke) and not escalated


def apply_tag(
    conn: sqlite3.Connection, tenant: Tenant, fsm: FsmAdapter, outbound_id: int, now: datetime
) -> bool:
    """Put the outcome on the call in ServiceTitan as the tenant's tag for it. True once it has
    landed. A call ServiceTitan does not show yet, or an FSM that is down, leaves it to be tried
    again by the callbacks job (retry_tags)."""
    out = conn.execute(
        "SELECT o.*, c.phone, c.placed_call_id FROM call_outcomes o "
        "JOIN outbound c ON c.id = o.outbound_id WHERE o.outbound_id = ?",
        (outbound_id,),
    ).fetchone()
    if not out or out["tagged"]:
        return bool(out)
    placed = records.get_call(conn, out["placed_call_id"])
    landed = False
    try:
        started = parse_iso(placed["started_at"]) if placed else now
        if placed is not None:
            ended = parse_iso(placed["ended_at"]) if placed["ended_at"] else now
            scoped = list(records.scoped_customers(conn, placed["id"]))
            fsm.record_outbound_call(
                out["sid"],
                placed["dialed_number"],
                out["phone"],
                started,
                max(0, int((ended - started).total_seconds())),
                scoped[0] if len(scoped) == 1 else None,
            )
        call = fsm.find_outbound_call(out["sid"], out["phone"], started)
        if call is not None:
            fsm.tag_call(call["leadCall"]["id"], tenant.fsm["callback_tags"][out["outcome"]])
            landed = True
    except (FsmUnavailable, FsmRejected):
        landed = False
    conn.execute(
        "UPDATE call_outcomes SET tagged = ?, attempts = attempts + 1, updated_at = ? "
        "WHERE outbound_id = ?",
        (int(landed), iso(now), outbound_id),
    )
    return landed


def retry_tags(conn: sqlite3.Connection, tenant: Tenant, fsm: FsmAdapter, now: datetime) -> dict:
    """Try again the tags that have not landed, for TAG_RETRY_MINUTES after the call (a phone
    system may log a call some minutes late). A resolved callback whose tag never landed is flagged
    once for a person, since the callback cannot be marked done without it."""
    cutoff = iso(now - timedelta(minutes=TAG_RETRY_MINUTES))
    waiting = conn.execute(
        "SELECT o.outbound_id FROM call_outcomes o JOIN outbound b ON b.id = o.outbound_id "
        "WHERE b.tenant = ? AND o.tagged = 0 AND o.created_at >= ? ORDER BY o.outbound_id",
        (tenant.slug, cutoff),
    ).fetchall()
    landed = sum(apply_tag(conn, tenant, fsm, r["outbound_id"], now) for r in waiting)
    abandoned = conn.execute(
        "SELECT b.call_id, b.callback_id FROM call_outcomes o JOIN outbound b ON b.id = o.outbound_id "
        "WHERE b.tenant = ? AND o.tagged = 0 AND o.created_at < ? AND o.outcome = 'resolved' "
        "AND NOT EXISTS (SELECT 1 FROM flags f WHERE f.call_id = b.call_id "
        "AND f.kind = 'callback_tag_failed')",
        (tenant.slug, cutoff),
    ).fetchall()
    with tx(conn):
        for row in abandoned:
            records.add_flag(
                conn,
                row["call_id"],
                "callback_tag_failed",
                f"Callback {row['callback_id']} was resolved on the call but ServiceTitan never "
                "showed the call to tag. Tag the call in ServiceTitan, or close the callback by hand.",
                now,
                "High",
            )
    return {"tried": len(waiting), "tagged": landed, "flagged": len(abandoned)}


def _accepted(conn: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> bool:
    """The caller took the opening if the booking was moved on the placed call. The reschedule
    went through the normal tool and its gates, so the server, not the model, says it happened."""
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (row["booking_id"],)).fetchone()
    moved = conn.execute(
        "SELECT * FROM bookings WHERE call_id = ? AND kind = 'reschedule' AND status = 'done' "
        "AND fsm_job_id = ? ORDER BY id DESC",
        (row["placed_call_id"], booking["fsm_job_id"]),
    ).fetchone()
    if not moved:
        return False
    conn.execute(
        "UPDATE bookings SET window_start = ?, window_end = ?, seek_earlier = 0, updated_at = ? "
        "WHERE id = ?",
        (moved["window_start"], moved["window_end"], iso(now), booking["id"]),
    )
    conn.execute(
        "UPDATE flags SET status = 'done' WHERE call_id = ? AND kind = 'seek_earlier'",
        (booking["call_id"],),
    )
    return True
