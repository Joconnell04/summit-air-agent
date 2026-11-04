"""Reads and writes on the tool server's own tables: calls, turns, flags, callbacks, do-not-call."""

import json
import sqlite3
from datetime import datetime, time, timedelta

from .db import iso, parse_iso
from .settings import TAG_RETRY_MINUTES
from .tenant import Tenant
from .text import clock_time, phone_key

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


def thread_both_ways(conn: sqlite3.Connection, call_id: str) -> list[str]:
    """The call's thread plus any later calls linked back to it (a caller who called back)."""
    ids = thread(conn, call_id)
    frontier = list(ids)
    while frontier:
        later = [
            r["id"]
            for r in conn.execute(
                f"SELECT id FROM calls WHERE linked_call_id IN ({','.join('?' * len(frontier))})",
                frontier,
            )
            if r["id"] not in ids
        ]
        ids += later
        frontier = later
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


def last_caller_line(conn: sqlite3.Connection, call_id: str) -> str:
    row = conn.execute(
        "SELECT text FROM turns WHERE call_id = ? AND speaker = 'caller' ORDER BY seq DESC LIMIT 1",
        (call_id,),
    ).fetchone()
    return row["text"] if row else ""


def caller_turn_after(conn: sqlite3.Connection, call_id: str, seq: int) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM turns WHERE call_id = ? AND speaker = 'caller' AND seq > ?",
            (call_id, seq),
        ).fetchone()
    )


def last_said(conn: sqlite3.Connection, call_id: str, lines: list[str]) -> str | None:
    """Which of these lines the agent said most recently on this call, or None."""
    for row in conn.execute(
        "SELECT text FROM turns WHERE call_id = ? AND speaker = 'agent' ORDER BY seq DESC",
        (call_id,),
    ):
        for line in lines:
            if line in row["text"]:
                return line
    return None


def agent_said(conn: sqlite3.Connection, call_id: str, line: str) -> bool:
    """Whether an agent turn on this call's thread already contained the line."""
    ids = thread(conn, call_id)
    return any(
        line in r["text"]
        for r in conn.execute(
            f"SELECT text FROM turns WHERE speaker = 'agent' AND call_id IN "
            f"({','.join('?' * len(ids))})",
            ids,
        )
    )


def caller_text(conn: sqlite3.Connection, call_id: str) -> str:
    ids = thread(conn, call_id)
    rows = conn.execute(
        f"SELECT text FROM turns WHERE speaker = 'caller' AND call_id IN "
        f"({','.join('?' * len(ids))}) ORDER BY at, seq",
        ids,
    )
    return "\n".join(r["text"] for r in rows)


def thread_turns(conn: sqlite3.Connection, call_id: str) -> list[sqlite3.Row]:
    """Every line on the call's thread, oldest call first, each call's lines in order."""
    ids = list(reversed(thread(conn, call_id)))
    rows = conn.execute(
        f"SELECT call_id, seq, speaker, text FROM turns WHERE call_id IN "
        f"({','.join('?' * len(ids))})",
        ids,
    ).fetchall()
    return sorted(rows, key=lambda r: (ids.index(r["call_id"]), r["seq"]))


def wait_named(conn: sqlite3.Connection, call_id: str) -> tuple[str, int] | None:
    """The call and turn on the thread where the agent first offered a window or named the wait
    (find_slots answered), oldest first. Lines after it were said after the caller heard the wait."""
    ids = list(reversed(thread(conn, call_id)))
    rows = conn.execute(
        f"SELECT call_id, turn_seq FROM waits WHERE call_id IN ({','.join('?' * len(ids))})", ids
    ).fetchall()
    if not rows:
        return None
    first = min(rows, key=lambda r: (ids.index(r["call_id"]), r["turn_seq"]))
    return first["call_id"], first["turn_seq"]


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


def _clock_on(value: str, tenant: Tenant, now: datetime, days: int = 0,
              after: time | None = None) -> datetime:
    """A local clock time today, or `days` later: "16:30", "4:30 PM", "4 pm", or a bare hour read
    as daytime (text.clock_time)."""
    day = tenant.local(now).date() + timedelta(days=days)
    return datetime.combine(day, clock_time(value, after), tenant.tz)


def parse_callback_window(
    after: str | None, before: str | None, tenant: Tenant, now: datetime, days: int = 0
) -> tuple[datetime | None, datetime | None]:
    """The window a caller asked for ("after 5", "before 3", "between 8 and 2"), today or `days`
    later, as (earliest, latest). A bare hour is daytime, and the end of a range comes after its
    start, so "8 and 2" is 8 AM to 2 PM. Raises ValueError when it has passed, is backwards, or
    falls outside the hours the company calls back in (callbacks.outbound_hours)."""
    earliest = _clock_on(after, tenant, now, days) if after else None
    latest = (
        _clock_on(before, tenant, now, days, earliest.time() if earliest else None)
        if before
        else None
    )
    day = tenant.local(now).date() + timedelta(days=days)
    opens, closes = (
        datetime.combine(day, time.fromisoformat(t), tenant.tz)
        for t in tenant.callbacks["outbound_hours"]
    )
    if earliest and latest and earliest >= latest:
        raise ValueError("the window ends before it starts")
    if (latest or earliest) <= now:
        raise ValueError("that time has passed")
    if (earliest and earliest >= closes) or (latest and latest <= opens):
        raise ValueError("outside callback hours")
    return earliest, latest


def parse_callback_time(
    value: str | None, minutes: int | None, tenant: Tenant, now: datetime, days: int = 0
) -> datetime:
    """A named time: minutes from now, an ISO time with an offset, or a local clock time today or
    `days` later ("16:30", "4:30 PM", "4 pm", a bare "2" read as 2 PM). Raises ValueError for
    anything vague or in the past, because "shortly" is never a callback time (C-45)."""
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
            due = _clock_on(value, tenant, now, days)
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
    return _reachable(conn, tenant, callback["call_id"], callback["phone_key"], now)


def dial_allowed(
    conn: sqlite3.Connection, tenant: Tenant, call_id: str, phone: str, now: datetime
) -> tuple[bool, str]:
    """The same rules for a call that isn't a promised callback, such as an earlier opening."""
    if is_do_not_call(conn, phone):
        return False, "do_not_call"
    return _reachable(conn, tenant, call_id, phone_key(phone), now)


def in_outbound_hours(tenant: Tenant, now: datetime) -> bool:
    start, end = (time.fromisoformat(t) for t in tenant.callbacks["outbound_hours"])
    return start <= tenant.local(now).time() < end


def _reachable(
    conn: sqlite3.Connection, tenant: Tenant, call_id: str | None, key: str, now: datetime
) -> tuple[bool, str]:
    call = get_call(conn, call_id) if call_id else None
    if not call or call["caller_key"] != key:
        return False, "not_the_caller"
    if not in_outbound_hours(tenant, now):
        return False, "outside_hours"
    return True, "ok"


def record_attempt(conn: sqlite3.Connection, callback_id: int, now: datetime) -> None:
    conn.execute(
        "UPDATE callbacks SET attempts = attempts + 1, last_attempt_at = ?, updated_at = ? "
        "WHERE id = ?",
        (iso(now), iso(now), callback_id),
    )


def mark_overdue(conn: sqlite3.Connection, tenant: Tenant, now: datetime) -> list[int]:
    """Flag every pending callback whose time has passed (spec S-32). One the system is dialing is
    left to the dial, which hands it to a person if it fails, and so is one it answered whose call
    has not been judged yet, or was judged resolved with the tag still on its way (app/outbound.py)."""
    tag_cutoff = iso(now - timedelta(minutes=TAG_RETRY_MINUTES))
    due = conn.execute(
        "SELECT c.* FROM callbacks c JOIN calls k ON k.id = c.call_id WHERE k.tenant = ? "
        "AND c.status = 'pending' AND c.due_at < ? AND NOT EXISTS "
        "(SELECT 1 FROM outbound o WHERE o.callback_id = c.id AND ("
        "o.status IN ('waiting', 'queued', 'dialing') "
        "OR (o.status = 'done' AND o.result = 'answered' AND NOT EXISTS "
        "(SELECT 1 FROM call_outcomes x WHERE x.outbound_id = o.id)) "
        "OR EXISTS (SELECT 1 FROM call_outcomes x WHERE x.outbound_id = o.id "
        "AND x.outcome = 'resolved' AND x.tagged = 0 AND x.created_at >= ?)))",
        (tenant.slug, iso(now), tag_cutoff),
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
            f"Callback promised by {tenant.time_spoken(parse_iso(row['due_at']), now)} not done: "
            f"{row['reason']}",
            now,
            "High",
        )
    return [r["id"] for r in due]


def recent_calls(
    conn: sqlite3.Connection, tenant: str, caller_key: str, before: datetime, since: datetime
) -> list[sqlite3.Row]:
    """Calls from this number to this tenant since a moment, newest first. Only this tenant's, so a
    call to another company on the same server is never linked or resumed."""
    return conn.execute(
        "SELECT * FROM calls WHERE tenant = ? AND caller_key = ? AND started_at >= ? "
        "AND started_at < ? ORDER BY started_at DESC",
        (tenant, caller_key, iso(since), iso(before)),
    ).fetchall()


def dropped(call: sqlite3.Row) -> bool:
    """Ended without the agent ending it: the line dropped or the caller hung up mid-call."""
    return bool(call["ended_at"]) and not call["end_requested"]


def readback(call: sqlite3.Row) -> dict | None:
    return json.loads(call["readback_json"]) if call["readback_json"] else None


def recent_caller_lines(conn: sqlite3.Connection, call_id: str, limit: int) -> list[tuple[int, str]]:
    """The caller's last few lines, oldest first, as (seq, text)."""
    rows = conn.execute(
        "SELECT seq, text FROM turns WHERE call_id = ? AND speaker = 'caller' "
        "ORDER BY seq DESC LIMIT ?",
        (call_id, limit),
    ).fetchall()
    return [(r["seq"], r["text"]) for r in reversed(rows)]


def rewrite_caller_lines(
    conn: sqlite3.Connection, call_id: str, changes: dict[int, str], olds: dict[str, str]
) -> None:
    """Earlier caller lines found to hold part of a card (app/cards.py): the turn and the tool
    log's record of the request that posted it get the redacted text."""
    for seq, text in changes.items():
        conn.execute(
            "UPDATE turns SET text = ? WHERE call_id = ? AND seq = ? AND speaker = 'caller'",
            (text, call_id, seq),
        )
    for old, new in olds.items():
        conn.execute(
            "UPDATE tool_calls SET args_json = json_set(args_json, '$.text', ?) "
            "WHERE call_id = ? AND tool = 'turn' AND json_extract(args_json, '$.text') = ?",
            (new, call_id, old),
        )
