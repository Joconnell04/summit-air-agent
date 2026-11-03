"""Capacity and technician assignment.

A slot is one technician in one arrival window on one day. Capacity for a window is the
technicians whose shift covers it (and whose skills fit the job type) minus the ones already
booked. The database enforces the final rule: one live appointment per technician per window.

Nothing here knows about priority, ZIPs or urgent holds. Those are the company's rules, not
ServiceTitan's, and the tool server applies them (app/capacity.py) to the technicians this answers
with.
"""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date

from .config import TenantConfig, Window
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
    # One statement for both, so a booking that commits meanwhile cannot make the busy list and the
    # day's load disagree (the order of technicians depends on the load).
    live = conn.execute(
        """SELECT id, technician_id, window_id FROM appointments
           WHERE window_date = ? AND status <> 'Canceled'""",
        (day.isoformat(),),
    ).fetchall()
    busy = {
        r["technician_id"]
        for r in live
        if r["window_id"] == window.id and r["id"] != exclude_appointment
    }
    daily = Counter(r["technician_id"] for r in live)
    staff = sorted(staff, key=lambda t: (daily.get(t["id"], 0), t["id"]))
    fit = {t["id"] for t in staff if set(skills) <= set(json.loads(t["skills_json"]))}
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
