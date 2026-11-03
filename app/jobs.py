"""Work that runs between calls: retrying queued bookings and leads, refreshing the slot cache,
flagging overdue callbacks, and the retention policy. The owner-view lane schedules more of these;
run_once is what the server's own timer runs."""

import json
import sqlite3
from datetime import datetime, timedelta

from . import booking, records
from .db import iso, tx
from .fsm import FsmAdapter, FsmRejected, FsmUnavailable, SlotCache
from .settings import QUEUE_MAX_ATTEMPTS
from .tenant import Tenant
from .tools import send_lead


def retry_queue(
    conn: sqlite3.Connection, tenant: Tenant, fsm: FsmAdapter, cache: SlotCache, now: datetime
) -> dict:
    """Send every queued booking and lead again with its original idempotency keys, so a booking
    that reached the FSM before the connection dropped is not made twice (spec S-48). One that
    still fails after QUEUE_MAX_ATTEMPTS, or that the FSM refuses, is flagged for a person."""
    counts = {"booked": 0, "still_queued": 0, "failed": 0, "leads_sent": 0}
    queued = conn.execute(
        "SELECT b.* FROM bookings b JOIN calls c ON c.id = b.call_id "
        "WHERE b.kind = 'book' AND b.status = 'queued' AND c.tenant = ? ORDER BY b.id",
        (tenant.slug,),
    ).fetchall()
    for row in queued:
        try:
            sent = booking.send(fsm, tenant, booking.request_of(row), row["fsm_key"])
        except FsmUnavailable as exc:
            final = row["attempts"] + 1 >= QUEUE_MAX_ATTEMPTS
            with tx(conn):
                booking.mark_attempt(
                    conn, row["id"], "failed" if final else "queued", str(exc), now
                )
                if final:
                    records.add_flag(
                        conn,
                        row["call_id"],
                        "queue_failed",
                        f"Queued booking {row['id']} still failing: {exc}",
                        now,
                        "High",
                    )
            counts["failed" if final else "still_queued"] += 1
            continue
        except FsmRejected as exc:
            with tx(conn):
                booking.mark_attempt(conn, row["id"], "failed", f"{exc.code}: {exc.detail}", now)
                records.add_flag(
                    conn,
                    row["call_id"],
                    "queue_failed",
                    f"Queued booking {row['id']} refused: {exc.code}",
                    now,
                    "High",
                )
            counts["failed"] += 1
            continue
        with tx(conn):
            booking.mark_booked(conn, row["id"], sent, now)
        cache.invalidate()
        counts["booked"] += 1
    leads = conn.execute(
        "SELECT l.* FROM leads l JOIN calls c ON c.id = l.call_id WHERE l.status = 'queued' AND c.tenant = ?",
        (tenant.slug,),
    ).fetchall()
    for row in leads:
        counts["leads_sent"] += send_lead(
            conn, fsm, row["id"], json.loads(row["request_json"]), row["fsm_key"], now
        )
    return counts


def purge(conn: sqlite3.Connection, tenant: Tenant, now: datetime, environment: str) -> dict:
    """Transcripts go after transcript_days and call records after call_record_days (spec S-45).
    Development keeps everything, because QA and debugging read full logs."""
    if environment == "development":
        return {"turns": 0, "calls": 0}
    transcripts = iso(now - timedelta(days=tenant.retention["transcript_days"]))
    records_before = iso(now - timedelta(days=tenant.retention["call_record_days"]))
    with tx(conn):
        turns = conn.execute(
            "DELETE FROM turns WHERE at < ? AND call_id IN (SELECT id FROM calls WHERE tenant = ?)",
            (transcripts, tenant.slug),
        ).rowcount
        old = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM calls WHERE tenant = ? AND started_at < ?",
                (tenant.slug, records_before),
            )
        ]
        for call_id in old:
            conn.execute("DELETE FROM tool_calls WHERE call_id = ?", (call_id,))
            conn.execute("DELETE FROM idempotency WHERE call_id = ?", (call_id,))
            conn.execute("DELETE FROM calls WHERE id = ?", (call_id,))
    return {"turns": turns, "calls": len(old)}


def run_once(services) -> dict:
    now = services.clock()
    conn = services.connect()
    report = {}
    try:
        for tenant in services.tenants:
            fsm, cache = services.fsm[tenant.slug], services.caches[tenant.slug]
            report[tenant.slug] = {
                "queue": retry_queue(conn, tenant, fsm, cache, now),
                "cache_refreshed": cache.refresh(),
                "purged": purge(conn, tenant, now, services.settings.environment),
            }
        with tx(conn):
            report["overdue_callbacks"] = records.mark_overdue(conn, now)
    finally:
        conn.close()
    return report
