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

# The appointment status that goes with a job status, for seeding history.
APPOINTMENT_OF_JOB = {"InProgress": "Working", "Completed": "Done"}


def local_dt(cfg: TenantConfig, day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=cfg.tz)


def add_technician(
    conn: sqlite3.Connection,
    cfg: TenantConfig,
    tech_id: int,
    name: str,
    business_unit_id: int,
    home_zone_id: int,
    skills: list[str],
    phone: str = "",
) -> None:
    email = name.lower().replace(" ", ".") + "@" + cfg.company["email_domain"]
    conn.execute(
        """INSERT INTO technicians (id, name, business_unit_id, phone, email, home_zone_id, skills_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (tech_id, name, business_unit_id, phone, email, home_zone_id, json.dumps(skills)),
    )


def add_shift(
    conn: sqlite3.Connection,
    cfg: TenantConfig,
    tech_id: int,
    day: date,
    start: str,
    end: str,
    shift_type: str = "Normal",
    ends_next_day: bool = False,
    note: str = "",
) -> None:
    end_day = day + timedelta(days=1) if ends_next_day else day
    conn.execute(
        "INSERT INTO technician_shifts (technician_id, shift_type, start, end, note) VALUES (?, ?, ?, ?, ?)",
        (
            tech_id,
            shift_type,
            iso(local_dt(cfg, day, start)),
            iso(local_dt(cfg, end_day, end)),
            note,
        ),
    )


def add_job(
    conn: sqlite3.Connection,
    cfg: TenantConfig,
    customer_id: int,
    location_id: int,
    job_type_id: int,
    day: date,
    window_id: str,
    status: str = "Scheduled",
    priority: str | None = None,
    tech_id: int | None = None,
    summary: str = "",
    created: datetime | None = None,
) -> tuple[int, int]:
    """Insert a job with one appointment; status is the job's (Scheduled, Dispatched, InProgress,
    Completed...) and the appointment gets the matching one. Without tech_id, takes any free
    technician who fits. Returns (job_id, appointment_id)."""
    job_type = cfg.job_type(job_type_id)
    unit_id = job_type["businessUnitIds"][0]
    window = cfg.window(window_id)
    if tech_id is None:
        tech_id = capacity.allocate(conn, cfg, day, window, unit_id, job_type["skills"])
    start, end = cfg.window_bounds(day, window)
    stamp = iso(created or start - timedelta(days=3))
    completed = iso(end) if status == "Completed" else None
    appointment_status = APPOINTMENT_OF_JOB.get(status, status)
    job_id = conn.execute(
        """INSERT INTO jobs (job_number, customer_id, location_id, business_unit_id, job_type_id, priority,
                             summary, status, completed_on, created_on, modified_on)
           VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            customer_id,
            location_id,
            unit_id,
            job_type_id,
            priority or job_type["priority"],
            summary,
            status,
            completed,
            stamp,
            stamp,
        ),
    ).lastrowid
    conn.execute("UPDATE jobs SET job_number = ? WHERE id = ?", (str(job_id), job_id))
    appt_id = conn.execute(
        """INSERT INTO appointments (job_id, appointment_number, technician_id, business_unit_id, window_date,
               window_id, start, end, arrival_window_start, arrival_window_end, status, created_on, modified_on)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job_id,
            f"{job_id}-1",
            tech_id,
            unit_id,
            day.isoformat(),
            window_id,
            iso(start),
            iso(end),
            iso(start),
            iso(end),
            appointment_status,
            stamp,
            stamp,
        ),
    ).lastrowid
    conn.execute(
        "INSERT INTO job_history (job_id, date, event_type, text) VALUES (?, ?, 'JobCreated', ?)",
        (job_id, stamp, f"Job created with priority {priority or job_type['priority']}."),
    )
    if status == "Completed":
        conn.execute(
            "INSERT INTO job_history (job_id, date, event_type, text) VALUES (?, ?, 'JobCompleted', 'Work completed.')",
            (job_id, completed),
        )
    return job_id, appt_id


def add_membership(
    conn: sqlite3.Connection,
    cfg: TenantConfig,
    customer_id: int,
    location_id: int,
    type_id: int,
    status: str,
    start: date,
    end: date,
    today: date,
) -> int:
    """Insert a membership and its included service visits. Visits before today are Completed,
    later ones NotAttempted (due)."""
    stamp = iso(local_dt(cfg, start, "09:00"))
    member_id = conn.execute(
        """INSERT INTO memberships (customer_id, location_id, membership_type_id, status, from_date, to_date,
                                    created_on, modified_on)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            customer_id,
            location_id,
            type_id,
            status,
            start.isoformat(),
            end.isoformat(),
            stamp,
            stamp,
        ),
    ).lastrowid
    if status != "Canceled":
        membership_type = next(t for t in cfg.membership_types if t["id"] == type_id)
        visits = membership_type["perks"]["visitsPerYear"]
        for n in range(visits):
            due = start + timedelta(days=(365 // (visits + 1)) * (n + 1))
            name = cfg.visit_name(membership_type, due)
            conn.execute(
                """INSERT INTO recurring_service_events (membership_id, location_id, name, date, status)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    member_id,
                    location_id,
                    name,
                    due.isoformat(),
                    "Won" if due < today else "NotAttempted",
                ),
            )
    return member_id


def add_equipment(
    conn: sqlite3.Connection,
    cfg: TenantConfig,
    customer_id: int,
    location_id: int,
    system_type: str,
    manufacturer: str,
    model: str,
    installed_on: date,
) -> None:
    label = cfg.equipment_label(system_type)
    conn.execute(
        """INSERT INTO installed_equipment (location_id, customer_id, name, system_type, manufacturer, model,
                                            installed_on)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            location_id,
            customer_id,
            f"{manufacturer} {label}",
            system_type,
            manufacturer,
            model,
            installed_on.isoformat(),
        ),
    )


def add_call(
    conn: sqlite3.Connection,
    cfg: TenantConfig,
    when: datetime,
    from_number: str,
    customer_id: int | None = None,
    job_id: int | None = None,
    call_type: str | None = "Unbooked",
    reason: str = "",
    duration: int = 180,
    to_number: str | None = None,
    direction: str = "Inbound",
    sid: str | None = None,
    tags: tuple[str, ...] = (),
) -> int:
    """Insert a call as ServiceTitan's phone system would log it. An inbound call without to_number
    came in on the company's main line. For an outbound call from_number is the company line and
    to_number the customer's number; ServiceTitan leaves its call type empty. The reason is a name
    from the tenant's call reasons; tags are names from its tag types."""
    reason_id = next((r["id"] for r in cfg.call_reasons if r["name"] == reason), None)
    if reason and reason_id is None:
        raise ValueError(f"no call reason called {reason!r} in the tenant config")
    tag_ids = []
    for name in tags:
        tag_id = next((t["id"] for t in cfg.tag_types if t["name"] == name), None)
        if tag_id is None:
            raise ValueError(f"no tag type called {name!r} in the tenant config")
        tag_ids.append(tag_id)
    to_number = to_number or cfg.company["phone"]
    call_id = conn.execute(
        """INSERT INTO calls (sid, direction, from_number, from_key, to_number, to_key, received_on,
                              duration, call_type, customer_id, job_id, reason_id, modified_on)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            sid,
            direction,
            from_number,
            phone_key(from_number),
            to_number,
            phone_key(to_number),
            iso(when),
            duration,
            None if direction == "Outbound" else call_type,
            customer_id,
            job_id,
            reason_id,
            iso(when),
        ),
    ).lastrowid
    for tag_id in tag_ids:
        conn.execute(
            "INSERT INTO call_tags (call_id, tag_type_id) VALUES (?, ?)", (call_id, tag_id)
        )
    return call_id
