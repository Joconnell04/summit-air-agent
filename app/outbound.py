"""Calls the system places (journey 12). The background jobs decide what to dial: an earlier
opening for a flagged urgent booking, or a callback the caller asked for once its time comes. The
voice layer claims the next one, dials it as an outbound call through the normal lifecycle, and
reports whether it was answered. The server then reads what happened on that call: a reschedule of
the booking means the opening was accepted.

The rules are the same as every outbound call (spec S-42): only the person who called about it,
inside the outbound hours, one attempt, never a do-not-call number. They are checked again at
claim time, because a number can be marked do-not-call between scheduling and dialing.
"""

import json
import sqlite3
from datetime import date, datetime

from . import records
from .db import iso, parse_iso, tx
from .tenant import Tenant


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
    conn: sqlite3.Connection, outbound_id: int, answered: bool, voicemail: bool, now: datetime
) -> dict | None:
    """Settle a dialed call from what happened on it. Returns None for an unknown or settled id."""
    row = conn.execute("SELECT * FROM outbound WHERE id = ?", (outbound_id,)).fetchone()
    if not row or row["status"] != "dialing":
        return None
    result = "answered" if answered else "voicemail" if voicemail else "no_answer"
    with tx(conn):
        if row["kind"] == "earlier_slot":
            if answered:
                result = "accepted" if _accepted(conn, row, now) else "declined"
        elif answered:
            conn.execute(
                "UPDATE callbacks SET status = 'done', updated_at = ? WHERE id = ? "
                "AND status IN ('pending', 'overdue')",
                (iso(now), row["callback_id"]),
            )
            conn.execute(
                "UPDATE flags SET status = 'done' WHERE call_id = ? AND kind = 'callback_requested'",
                (row["call_id"],),
            )
        else:
            to_person(conn, row, f"not answered ({result})", now)
        conn.execute(
            "UPDATE outbound SET status = 'done', result = ?, updated_at = ? WHERE id = ?",
            (result, iso(now), outbound_id),
        )
    return {"outbound_id": outbound_id, "result": result}


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
