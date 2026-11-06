"""The Results page's cost estimate: what the agent costs a month, from five inputs the owner can
slide, each starting at a value suggested from this company's own recent calls.

The rates are the socket build's, MEASURED on 65 calls to (516) on 5 and 6 November 2026 from
Retell's `combined_cost` and the socket's token log (docs/cost.md). Retell bills by the minute
(its voice engine, the Cartesia voice and the phone line); the model runs on our own Anthropic key
and costs about the same on any call.
"""

import sqlite3
from datetime import datetime, timedelta

from .db import iso, parse_iso
from .tenant import Tenant

# MEASURED (docs/cost.md): Retell's bill over the minutes of all 65 socket calls.
VOICE_PER_MINUTE = 0.096
# MEASURED (docs/cost.md): Claude Haiku's tokens a call, at the list price in config/models.yaml.
MODEL_PER_CALL = 0.004
# ASSUMED: the server (one small box, about $20) and two Retell numbers (about $2 each).
FIXED_MONTHLY = 24.0
# Used until the company has calls of its own (MEASURED averages of the same 65 calls; the volume
# is ASSUMED, the busy end of a 40-technician shop).
DEFAULTS = {"calls_per_month": 2000, "minutes_per_call": 2.4, "booked_share": 0.66}
WINDOW_DAYS = 30
# A call shorter than this is a hang-up or a test, and would drag the average length down.
MIN_SECONDS = 5


def recommended(conn: sqlite3.Connection, tenant: Tenant, now: datetime) -> dict:
    """The suggested inputs from the last 30 days of finished inbound calls, and what they came
    from. With no calls yet, the measured defaults."""
    since = now - timedelta(days=WINDOW_DAYS)
    rows = conn.execute(
        "SELECT c.started_at, c.ended_at, EXISTS (SELECT 1 FROM bookings b WHERE b.call_id = c.id)"
        " AS booked FROM calls c WHERE c.tenant = ? AND c.direction = 'inbound'"
        " AND c.ended_at IS NOT NULL AND c.started_at >= ?",
        (tenant.slug, iso(since)),
    ).fetchall()
    calls = []
    for row in rows:
        seconds = (parse_iso(row["ended_at"]) - parse_iso(row["started_at"])).total_seconds()
        if seconds >= MIN_SECONDS:
            calls.append((parse_iso(row["started_at"]), seconds, bool(row["booked"])))
    rates = {
        "voice_per_minute": VOICE_PER_MINUTE,
        "model_per_call": MODEL_PER_CALL,
        "fixed_monthly": FIXED_MONTHLY,
    }
    if not calls:
        return {**DEFAULTS, **rates, "calls": 0, "days": 0, "from_calls": False}
    first = min(start for start, _, _ in calls)
    days = max(1.0, min(float(WINDOW_DAYS), (now - first).total_seconds() / 86400))
    return {
        "calls_per_month": max(100, round(len(calls) / days * 30, -2)),
        "minutes_per_call": round(sum(s for _, s, _ in calls) / len(calls) / 60, 1),
        "booked_share": round(sum(1 for *_, b in calls if b) / len(calls), 2),
        **rates,
        "calls": len(calls),
        "days": round(days),
        "from_calls": True,
    }


def monthly(calls_per_month: float, minutes_per_call: float, booked_share: float,
            voice_per_minute: float, fixed_monthly: float,
            model_per_call: float = MODEL_PER_CALL) -> dict:
    """The month's cost from the inputs: the same sum the page's sliders run in the browser."""
    per_call = minutes_per_call * voice_per_minute + model_per_call
    total = calls_per_month * per_call + fixed_monthly
    booked = calls_per_month * booked_share
    return {
        "per_call": per_call,
        "total": total,
        "per_booked_job": total / booked if booked else None,
        "per_year": total * 12,
    }
