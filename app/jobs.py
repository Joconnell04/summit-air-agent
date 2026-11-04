"""Work that runs between calls: retrying queued bookings and leads, offering earlier openings to
flagged urgent bookings, queuing the callbacks the system places, flagging overdue callbacks,
checking which bookings were kept, emailing the on-call person after hours, refreshing the slot
cache, the retention policy, and the technician's summary of each booked call (app/summary.py).
run_once is what the server's own timer runs."""

import json
import logging
import smtplib
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import (
    booking,
    outbound,
    owner_settings,
    recordings,
    records,
    reports,
    search,
    summary,
    weather,
)
from .db import iso, parse_iso, tx
from .fsm import FsmAdapter, FsmRejected, FsmUnavailable, SlotCache
from .mail import Mailer
from .settings import ALERT_MAX_ATTEMPTS, QUEUE_MAX_ATTEMPTS, STUCK_DIALING_MINUTES
from .tenant import Tenant
from .tools import open_windows, send_lead

log = logging.getLogger(__name__)

STUCK_SENDING_MINUTES = 5
# A queued call nobody claimed (the voice worker is down) expires after this and goes to a person.
UNCLAIMED_MINUTES = 15
# FSM reads per run for the booked-and-kept check, so a backlog can't hold up the other jobs.
KEPT_CHECKS_PER_RUN = 50
# The safety call's flags: emailed after hours whatever the owner's on-call alerts setting says.
SAFETY_FLAG_KINDS = ("safety", "safety_dropped")


def retry_queue(
    conn: sqlite3.Connection, tenant: Tenant, fsm: FsmAdapter, cache: SlotCache, now: datetime
) -> dict:
    """Send every queued booking and lead again with its original idempotency keys, so a booking
    that reached the FSM before the connection dropped is not made twice (spec S-48). One that
    still fails after QUEUE_MAX_ATTEMPTS, or that the FSM refuses, is flagged for a person."""
    counts = {"booked": 0, "still_queued": 0, "failed": 0, "leads_sent": 0}
    queued = conn.execute(
        "SELECT b.* FROM bookings b JOIN calls c ON c.id = b.call_id "
        "WHERE b.kind = 'book' AND c.tenant = ? AND (b.status = 'queued' "
        # A live request that died mid-send (a crash) is picked up once it is clearly abandoned.
        "OR (b.status = 'sending' AND b.updated_at < ?)) ORDER BY b.id",
        (tenant.slug, iso(now - timedelta(minutes=STUCK_SENDING_MINUTES))),
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


def offer_earlier_slots(
    conn: sqlite3.Connection, tenant: Tenant, fsm: FsmAdapter, cache: SlotCache, now: datetime
) -> int:
    """Journey 12: an urgent booking flagged for an earlier slot is offered the first opening
    before its window, by a call the system places. Each opening goes to one booking at a time, the
    oldest flag first, and each booking gets one offer call (spec S-25, S-42). Returns offers made."""
    if not records.in_outbound_hours(tenant, now):
        return 0
    waiting = conn.execute(
        "SELECT b.* FROM bookings b JOIN calls c ON c.id = b.call_id "
        "WHERE c.tenant = ? AND b.kind = 'book' AND b.status = 'booked' AND b.seek_earlier = 1 "
        "AND b.window_start > ? AND NOT EXISTS (SELECT 1 FROM outbound o WHERE o.booking_id = b.id) "
        "ORDER BY b.created_at, b.id",
        (tenant.slug, iso(now)),
    ).fetchall()
    offered = {
        (o["date"], o["window"])
        for o in (
            json.loads(r["offer_json"])
            for r in conn.execute(
                "SELECT offer_json FROM outbound WHERE tenant = ? AND kind = 'earlier_slot' "
                "AND status IN ('queued', 'dialing')",
                (tenant.slug,),
            )
        )
    }
    made = 0
    for row in waiting:
        job_type = tenant.job_types.get(row["job_type"])
        call = records.get_call(conn, row["call_id"])
        if not job_type or not call:
            continue
        allowed, _ = records.dial_allowed(conn, tenant, call["id"], call["caller_id"], now)
        if not allowed:
            continue
        try:
            windows = open_windows(tenant, fsm, cache, now, job_type, row["priority"])
        except (FsmUnavailable, FsmRejected):
            return made
        # An after-hours window is the on-call technician's to give, never an automatic offer.
        earlier = [
            w
            for w in windows
            if not w["after_hours"]
            and parse_iso(w["start"]) < parse_iso(row["window_start"])
            and (w["date"], w["window"]) not in offered
        ]
        if not earlier:
            continue
        offer = {k: earlier[0][k] for k in ("date", "window", "start", "end")}
        with tx(conn):
            outbound.add_offer(conn, tenant, row, call["caller_id"], offer, now)
        offered.add((offer["date"], offer["window"]))
        made += 1
    return made


def schedule_callbacks(conn: sqlite3.Connection, tenant: Tenant, now: datetime) -> dict:
    """A callback the system places waits until its time, then is queued for the voice layer if it
    may still be dialed; one that may not goes to a person. A dialed call never reported on is
    settled as no_report, and one nobody claims expires; either way its callback goes to a person."""
    counts = {"queued": 0, "skipped": 0, "no_report": 0, "not_claimed": 0}
    with tx(conn):
        due = conn.execute(
            "SELECT * FROM outbound WHERE tenant = ? AND status = 'waiting' AND due_at <= ?",
            (tenant.slug, iso(now)),
        ).fetchall()
        for row in due:
            allowed, reason = outbound.allowed(conn, tenant, row, now)
            if allowed:
                conn.execute(
                    "UPDATE outbound SET status = 'queued', updated_at = ? WHERE id = ?",
                    (iso(now), row["id"]),
                )
                counts["queued"] += 1
            else:
                outbound.skip(conn, row, reason, now)
                counts["skipped"] += 1
        stuck = conn.execute(
            "SELECT * FROM outbound WHERE tenant = ? AND status = 'dialing' AND updated_at < ?",
            (tenant.slug, iso(now - timedelta(minutes=STUCK_DIALING_MINUTES))),
        ).fetchall()
        for row in stuck:
            conn.execute(
                "UPDATE outbound SET status = 'done', result = 'no_report', updated_at = ? "
                "WHERE id = ?",
                (iso(now), row["id"]),
            )
            if row["kind"] == "callback":
                outbound.to_person(conn, row, "dialed but never reported back", now)
            counts["no_report"] += 1
        # Queued but never claimed: the voice worker is down or not polling.
        unclaimed = conn.execute(
            "SELECT * FROM outbound WHERE tenant = ? AND status = 'queued' AND updated_at < ?",
            (tenant.slug, iso(now - timedelta(minutes=UNCLAIMED_MINUTES))),
        ).fetchall()
        for row in unclaimed:
            outbound.skip(conn, row, "not_claimed", now)
            counts["not_claimed"] += 1
    return counts


# Open callbacks looked up in the call log per run, so a backlog can't hold up the other jobs, and
# how far back a callback is still looked for (a promise may be at most a week out, plus a late return).
CALLBACK_CHECKS_PER_RUN = 50
CALLBACK_LOOKBACK_DAYS = 14
# The flags a callback leaves on its call, closed together with it.
CALLBACK_FLAG_KINDS = (
    "callback_requested",
    "billing",
    "person",
    "overdue_callback",
    "callback_needs_person",
    "callback_tag_failed",
)


def confirm_callbacks(
    conn: sqlite3.Connection, tenant: Tenant, fsm: FsmAdapter, now: datetime
) -> dict:
    """A promised callback is done when ServiceTitan's call log shows an outbound call to that
    number after the promise, tagged as resolved (decision 010). A call that failed, went to voicemail
    or reached the wrong person is never tagged resolved, so it never marks a callback done. The
    office tags the calls it makes; outbound.finish tags the ones the system places. One resolved
    call settles every open callback promised to that number before it."""
    counts = {"checked": 0, "done": 0}
    resolved_tag = tenant.fsm["callback_tags"]["resolved"]
    rows = conn.execute(
        "SELECT cb.* FROM callbacks cb JOIN calls c ON c.id = cb.call_id "
        "LEFT JOIN callback_checks k ON k.callback_id = cb.id "
        "WHERE c.tenant = ? AND cb.status IN ('pending', 'overdue') AND cb.created_at >= ? "
        "ORDER BY COALESCE(k.checked_at, ''), cb.id LIMIT ?",
        (
            tenant.slug,
            iso(now - timedelta(days=CALLBACK_LOOKBACK_DAYS)),
            CALLBACK_CHECKS_PER_RUN,
        ),
    ).fetchall()
    by_phone: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_phone.setdefault(row["phone"], []).append(row)
    for phone, callbacks in by_phone.items():
        # Stamped before the read, so a number ServiceTitan keeps refusing goes to the back of the
        # line instead of holding the front of it.
        with tx(conn):
            for cb in callbacks:
                conn.execute(
                    "INSERT INTO callback_checks (callback_id, checked_at) VALUES (?, ?) "
                    "ON CONFLICT (callback_id) DO UPDATE SET checked_at = excluded.checked_at",
                    (cb["id"], iso(now)),
                )
        try:
            calls = fsm.calls_to(phone, parse_iso(min(cb["created_at"] for cb in callbacks)))
        except FsmUnavailable:
            break
        except FsmRejected:
            continue
        resolved = [
            parse_iso(c["leadCall"]["receivedOn"])
            for c in calls
            if resolved_tag in [t["typeName"] for t in c["leadCall"].get("tags") or []]
        ]
        for cb in callbacks:
            counts["checked"] += 1
            if not any(at >= parse_iso(cb["created_at"]) for at in resolved):
                continue
            with tx(conn):
                conn.execute(
                    "UPDATE callbacks SET status = 'done', updated_at = ? WHERE id = ? "
                    "AND status IN ('pending', 'overdue')",
                    (iso(now), cb["id"]),
                )
                conn.execute(
                    f"UPDATE flags SET status = 'done' WHERE call_id = ? AND status = 'open' "
                    f"AND kind IN ({','.join('?' * len(CALLBACK_FLAG_KINDS))})",
                    (cb["call_id"], *CALLBACK_FLAG_KINDS),
                )
            counts["done"] += 1
    return counts


def check_kept(conn: sqlite3.Connection, tenant: Tenant, fsm: FsmAdapter, now: datetime) -> dict:
    """Booked-and-kept (C-42): once a booking's current appointment has started, record whether the
    job was still on the schedule or had been cancelled. A booking moved later waits for its new
    window. Reads the FSM, so it is capped per run and stops when the FSM is down."""
    counts = {"kept": 0, "cancelled": 0}
    rows = conn.execute(
        "SELECT b.* FROM bookings b JOIN calls c ON c.id = b.call_id "
        "WHERE c.tenant = ? AND b.kind = 'book' AND b.status = 'booked' AND b.window_start <= ? "
        "AND NOT EXISTS (SELECT 1 FROM kept k WHERE k.booking_id = b.id) ORDER BY b.window_start "
        "LIMIT ?",
        (tenant.slug, iso(now), KEPT_CHECKS_PER_RUN),
    ).fetchall()
    for row in rows:
        try:
            job = fsm.job(row["fsm_job_id"])
            if job["jobStatus"] != "Canceled":
                live = [a for a in fsm.appointments(row["fsm_job_id"]) if a["status"] != "Canceled"]
                if live and parse_iso(live[-1]["arrivalWindowStart"]) > now:
                    continue
        except FsmUnavailable:
            break
        except FsmRejected:
            continue
        kept = job["jobStatus"] != "Canceled"
        with tx(conn):
            conn.execute(
                "INSERT INTO kept (booking_id, kept, fsm_status, checked_at) VALUES (?, ?, ?, ?)",
                (row["id"], int(kept), job["jobStatus"], iso(now)),
            )
        counts["kept" if kept else "cancelled"] += 1
    return counts


def alert_on_call(
    conn: sqlite3.Connection,
    tenant: Tenant,
    mailer: Mailer,
    now: datetime,
    owner_view_url: str = "",
) -> dict:
    """Email the on-call person about each urgent flag raised while the company is closed: what,
    where, who and why (journey 11). A send that fails is tried again on the next runs. With the
    owner's on-call alerts setting off for the call, only safety flags are sent."""
    cfg = tenant.on_call_alerts
    since = iso(now - timedelta(minutes=cfg["max_age_minutes"]))
    marks = ",".join("?" * len(cfg["priorities"]))
    flags = conn.execute(
        f"SELECT f.* FROM flags f JOIN calls c ON c.id = f.call_id WHERE c.tenant = ? "
        f"AND f.priority IN ({marks}) AND f.created_at >= ? "
        f"AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.flag_id = f.id) ORDER BY f.id",
        (tenant.slug, *cfg["priorities"], since),
    ).fetchall()
    retry = conn.execute(
        "SELECT a.*, f.call_id FROM alerts a JOIN flags f ON f.id = a.flag_id "
        "JOIN calls c ON c.id = f.call_id WHERE c.tenant = ? AND a.status = 'failed' "
        "AND a.attempts < ?",
        (tenant.slug, ALERT_MAX_ATTEMPTS),
    ).fetchall()
    counts = {"sent": 0, "held": 0, "failed": 0}
    for flag in flags:
        if flag["kind"] in cfg["skip_kinds"] or tenant.is_open(parse_iso(flag["created_at"])):
            continue
        if (
            flag["kind"] not in SAFETY_FLAG_KINDS
            and not owner_settings.for_call(conn, tenant, flag["call_id"])["on_call_alerts"]
        ):
            continue
        subject, body = _alert_text(conn, tenant, flag, owner_view_url)
        status, error = _send(mailer, cfg["email"], subject, body)
        with tx(conn):
            conn.execute(
                "INSERT INTO alerts (flag_id, to_address, subject, body, status, attempts, error, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (flag["id"], cfg["email"], subject, body, status, error, iso(now), iso(now)),
            )
        counts[status] += 1
    for alert in retry:
        status, error = _send(mailer, alert["to_address"], alert["subject"], alert["body"])
        with tx(conn):
            conn.execute(
                "UPDATE alerts SET status = ?, attempts = attempts + 1, error = ?, updated_at = ? "
                "WHERE id = ?",
                (status, error, iso(now), alert["id"]),
            )
        counts[status] += 1
    return counts


def _send(mailer: Mailer, to: str, subject: str, body: str) -> tuple[str, str | None]:
    try:
        return mailer.send(to, subject, body), None
    except (OSError, ValueError, smtplib.SMTPException) as exc:
        return "failed", f"{type(exc).__name__}: {exc}"


def _alert_text(
    conn: sqlite3.Connection, tenant: Tenant, flag: sqlite3.Row, owner_view_url: str
) -> tuple[str, str]:
    call = records.get_call(conn, flag["call_id"])
    readback = records.readback(call) or {}
    where = ", ".join(p for p in (readback.get("street"), readback.get("zip")) if p)
    callback = conn.execute(
        "SELECT due_at FROM callbacks WHERE call_id = ? AND status IN ('pending', 'overdue') "
        "ORDER BY due_at LIMIT 1",
        (call["id"],),
    ).fetchone()
    local = tenant.local(parse_iso(flag["created_at"]))
    lines = [
        f"What: {flag['reason']}",
        f"Where: {where or 'no address confirmed on the call'}",
        f"Who: {call['caller_id']}",
        f"Why urgent: {flag['kind'].replace('_', ' ')}, raised {local.strftime('%-I:%M %p')}",
    ]
    if callback:
        due = tenant.local(parse_iso(callback["due_at"])).strftime("%-I:%M %p")
        lines.append(f"Caller was told: someone will call by {due}")
    if owner_view_url:
        lines.append(f"Call: {owner_view_url.rstrip('/')}/owner/calls/{call['id']}")
    subject = " ".join(f"{tenant.name} urgent: {flag['reason']}".split())[:120]
    return subject, "\n".join(lines)


def purge(
    conn: sqlite3.Connection, tenant: Tenant, now: datetime, environment: str, folder: Path
) -> dict:
    """Transcripts and recordings go after transcript_days, call records after call_record_days
    (spec S-45). Development keeps everything, because QA and debugging read full logs."""
    if environment == "development":
        return {"turns": 0, "recordings": 0, "calls": 0}
    audio = recordings.purge(
        conn, tenant, folder, now - timedelta(days=tenant.retention["transcript_days"]), now
    )
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
    return {"turns": turns, "recordings": audio, "calls": len(old)}


def _guarded(job, *args, **kwargs):
    """Run a job whose failure must not stop the jobs after it (the overdue and alert jobs matter
    more): ServiceTitan's call log replies it does not understand, or the search index."""
    try:
        return job(*args, **kwargs)
    except Exception as exc:
        log.exception("job failed")
        return {"error": f"{type(exc).__name__}: {exc}"}


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
                "weather": (
                    weather.refresh(conn, tenant, services.weather[tenant.slug], now)
                    if services.weather.get(tenant.slug)
                    else "off"
                ),
                "earlier_slots_offered": offer_earlier_slots(conn, tenant, fsm, cache, now),
                "callbacks": schedule_callbacks(conn, tenant, now),
                "callbacks_settled": _guarded(outbound.settle_answered, conn, tenant, fsm, now),
                "call_tags": _guarded(outbound.retry_tags, conn, tenant, fsm, now),
                "callbacks_done": _guarded(confirm_callbacks, conn, tenant, fsm, now),
                "kept": check_kept(conn, tenant, fsm, now),
                "alerts": alert_on_call(
                    conn, tenant, services.mailer, now, services.settings.owner_view_url
                ),
                "reports": reports.file_held(conn, tenant, services.report_poster, now),
                "search_indexed": _guarded(search.refresh, conn, tenant),
                "recordings": _guarded(
                    recordings.collect,
                    conn,
                    tenant,
                    services.bucket,
                    services.settings.recordings_dir,
                    now,
                ),
                "purged": purge(
                    conn,
                    tenant,
                    now,
                    services.settings.environment,
                    services.settings.recordings_dir,
                ),
            }
        report["overdue_callbacks"] = []
        for tenant in services.tenants:
            with tx(conn):
                report["overdue_callbacks"] += records.mark_overdue(conn, tenant, now)
        # Last, so a slow model call never holds up the jobs above. One deadline for every
        # tenant, so the run's summary time doesn't grow with the number of tenants.
        deadline = time.monotonic() + summary.RUN_SECONDS
        for tenant in services.tenants:
            report[tenant.slug]["summaries"] = _guarded(
                summary.run, conn, tenant, services.fsm[tenant.slug], services.summary_model, now,
                deadline=deadline,
            )
    finally:
        conn.close()
    return report
