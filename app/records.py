"""Reads and writes on the tool server's own tables: calls, turns, flags, callbacks, do-not-call."""

import json
import re
import sqlite3
from datetime import datetime, time, timedelta

from .db import iso
from .tenant import Tenant
from .text import phone_key

CALLBACK_MAX_DAYS = 7


# ---- calls ---------------------------------------------------------------------------------


def get_call(conn: sqlite3.Connection, call_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()


def update_call(conn: sqlite3.Connection, call_id: str, **fields) -> None:
    columns = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(f"UPDATE calls SET {columns} WHERE id = ?", (*fields.values(), call_id))


def thread(conn: sqlite3.Connection, call_id: str) -> list[str]:
    """This call and the calls it was linked to (a caller who dropped and called back), newest
    first. Limits and bookings count across the thread, so calling back never books twice."""
    ids, current = [], call_id
    while current and current not in ids:
        ids.append(current)
        row = conn.execute("SELECT linked_call_id FROM calls WHERE id = ?", (current,)).fetchone()
        current = row["linked_call_id"] if row else None
    return ids


def scoped_customers(conn: sqlite3.Connection, call_id: str) -> dict[int, sqlite3.Row]:
    """Customers matched on this call or its thread. Every customer read and write is limited to
    these (spec S-39)."""
    ids = thread(conn, call_id)
    rows = conn.execute(
        f"SELECT * FROM call_customers WHERE call_id IN ({','.join('?' * len(ids))})", ids
    ).fetchall()
    return {r["customer_id"]: r for r in rows}


def add_scope(
    conn: sqlite3.Connection, call_id: str, customer_id: int, matched_by: str, customer_type: str
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO call_customers (call_id, customer_id, matched_by, customer_type) "
        "VALUES (?, ?, ?, ?)",
        (call_id, customer_id, matched_by, customer_type),
    )


def caller_turn_after(conn: sqlite3.Connection, call_id: str, seq: int) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM turns WHERE call_id = ? AND speaker = 'caller' AND seq > ?",
            (call_id, seq),
        ).fetchone()
    )


def caller_text(conn: sqlite3.Connection, call_id: str) -> str:
    ids = thread(conn, call_id)
    rows = conn.execute(
        f"SELECT text FROM turns WHERE speaker = 'caller' AND call_id IN "
        f"({','.join('?' * len(ids))}) ORDER BY at, seq",
        ids,
    )
    return "\n".join(r["text"] for r in rows)


def count_actions(conn: sqlite3.Connection, call_id: str, kind: str) -> int:
    ids = thread(conn, call_id)
    return conn.execute(
        f"SELECT COUNT(*) FROM bookings WHERE kind = ? AND status <> 'failed' "
        f"AND call_id IN ({','.join('?' * len(ids))})",
        (kind, *ids),
    ).fetchone()[0]


def count_tool(conn: sqlite3.Connection, call_id: str, tool: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM tool_calls WHERE call_id = ? AND tool = ?", (call_id, tool)
    ).fetchone()[0]


# ---- flags and callbacks -------------------------------------------------------------------


def add_flag(
    conn: sqlite3.Connection,
    call_id: str | None,
    kind: str,
    reason: str,
    now: datetime,
    priority: str = "Normal",
) -> int:
    cur = conn.execute(
        "INSERT INTO flags (call_id, kind, reason, priority, created_at) VALUES (?, ?, ?, ?, ?)",
        (call_id, kind, reason, priority, iso(now)),
    )
    return cur.lastrowid


def parse_callback_time(
    value: str | None, minutes: int | None, tenant: Tenant, now: datetime
) -> datetime:
    """A named time: minutes from now, an ISO time with an offset, or a local clock time today
    ("16:30", "4:30 PM", "4 pm"). Raises ValueError for anything vague or in the past, because
    "shortly" is never a callback time (C-45)."""
    if minutes is not None:
        due = now + timedelta(minutes=minutes)
    elif value:
        value = value.strip()
        try:
            due = datetime.fromisoformat(value)
        except ValueError:
            due = None
        if due is not None and due.tzinfo is None:
            raise ValueError("an ISO time needs a UTC offset")
        if due is None:
            match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?", value.lower())
            if not match:
                raise ValueError("not a time")
            hour, minute = int(match[1]), int(match[2] or 0)
            if match[3]:
                if not 1 <= hour <= 12:
                    raise ValueError("not a time")
                hour = hour % 12 + (12 if match[3].startswith("p") else 0)
            if hour > 23 or minute > 59:
                raise ValueError("not a time")
            due = datetime.combine(tenant.local(now).date(), time(hour, minute), tenant.tz)
    else:
        raise ValueError("no callback time given")
    if due <= now:
        raise ValueError("that time has passed")
    if due > now + timedelta(days=CALLBACK_MAX_DAYS):
        raise ValueError("too far ahead")
    return due


def is_do_not_call(conn: sqlite3.Connection, phone: str) -> bool:
    key = phone_key(phone)
    return bool(conn.execute("SELECT 1 FROM do_not_call WHERE phone_key = ?", (key,)).fetchone())


def add_callback(
    conn: sqlite3.Connection,
    tenant: Tenant,
    call: sqlite3.Row,
    due: datetime,
    reason: str,
    now: datetime,
) -> int | None:
    """Promise a callback to the caller's number. None when the number is on the do-not-call list,
    since nobody may call it."""
    if is_do_not_call(conn, call["caller_id"]):
        return None
    owner = tenant.callbacks["owner_in_hours" if tenant.is_open(due) else "owner_after_hours"]
    cur = conn.execute(
        """INSERT INTO callbacks (call_id, phone, phone_key, due_at, owner, reason, created_at,
                                  updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            call["id"],
            call["caller_id"],
            call["caller_key"],
            iso(due),
            owner,
            reason,
            iso(now),
            iso(now),
        ),
    )
    return cur.lastrowid


def mark_do_not_call(conn: sqlite3.Connection, call: sqlite3.Row, now: datetime) -> int:
    """Add the caller's number and cancel every pending callback to it. Returns how many were
    cancelled."""
    conn.execute(
        "INSERT OR IGNORE INTO do_not_call (phone_key, phone, call_id, added_at) VALUES (?, ?, ?, ?)",
        (call["caller_key"], call["caller_id"], call["id"], iso(now)),
    )
    return conn.execute(
        "UPDATE callbacks SET status = 'cancelled', updated_at = ? "
        "WHERE phone_key = ? AND status IN ('pending', 'overdue')",
        (iso(now), call["caller_key"]),
    ).rowcount


def outbound_allowed(
    conn: sqlite3.Connection, tenant: Tenant, callback: sqlite3.Row, now: datetime
) -> tuple[bool, str]:
    """May the system place this callback now? Only to the person who called about it, within the
    outbound hours, one attempt, never to a do-not-call number (spec S-42)."""
    if callback["status"] not in ("pending", "overdue"):
        return False, callback["status"]
    if is_do_not_call(conn, callback["phone"]):
        return False, "do_not_call"
    if callback["attempts"] >= tenant.callbacks["max_attempts"]:
        return False, "attempted"
    call = get_call(conn, callback["call_id"]) if callback["call_id"] else None
    if not call or call["caller_key"] != callback["phone_key"]:
        return False, "not_the_caller"
    start, end = (time.fromisoformat(t) for t in tenant.callbacks["outbound_hours"])
    if not start <= tenant.local(now).time() < end:
        return False, "outside_hours"
    return True, "ok"


def record_attempt(conn: sqlite3.Connection, callback_id: int, now: datetime) -> None:
    conn.execute(
        "UPDATE callbacks SET attempts = attempts + 1, last_attempt_at = ?, updated_at = ? "
        "WHERE id = ?",
        (iso(now), iso(now), callback_id),
    )


def mark_overdue(conn: sqlite3.Connection, now: datetime) -> list[int]:
    """Flag every pending callback whose time has passed (spec S-32)."""
    due = conn.execute(
        "SELECT * FROM callbacks WHERE status = 'pending' AND due_at < ?", (iso(now),)
    ).fetchall()
    for row in due:
        conn.execute(
            "UPDATE callbacks SET status = 'overdue', updated_at = ? WHERE id = ?",
            (iso(now), row["id"]),
        )
        add_flag(
            conn,
            row["call_id"],
            "overdue_callback",
            f"Callback promised by {row['due_at']} not done: {row['reason']}",
            now,
            "High",
        )
    return [r["id"] for r in due]


def recent_calls(
    conn: sqlite3.Connection, caller_key: str, before: datetime, minutes: int
) -> list[sqlite3.Row]:
    since = iso(before - timedelta(minutes=minutes))
    return conn.execute(
        "SELECT * FROM calls WHERE caller_key = ? AND started_at >= ? AND started_at < ? "
        "ORDER BY started_at DESC",
        (caller_key, since, iso(before)),
    ).fetchall()


def dropped(call: sqlite3.Row) -> bool:
    """Ended without the agent ending it: the line dropped or the caller hung up mid-call."""
    return bool(call["ended_at"]) and not call["end_requested"]


def readback(call: sqlite3.Row) -> dict | None:
    return json.loads(call["readback_json"]) if call["readback_json"] else None
