"""Capacity and technician assignment.

A slot is one technician in one arrival window on one day. Capacity for a window is the
technicians whose shift covers it (and whose skills fit the job type) minus the ones already
booked. A few technicians per window are held for urgent jobs; normal jobs cannot use them.
The database enforces the final rule: one live appointment per technician per window.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from .config import TenantConfig, Window
from .errors import ApiError


@dataclass
class Slots:
    total: int  # technicians on shift with the right skills
    booked: int
    held: int  # technicians kept back for urgent jobs
    open_ids: list[int]  # free technicians a job of the asked priority may use, best first

    @property
    def open(self) -> int:
        return len(self.open_ids)


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
    priority: str,
    zone_id: int | None = None,
    exclude_appointment: int | None = None,
) -> Slots:
    staff = _on_shift(conn, cfg, day, window, bu_id)
    busy = {
        r[0]
        for r in conn.execute(
            """SELECT technician_id FROM appointments
               WHERE window_date = ? AND window_id = ? AND status <> 'Canceled' AND id IS NOT ?""",
            (day.isoformat(), window.id, exclude_appointment),
        )
    }
    fits = [t for t in staff if set(skills) <= set(json.loads(t["skills_json"]))]
    free_fit = [t for t in fits if t["id"] not in busy]
    free_any = [t for t in staff if t["id"] not in busy]
    row = conn.execute(
        "SELECT held FROM urgent_holds WHERE window_date = ? AND window_id = ? AND business_unit_id = ?",
        (day.isoformat(), window.id, bu_id),
    ).fetchone()
    held = row["held"] if row else 0

    if priority == "Urgent":
        usable = free_fit
    else:
        usable = free_fit[: max(0, len(free_any) - held)]
    daily = {
        r[0]: r[1]
        for r in conn.execute(
            """SELECT technician_id, COUNT(*) FROM appointments
               WHERE window_date = ? AND status <> 'Canceled' GROUP BY technician_id""",
            (day.isoformat(),),
        )
    }
    usable.sort(key=lambda t: (t["home_zone_id"] != zone_id, daily.get(t["id"], 0), t["id"]))
    return Slots(
        len(fits), len([t for t in fits if t["id"] in busy]), held, [t["id"] for t in usable]
    )


def allocate(
    conn: sqlite3.Connection,
    cfg: TenantConfig,
    day: date,
    window: Window,
    bu_id: int,
    skills: list[str],
    priority: str,
    zone_id: int | None,
    technician_ids: list[int] | None = None,
    exclude_appointment: int | None = None,
) -> int:
    """Pick the technician for an appointment, or raise 409 when the slot is not free."""
    free = slots(conn, cfg, day, window, bu_id, skills, priority, zone_id, exclude_appointment)
    if technician_ids:
        for tech_id in technician_ids:
            if tech_id in free.open_ids:
                return tech_id
        raise ApiError(
            409,
            "slot_taken",
            f"Technician {technician_ids[0]} is not free in the {window.id} window on {day}.",
        )
    if not free.open_ids:
        raise ApiError(409, "no_capacity", f"No capacity left in the {window.id} window on {day}.")
    return free.open_ids[0]
