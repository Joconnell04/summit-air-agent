"""Capacity and technician assignment.

A slot is one technician in one arrival window on one day. A window is any run of the company's
30-minute blocks (tenants/<name>/config.yaml scheduling), so capacity for a window is the
technicians whose shift covers it (and whose skills fit the job type) minus the ones with a live
appointment overlapping any part of it, and minus the ones a non-job appointment (a block, a
meeting) takes out of capacity for any part of it. Allocation runs inside the write transaction,
so two bookings can't take one technician for overlapping windows.

Nothing here knows about priority, ZIPs or urgent holds. Those are the company's rules, not
ServiceTitan's, and the tool server applies them (app/capacity.py) to the technicians this answers
with.
"""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .config import TenantConfig, Window, iso
from .errors import ApiError


@dataclass
class Slots:
    staff: list[sqlite3.Row]  # technicians on shift, least booked that day first
    busy: set[int]  # technician ids with a live appointment in the window
    fit: set[int]  # technician ids whose skills fit the job type

    @property
    def total(self) -> int:
        return len(self.fit)

    @property
    def booked(self) -> int:
        return len(self.fit & self.busy)

    @property
    def free_ids(self) -> list[int]:
        """Free technicians whatever their skills, in the same order as staff."""
        return [t["id"] for t in self.staff if t["id"] not in self.busy]

    @property
    def free_fit_ids(self) -> list[int]:
        return [i for i in self.free_ids if i in self.fit]


def _on_shift(conn: sqlite3.Connection, cfg: TenantConfig, day: date, window: Window, bu_id: int):
    start, end = cfg.window_bounds(day, window)
    shift_type = "OnCall" if window.after_hours else "Normal"
    return conn.execute(
        """SELECT DISTINCT t.* FROM technicians t
           JOIN technician_shifts s ON s.technician_id = t.id
           WHERE t.active = 1 AND t.business_unit_id = ? AND s.shift_type = ?
             AND s.start <= ? AND s.end >= ?
           ORDER BY t.id""",
        (
            bu_id,
            shift_type,
            start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
    ).fetchall()


def blocked(conn: sqlite3.Connection, start, end) -> set[int]:
    """Technicians an active non-job appointment takes out of capacity for any part of start to end."""
    rows = conn.execute(
        """SELECT technician_id FROM non_job_appointments
           WHERE active = 1 AND remove_from_capacity = 1 AND start < ? AND end > ?""",
        (iso(end), iso(start)),
    ).fetchall()
    return {r["technician_id"] for r in rows}


def slots(
    conn: sqlite3.Connection,
    cfg: TenantConfig,
    day: date,
    window: Window,
    bu_id: int,
    skills: list[str],
    exclude_appointment: int | None = None,
) -> Slots:
    staff = _on_shift(conn, cfg, day, window, bu_id)
    start, end = cfg.window_bounds(day, window)
    # One statement for both, so a booking that commits meanwhile cannot make the busy list and the
    # day's load disagree (the order of technicians depends on the load).
    live = conn.execute(
        """SELECT id, technician_id, window_date, arrival_window_start, arrival_window_end
           FROM appointments
           WHERE status <> 'Canceled' AND (window_date = ? OR (arrival_window_start < ?
                 AND arrival_window_end > ?))""",
        (day.isoformat(), iso(end), iso(start)),
    ).fetchall()
    busy = {
        r["technician_id"]
        for r in live
        if r["arrival_window_start"] < iso(end)
        and r["arrival_window_end"] > iso(start)
        and r["id"] != exclude_appointment
    }
    busy |= blocked(conn, start, end)
    live = [r for r in live if r["window_date"] == day.isoformat()]
    daily = Counter(r["technician_id"] for r in live)
    staff = sorted(staff, key=lambda t: (daily.get(t["id"], 0), t["id"]))
    fit = {t["id"] for t in staff if set(skills) <= set(json.loads(t["skills_json"]))}
    return Slots(staff, busy, fit)


class Snapshot:
    """Every shift, live appointment and capacity block over a range, read once, answering `slots`
    for any block in it the same way. The capacity call asked three queries per 30-minute block
    over 16 days, about 1,200 a call: 600 ms at p50 on the live world (MEASURED from the mock's
    request log), the slowest part of find_slots."""

    def __init__(self, conn: sqlite3.Connection, start: datetime, end: datetime):
        self.shifts = conn.execute(
            """SELECT t.*, s.shift_type AS shift_type, s.start AS shift_start, s.end AS shift_end
               FROM technicians t JOIN technician_shifts s ON s.technician_id = t.id
               WHERE t.active = 1 AND s.end >= ? AND s.start <= ?
               ORDER BY t.id""",
            (iso(start), iso(end)),
        ).fetchall()
        first_day = (start - timedelta(days=1)).date().isoformat()
        last_day = (end + timedelta(days=1)).date().isoformat()
        self.live = conn.execute(
            """SELECT id, technician_id, window_date, arrival_window_start, arrival_window_end
               FROM appointments
               WHERE status <> 'Canceled' AND (window_date BETWEEN ? AND ?
                     OR (arrival_window_start < ? AND arrival_window_end > ?))""",
            (first_day, last_day, iso(end), iso(start)),
        ).fetchall()
        self.blocks = conn.execute(
            """SELECT technician_id, start, end FROM non_job_appointments
               WHERE active = 1 AND remove_from_capacity = 1 AND start < ? AND end > ?""",
            (iso(end), iso(start)),
        ).fetchall()
        # Worked out once for the range, not per block: the day's load, each technician's skills,
        # and the busy spans as plain tuples.
        self.daily: dict[str, Counter] = {}
        for r in self.live:
            self.daily.setdefault(r["window_date"], Counter())[r["technician_id"]] += 1
        self.spans = [
            (r["arrival_window_start"], r["arrival_window_end"], r["technician_id"])
            for r in self.live
        ] + [(b["start"], b["end"], b["technician_id"]) for b in self.blocks]
        self.skills = {t["id"]: set(json.loads(t["skills_json"])) for t in self.shifts}

    def slots(
        self, cfg: TenantConfig, day: date, window: Window, bu_id: int, skills: list[str]
    ) -> Slots:
        """What `slots` answers for this block, from the rows read once."""
        start, end = cfg.window_bounds(day, window)
        s, e = iso(start), iso(end)
        shift_type = "OnCall" if window.after_hours else "Normal"
        seen, staff = set(), []
        for t in self.shifts:
            if (t["business_unit_id"] == bu_id and t["shift_type"] == shift_type
                    and t["shift_start"] <= s and t["shift_end"] >= e and t["id"] not in seen):
                seen.add(t["id"])
                staff.append(t)
        busy = {tech for a, b, tech in self.spans if a < e and b > s}
        daily = self.daily.get(day.isoformat(), Counter())
        staff = sorted(staff, key=lambda t: (daily.get(t["id"], 0), t["id"]))
        need = set(skills)
        fit = {t["id"] for t in staff if need <= self.skills[t["id"]]}
        return Slots(staff, busy, fit)


def allocate(
    conn: sqlite3.Connection,
    cfg: TenantConfig,
    day: date,
    window: Window,
    bu_id: int,
    skills: list[str],
    technician_ids: list[int] | None = None,
    exclude_appointment: int | None = None,
) -> int:
    """The technician for an appointment, or 409 when the slot is not free. With technician_ids it
    takes the first of them who is free and fits; without, it picks the least booked free technician
    who fits. The pick without ids is not in the real API: there an appointment created without
    technicianIds stays unassigned until a dispatcher assigns it."""
    free = slots(conn, cfg, day, window, bu_id, skills, exclude_appointment)
    if technician_ids:
        for tech_id in technician_ids:
            if tech_id in free.free_fit_ids:
                return tech_id
        raise ApiError(
            409,
            "slot_taken",
            f"Technician {technician_ids[0]} is not free in the {window.id} window on {day}.",
        )
    if not free.free_fit_ids:
        raise ApiError(409, "no_capacity", f"No capacity left in the {window.id} window on {day}.")
    return free.free_fit_ids[0]
