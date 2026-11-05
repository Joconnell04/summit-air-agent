"""Opens the live demo's schedule (issue #149, DECISIONS.md 095): most windows free, weekends
staffed, and shifts for the next two weeks. Run on the server against the live mock ServiceTitan,
between calls, once at deploy and again after a reseed:

    uv run python -m scripts.open_live_schedule --db data/servicetitan_mock.db

The eval world is never touched: each eval run seeds its own copy from tenants/<tenant>/seed.py,
which this script doesn't change, so every appointment the scenarios need stays as it is. In the
live world, the named customers' visits stay too (Maria Delgado's tune-up and the rest); only the
seed's filler bookings ("Booked by phone.", "Replacement estimate.") are cancelled, all but one in
`--keep-every`, so a few remain for Jackson's own runs. Running it twice changes nothing more.
"""

import argparse
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from servicetitan_mock import worldkit as wk
from servicetitan_mock.config import DEFAULT_TENANT_DIR, TenantConfig, iso

# The seed's filler bookings, by the summary it gives them (tenants/summit-air/seed.py).
FILLER_SUMMARIES = ("Booked by phone.", "Replacement estimate.")
SERVICE_UNIT, INSTALL_UNIT = 11, 12
CANCEL_REASON = 401  # "Customer cancelled" (config.yaml cancel_reasons)
WEEKEND_HOURS = ("08:00", "20:00")


def day_bounds(cfg: TenantConfig, day: date) -> tuple[str, str]:
    return iso(wk.local_dt(cfg, day, "00:00")), iso(wk.local_dt(cfg, day + timedelta(days=1), "00:00"))


def has_shift(conn: sqlite3.Connection, cfg: TenantConfig, tech_id: int, day: date, kind: str) -> bool:
    start, end = day_bounds(cfg, day)
    return bool(
        conn.execute(
            "SELECT 1 FROM technician_shifts WHERE technician_id = ? AND shift_type = ? "
            "AND start >= ? AND start < ?",
            (tech_id, kind, start, end),
        ).fetchone()
    )


def technicians(conn: sqlite3.Connection, unit: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, skills_json FROM technicians WHERE business_unit_id = ? AND active = 1 "
        "ORDER BY id",
        (unit,),
    ).fetchall()


def weekday_hours(i: int) -> tuple[str, str]:
    """The seed's staggered weekday shifts (tenants/summit-air/seed.py, shifts)."""
    group = i % 10
    return ("08:00", "16:00") if group < 4 else ("12:00", "20:00") if group < 7 else ("08:00", "20:00")


def add_shifts(conn: sqlite3.Connection, cfg: TenantConfig, first: date, days: int) -> int:
    """Shifts for every technician with none that day: weekdays as the seed staffs them, and
    weekends with two in three service technicians on all day. A technician who already has a
    shift that day keeps it as it is. Returns the number added."""
    service, advisors = technicians(conn, SERVICE_UNIT), technicians(conn, INSTALL_UNIT)
    all_skilled = [
        t["id"] for t in service if {"heating", "cooling"} <= set(json.loads(t["skills_json"]))
    ]
    added = 0
    for offset in range(days):
        day = first + timedelta(days=offset)
        weekend = day.weekday() >= 5
        for i, tech in enumerate(service):
            if weekend and i % 3 == 2:
                continue
            if has_shift(conn, cfg, tech["id"], day, "Normal"):
                continue
            start, end = WEEKEND_HOURS if weekend else weekday_hours(i)
            wk.add_shift(conn, cfg, tech["id"], day, start, end)
            added += 1
        if not weekend:
            for tech in advisors:
                if not has_shift(conn, cfg, tech["id"], day, "Normal"):
                    wk.add_shift(conn, cfg, tech["id"], day, "08:00", "16:00")
                    added += 1
        # One on-call technician a night, rotating weekly like the seed.
        if all_skilled and not any(has_shift(conn, cfg, t, day, "OnCall") for t in all_skilled):
            week = (day - timedelta(days=day.weekday())).toordinal() // 7
            wk.add_shift(
                conn, cfg, all_skilled[week % len(all_skilled)], day, "20:00", "08:00", "OnCall",
                ends_next_day=True, note="Weekly on-call",
            )
            added += 1
    return added


def cancel_fillers(conn: sqlite3.Connection, first: date, keep_every: int) -> int:
    """Cancels the seed's filler visits from `first` on, keeping those whose job id is a multiple
    of `keep_every`, so the choice is the same on every run. Returns the number cancelled."""
    marks = ",".join("?" * len(FILLER_SUMMARIES))
    rows = conn.execute(
        f"SELECT j.id FROM jobs j JOIN appointments a ON a.job_id = j.id "
        f"WHERE j.summary IN ({marks}) AND j.status = 'Scheduled' AND a.status = 'Scheduled' "
        # Untouched since the seed: a visit a live caller moved or changed is left alone (PR #155
        # review).
        f"AND j.modified_on = j.created_on AND a.modified_on = a.created_on "
        f"AND a.window_date >= ?",
        (*FILLER_SUMMARIES, first.isoformat()),
    ).fetchall()
    stamp = iso(datetime.now(UTC))
    cancelled = 0
    for row in rows:
        if row["id"] % keep_every == 0:
            continue
        conn.execute(
            "UPDATE appointments SET status = 'Canceled', modified_on = ? WHERE job_id = ?",
            (stamp, row["id"]),
        )
        conn.execute(
            "UPDATE jobs SET status = 'Canceled', cancel_reason_id = ?, modified_on = ? "
            "WHERE id = ?",
            (CANCEL_REASON, stamp, row["id"]),
        )
        cancelled += 1
    return cancelled


def open_schedule(
    db: Path, tenant_dir: Path, today: date | None = None, days: int = 14, keep_every: int = 6
) -> dict:
    cfg = TenantConfig.load(tenant_dir)
    first = today or datetime.now(cfg.tz).date()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            shifts = add_shifts(conn, cfg, first, days)
            cancelled = cancel_fillers(conn, first, keep_every)
    finally:
        conn.close()
    return {"shifts_added": shifts, "fillers_cancelled": cancelled}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="data/servicetitan_mock.db")
    parser.add_argument("--tenant", default=DEFAULT_TENANT_DIR)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--keep-every", type=int, default=6)
    args = parser.parse_args()
    print(json.dumps(open_schedule(Path(args.db), Path(args.tenant), None, args.days, args.keep_every)))


if __name__ == "__main__":
    main()
