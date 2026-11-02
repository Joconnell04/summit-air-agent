"""Helpers for building a fake company: used by tenant seed scripts and by tests.

These write rows directly, so they can create history (past jobs, expired memberships) that the
API rules would refuse.
"""
import json
import sqlite3
from datetime import date, datetime, time, timedelta

from . import capacity
from .config import TenantConfig, iso
from .util import phone_key


def local_dt(cfg: TenantConfig, day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=cfg.tz)


def add_technician(
    conn: sqlite3.Connection, tech_id: int, name: str, business_unit_id: int, home_zone_id: int,
    skills: list[str], phone: str = "",
) -> None:
    email = name.lower().replace(" ", ".") + "@summit-air.example"
    conn.execute(
        """INSERT INTO technicians (id, name, business_unit_id, phone, email, home_zone_id, skills_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (tech_id, name, business_unit_id, phone, email, home_zone_id, json.dumps(skills)),
    )


def add_shift(
    conn: sqlite3.Connection, cfg: TenantConfig, tech_id: int, day: date, start: str, end: str,
    shift_type: str = "Normal", ends_next_day: bool = False, note: str = "",
) -> None:
    end_day = day + timedelta(days=1) if ends_next_day else day
    conn.execute(
        "INSERT INTO technician_shifts (technician_id, shift_type, start, end, note) VALUES (?, ?, ?, ?, ?)",
        (tech_id, shift_type, iso(local_dt(cfg, day, start)), iso(local_dt(cfg, end_day, end)), note),
    )


def add_holds(conn: sqlite3.Connection, cfg: TenantConfig, days: list[date]) -> None:
    """Write the configured urgent hold for each day, window and business unit."""
    for day in days:
        for window in cfg.windows:
            for unit in cfg.business_units:
                held = cfg.hold_for(day, window.id, unit["id"])
                if held:
                    conn.execute(
                        "INSERT OR REPLACE INTO urgent_holds VALUES (?, ?, ?, ?)",
                        (day.isoformat(), window.id, unit["id"], held),
                    )


def add_job(
    conn: sqlite3.Connection, cfg: TenantConfig, customer_id: int, location_id: int, job_type_id: int,
    day: date, window_id: str, status: str = "Scheduled", priority: str | None = None,
    tech_id: int | None = None, summary: str = "", created: datetime | None = None,
) -> tuple[int, int]:
    """Insert a job with one appointment. Without tech_id, takes any free technician who fits,
    ignoring urgent holds. Returns (job_id, appointment_id)."""
    job_type = cfg.job_type(job_type_id)
    unit_id = job_type["businessUnitIds"][0]
    window = cfg.window(window_id)
    zone_id = conn.execute("SELECT zone_id FROM locations WHERE id = ?", (location_id,)).fetchone()[0]
    if tech_id is None:
        tech_id = capacity.allocate(conn, cfg, day, window, unit_id, job_type["skills"], "Urgent", zone_id)
    start, end = cfg.window_bounds(day, window)
    stamp = iso(created or start - timedelta(days=3))
    completed = iso(end) if status == "Done" else None
    job_id = conn.execute(
        """INSERT INTO jobs (job_number, customer_id, location_id, business_unit_id, job_type_id, priority,
                             summary, status, completed_on, created_on, modified_on)
           VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (customer_id, location_id, unit_id, job_type_id, priority or job_type["priority"], summary, status,
         completed, stamp, stamp),
    ).lastrowid
    conn.execute("UPDATE jobs SET job_number = ? WHERE id = ?", (str(job_id), job_id))
    appt_id = conn.execute(
        """INSERT INTO appointments (job_id, appointment_number, technician_id, business_unit_id, window_date,
               window_id, start, end, arrival_window_start, arrival_window_end, status, created_on, modified_on)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, f"{job_id}-1", tech_id, unit_id, day.isoformat(), window_id, iso(start), iso(end),
         iso(start), iso(end), status, stamp, stamp),
    ).lastrowid
    conn.execute(
        "INSERT INTO job_history (job_id, date, event_type, text) VALUES (?, ?, 'JobCreated', ?)",
        (job_id, stamp, f"Job created with priority {priority or job_type['priority']}."),
    )
    if status == "Done":
        conn.execute(
            "INSERT INTO job_history (job_id, date, event_type, text) VALUES (?, ?, 'JobCompleted', 'Work completed.')",
            (job_id, completed),
        )
    return job_id, appt_id


def add_membership(
    conn: sqlite3.Connection, cfg: TenantConfig, customer_id: int, location_id: int, type_id: int, status: str,
    start: date, end: date, today: date,
) -> int:
    """Insert a membership and its included service visits. Visits before today are Completed,
    later ones NotAttempted (due)."""
    stamp = iso(local_dt(cfg, start, "09:00"))
    member_id = conn.execute(
        """INSERT INTO memberships (customer_id, location_id, membership_type_id, status, from_date, to_date,
                                    created_on, modified_on)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (customer_id, location_id, type_id, status, start.isoformat(), end.isoformat(), stamp, stamp),
    ).lastrowid
    if status != "Canceled":
        visits = next(t for t in cfg.membership_types if t["id"] == type_id)["perks"]["visitsPerYear"]
        for n in range(visits):
            due = start + timedelta(days=(365 // (visits + 1)) * (n + 1))
            name = "Heating system tune-up" if due.month in (8, 9, 10, 11, 12, 1, 2) else "Cooling system tune-up"
            conn.execute(
                """INSERT INTO recurring_service_events (membership_id, location_id, name, date, status)
                   VALUES (?, ?, ?, ?, ?)""",
                (member_id, location_id, name, due.isoformat(), "Completed" if due < today else "NotAttempted"),
            )
    return member_id


def add_equipment(
    conn: sqlite3.Connection, customer_id: int, location_id: int, system_type: str, manufacturer: str,
    model: str, installed_on: date,
) -> None:
    label = system_type.replace("_", " ").replace("ac", "AC")
    conn.execute(
        """INSERT INTO installed_equipment (location_id, customer_id, name, system_type, manufacturer, model,
                                            installed_on)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (location_id, customer_id, f"{manufacturer} {label}", system_type, manufacturer, model,
         installed_on.isoformat()),
    )


def add_call(
    conn: sqlite3.Connection, when: datetime, from_number: str, customer_id: int | None = None,
    job_id: int | None = None, call_type: str = "Unbooked", reason: str = "", duration: int = 180,
    to_number: str = "(718) 555-0100",
) -> None:
    conn.execute(
        """INSERT INTO calls (direction, from_number, from_key, to_number, received_on, duration, call_type,
                              customer_id, job_id, reason)
           VALUES ('Inbound', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (from_number, phone_key(from_number), to_number, iso(when), duration, call_type, customer_id,
         job_id, reason),
    )
