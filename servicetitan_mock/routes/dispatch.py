"""Dispatch: capacity, appointment assignments, arrival windows, zones, technician shifts."""

import sqlite3
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import Depends, Header, Request
from pydantic import AwareDatetime, BaseModel, Field

from .. import capacity
from ..config import TenantConfig, iso
from ..db import page_of, write
from ..deps import get_cfg, get_conn, get_now, paging
from ..errors import ApiError
from . import tenant_router
from .jpm import shape_appointment

router = tenant_router("dispatch")

MAX_RANGE_DAYS = 31


class CapacityIn(BaseModel):
    """The real request: no priority and no ZIP, and skillBasedAvailability is required."""

    startsOnOrAfter: AwareDatetime
    endsOnOrBefore: AwareDatetime
    businessUnitIds: list[int] | None = None
    jobTypeId: int | None = None
    skillBasedAvailability: bool


def _hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 2)


@router.post("/capacity")
def get_capacity(
    body: CapacityIn,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now: datetime = Depends(get_now),
):
    """Availability by time frame: one frame per arrival window and business unit, with the named
    technicians and whether each is free. The unit of totalAvailability and openAvailability is
    hours in ServiceTitan's current spec; here it is technicians times the frame's hours (ASSUMED).
    Callers should read the technicians, not the numbers."""
    if body.endsOnOrBefore <= body.startsOnOrAfter:
        raise ApiError(400, "validation_error", "endsOnOrBefore must be after startsOnOrAfter.")
    if body.endsOnOrBefore - body.startsOnOrAfter > timedelta(days=MAX_RANGE_DAYS):
        raise ApiError(400, "validation_error", f"Ask for at most {MAX_RANGE_DAYS} days at a time.")

    job_type = None
    if body.jobTypeId is not None:
        job_type = cfg.job_type(body.jobTypeId)
        if not job_type or not job_type["active"]:
            raise ApiError(422, "unknown_reference", f"Job type {body.jobTypeId} is not available.")
    unit_ids = body.businessUnitIds or [b["id"] for b in cfg.business_units if b["active"]]
    if job_type:
        unit_ids = [u for u in unit_ids if u in job_type["businessUnitIds"]]
    skill_based = body.skillBasedAvailability and job_type is not None
    skills = job_type["skills"] if skill_based else []

    # A window that starts the evening before the range can still end inside it.
    first_day = body.startsOnOrAfter.astimezone(cfg.tz).date() - timedelta(days=1)
    last_day = body.endsOnOrBefore.astimezone(cfg.tz).date()
    frames = []
    day = first_day
    while day <= last_day:
        for window in cfg.windows:
            start, end = cfg.window_bounds(day, window)
            if not (body.startsOnOrAfter <= start and end <= body.endsOnOrBefore):
                continue
            if not cfg.window_offered(day, window):
                continue
            hours = _hours(end - start)
            for unit_id in unit_ids:
                s = capacity.slots(conn, cfg, day, window, unit_id, skills)
                free = s.free_fit_ids if skill_based else s.free_ids
                frames.append(
                    {
                        "start": start.astimezone(cfg.tz).isoformat(),
                        "end": end.astimezone(cfg.tz).isoformat(),
                        "startUtc": iso(start),
                        "endUtc": iso(end),
                        "businessUnitIds": [unit_id],
                        "totalAvailability": hours * (s.total if skill_based else len(s.staff)),
                        "openAvailability": hours * len(free),
                        "technicians": [
                            {
                                "id": t["id"],
                                "name": t["name"],
                                "status": "Unavailable" if t["id"] in s.busy else "Available",
                                "hasRequiredSkills": (t["id"] in s.fit) if skill_based else None,
                            }
                            for t in s.staff
                        ],
                        "isAvailable": bool(free),
                        "isExceedingIdealBookingPercentage": False,
                    }
                )
        day += timedelta(days=1)
    frames.sort(key=lambda f: (f["startUtc"], f["businessUnitIds"]))
    return {"timeStamp": iso(now), "availabilities": frames}


# ---- who is on which appointment ---------------------------------------------------------------


def shape_assignment(r: sqlite3.Row) -> dict:
    status = r["status"]
    return {
        "id": r["id"],
        "technicianId": r["technician_id"],
        "technicianName": r["technician_name"],
        "assignedById": 4001,
        "assignedOn": r["created_on"],
        "status": "Scheduled" if status in ("Hold", "Canceled") else status,
        "isPaused": False,
        "jobId": r["job_id"],
        "appointmentId": r["id"],
        "createdOn": r["created_on"],
        "modifiedOn": r["modified_on"],
        "active": status != "Canceled",
    }


@router.get("/appointment-assignments")
def list_assignments(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    jobId: int | None = None,
    appointmentIds: str | None = None,
    technicianIds: str | None = None,
    active: str | None = None,
):
    """The technician on each appointment. The mock keeps one technician per appointment, so an
    assignment shares the appointment's id."""
    where, params = [], []
    if jobId is not None:
        where.append("a.job_id = ?")
        params.append(jobId)
    for column, value in (("a.id", appointmentIds), ("a.technician_id", technicianIds)):
        if value:
            ids = [int(v) for v in value.split(",") if v.strip().isdigit()]
            where.append(f"{column} IN ({','.join('?' * len(ids))})")
            params += ids
    if active == "True":
        where.append("a.status <> 'Canceled'")
    elif active == "False":
        where.append("a.status = 'Canceled'")
    sql = (
        "SELECT a.*, t.name AS technician_name FROM appointments a "
        "JOIN technicians t ON t.id = a.technician_id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY a.id"
    )
    return page_of(conn, sql, params, shape_assignment, *pg)


class AssignIn(BaseModel):
    jobAppointmentId: int
    technicianIds: list[int] = Field(min_length=1)


@router.post("/appointment-assignments/assign-technicians")
def assign_technicians(
    body: AssignIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now: datetime = Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    """Put a technician on an appointment. 409 when none of the technicians is free in its window.
    The mock holds one technician per appointment, so the first free one replaces the old one."""

    def run(c: sqlite3.Connection):
        appt = c.execute(
            "SELECT * FROM appointments WHERE id = ?", (body.jobAppointmentId,)
        ).fetchone()
        if not appt or appt["status"] == "Canceled":
            raise ApiError(404, "not_found", f"Appointment {body.jobAppointmentId} does not exist.")
        job = c.execute("SELECT * FROM jobs WHERE id = ?", (appt["job_id"],)).fetchone()
        tech_id = capacity.allocate(
            c,
            cfg,
            date.fromisoformat(appt["window_date"]),
            cfg.window(appt["window_id"]),
            appt["business_unit_id"],
            cfg.job_type(job["job_type_id"])["skills"],
            body.technicianIds,
            exclude_appointment=appt["id"],
        )
        c.execute(
            "UPDATE appointments SET technician_id = ?, modified_on = ? WHERE id = ?",
            (tech_id, iso(now), appt["id"]),
        )
        return 200, shape_appointment(
            c.execute("SELECT * FROM appointments WHERE id = ?", (appt["id"],)).fetchone()
        )

    return write(
        conn,
        run,
        idem_key=idempotency_key,
        route=f"{request.method} {request.url.path}",
        body=body.model_dump(),
    )


# ---- reference data ----------------------------------------------------------------------------


def _duration(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


@router.get("/arrival-windows")
def list_arrival_windows(cfg: TenantConfig = Depends(get_cfg)):
    """The configured windows as ServiceTitan lists them: a start time of day and a duration."""
    data = []
    for n, window in enumerate(cfg.windows, 1):
        start, end = cfg.window_bounds(date(2026, 1, 5), window)
        local_start = start.astimezone(cfg.tz)
        data.append(
            {
                "id": n,
                "start": _duration(timedelta(hours=local_start.hour, minutes=local_start.minute)),
                "duration": _duration(end - start),
                "businessUnitIds": [b["id"] for b in cfg.business_units if b["active"]],
                "active": True,
            }
        )
    return {
        "page": 1,
        "pageSize": len(data),
        "hasMore": False,
        "totalCount": len(data),
        "data": data,
    }


@router.get("/zones")
def list_zones(cfg: TenantConfig = Depends(get_cfg)):
    data = [
        {"id": z["id"], "name": z["name"], "zips": z["zips"], "cities": [], "active": True}
        for z in cfg.zones
    ]
    return {
        "page": 1,
        "pageSize": len(data),
        "hasMore": False,
        "totalCount": len(data),
        "data": data,
    }


def shape_shift(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "technicianId": r["technician_id"],
        "shiftType": r["shift_type"],
        "title": r["note"] or r["shift_type"],
        "start": r["start"],
        "end": r["end"],
        "note": r["note"],
        "active": True,
    }


@router.get("/technician-shifts")
def list_shifts(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    technicianId: int | None = None,
    shiftType: Literal["Normal", "OnCall", "TimeOff"] | None = None,
    startsOnOrAfter: AwareDatetime | None = None,
    endsOnOrBefore: AwareDatetime | None = None,
):
    """shiftType OnCall gives the weekly on-call technician's nights."""
    where, params = [], []
    if technicianId is not None:
        where.append("technician_id = ?")
        params.append(technicianId)
    if shiftType:
        where.append("shift_type = ?")
        params.append(shiftType)
    if startsOnOrAfter:
        where.append("start >= ?")
        params.append(iso(startsOnOrAfter))
    if endsOnOrBefore:
        where.append("end <= ?")
        params.append(iso(endsOnOrBefore))
    sql = (
        "SELECT * FROM technician_shifts"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY start, id"
    )
    return page_of(conn, sql, params, shape_shift, *pg)
