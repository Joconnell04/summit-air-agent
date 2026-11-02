"""Dispatch: capacity, zones, technician shifts."""

import sqlite3
from datetime import datetime, timedelta
from typing import Literal

from fastapi import Depends
from pydantic import AwareDatetime, BaseModel

from .. import capacity
from ..config import TenantConfig, iso
from ..db import page_of
from ..deps import get_cfg, get_conn, get_now, paging
from ..errors import ApiError
from . import tenant_router

router = tenant_router("dispatch")

MAX_RANGE_DAYS = 31


class CapacityIn(BaseModel):
    startsOnOrAfter: AwareDatetime
    endsOnOrBefore: AwareDatetime
    businessUnitIds: list[int] = []
    jobTypeId: int | None = None
    skillBasedAvailability: bool = True
    priority: Literal["Low", "Normal", "High", "Urgent"] = "Normal"  # ASSUMED request field
    zip: str | None = None  # ASSUMED: sort technicians from that ZIP's zone first


@router.post("/capacity")
def get_capacity(
    body: CapacityIn,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now: datetime = Depends(get_now),
):
    """Open technician-windows by day and business unit. The response shape is ASSUMED: it follows
    the earlier practice agent's adapter, not a verified ServiceTitan response."""
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
    skills = job_type["skills"] if job_type and body.skillBasedAvailability else []
    zone = cfg.zone_by_zip.get(body.zip) if body.zip else None

    first_day = body.startsOnOrAfter.astimezone(cfg.tz).date()
    last_day = body.endsOnOrBefore.astimezone(cfg.tz).date()
    availabilities = []
    day = first_day
    while day <= last_day:
        for unit_id in unit_ids:
            timeslots = []
            for window in cfg.windows:
                start, end = cfg.window_bounds(day, window)
                if not (body.startsOnOrAfter <= start < body.endsOnOrBefore):
                    continue
                if (end <= now) if window.after_hours else (start < now):
                    continue
                if not cfg.window_offered(day, window):
                    continue
                open_to_all = not (window.after_hours and cfg.urgent_only_after_hours)
                usable = body.priority == "Urgent" or open_to_all
                s = capacity.slots(
                    conn,
                    cfg,
                    day,
                    window,
                    unit_id,
                    skills,
                    body.priority,
                    zone["id"] if zone else None,
                )
                open_ids = s.open_ids if usable else []
                timeslots.append(
                    {
                        "windowId": window.id,
                        "start": iso(start),
                        "end": iso(end),
                        "afterHours": window.after_hours,
                        "totalAvailability": s.total,
                        "bookedCount": s.booked,
                        "heldForUrgent": s.held,
                        "openAvailability": len(open_ids),
                        "isAvailable": bool(open_ids),
                        "technicianIds": open_ids,
                    }
                )
            if timeslots:
                availabilities.append(
                    {"date": day.isoformat(), "businessUnitId": unit_id, "timeslots": timeslots}
                )
        day += timedelta(days=1)
    return {"availabilities": availabilities}


@router.get("/zones")
def list_zones(cfg: TenantConfig = Depends(get_cfg)):
    data = [
        {"id": z["id"], "name": z["name"], "county": z["county"], "zips": z["zips"], "active": True}
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
    shiftType: Literal["Normal", "OnCall"] | None = None,
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
