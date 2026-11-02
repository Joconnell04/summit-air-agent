"""Jobs and appointments (JPM): create, cancel, reschedule, history."""
import sqlite3
from datetime import date, datetime
from typing import Literal

from fastapi import Depends, Header, Request
from pydantic import AwareDatetime, BaseModel, Field

from .. import capacity, rules
from ..config import TenantConfig, Window, iso
from ..db import page_of, write
from ..deps import get_cfg, get_conn, get_now, paging
from ..errors import ApiError
from . import tenant_router

router = tenant_router("jpm")

Priority = Literal["Low", "Normal", "High", "Urgent"]
OPEN_STATUSES = ("Scheduled", "Dispatched", "Working")


class AppointmentIn(BaseModel):
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    arrivalWindowStart: AwareDatetime
    arrivalWindowEnd: AwareDatetime
    technicianIds: list[int] = []


class JobIn(BaseModel):
    customerId: int
    locationId: int
    businessUnitId: int
    jobTypeId: int
    priority: Priority | None = None
    campaignId: int | None = None
    summary: str = ""
    appointments: list[AppointmentIn] = Field(min_length=1)


class CancelIn(BaseModel):
    reasonId: int | None = None
    memo: str = ""


class RescheduleIn(BaseModel):
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    arrivalWindowStart: AwareDatetime
    arrivalWindowEnd: AwareDatetime
    technicianIds: list[int] = []


def _route(request: Request) -> str:
    return f"{request.method} {request.url.path}"


def _history(conn: sqlite3.Connection, job_id: int, now: datetime, event: str, text: str) -> None:
    conn.execute(
        "INSERT INTO job_history (job_id, date, event_type, text) VALUES (?, ?, ?, ?)",
        (job_id, iso(now), event, text),
    )


# ---- shapes ------------------------------------------------------------------------------------


def shape_job(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    appts = conn.execute(
        "SELECT id FROM appointments WHERE job_id = ? ORDER BY id", (r["id"],)
    ).fetchall()
    return {
        "id": r["id"], "jobNumber": r["job_number"], "customerId": r["customer_id"],
        "locationId": r["location_id"], "jobStatus": r["status"], "completedOn": r["completed_on"],
        "businessUnitId": r["business_unit_id"], "jobTypeId": r["job_type_id"], "priority": r["priority"],
        "campaignId": r["campaign_id"], "summary": r["summary"],
        "firstAppointmentId": appts[0]["id"] if appts else None,
        "lastAppointmentId": appts[-1]["id"] if appts else None, "appointmentCount": len(appts),
        "createdOn": r["created_on"], "modifiedOn": r["modified_on"],
    }


def shape_appointment(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "jobId": r["job_id"], "appointmentNumber": r["appointment_number"],
        "start": r["start"], "end": r["end"],
        "arrivalWindowStart": r["arrival_window_start"], "arrivalWindowEnd": r["arrival_window_end"],
        "status": r["status"], "technicianIds": [r["technician_id"]],
        "createdOn": r["created_on"], "modifiedOn": r["modified_on"],
    }


def _one(conn: sqlite3.Connection, table: str, row_id: int, what: str) -> sqlite3.Row:
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if not row:
        raise ApiError(404, "not_found", f"{what} {row_id} does not exist.")
    return row


# ---- reference data ----------------------------------------------------------------------------


@router.get("/job-types")
def list_job_types(cfg: TenantConfig = Depends(get_cfg)):
    data = [{k: v for k, v in j.items()} for j in cfg.job_types]
    return {"page": 1, "pageSize": len(data), "hasMore": False, "totalCount": len(data), "data": data}


@router.get("/job-types/{job_type_id}")
def get_job_type(job_type_id: int, cfg: TenantConfig = Depends(get_cfg)):
    found = cfg.job_type(job_type_id)
    if not found:
        raise ApiError(404, "not_found", f"Job type {job_type_id} does not exist.")
    return found


@router.get("/job-cancel-reasons")
def list_cancel_reasons(cfg: TenantConfig = Depends(get_cfg)):
    data = cfg.cancel_reasons
    return {"page": 1, "pageSize": len(data), "hasMore": False, "totalCount": len(data), "data": data}


# ---- reading jobs ------------------------------------------------------------------------------


@router.get("/jobs")
def list_jobs(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    customerId: int | None = None,
    locationId: int | None = None,
    jobStatus: str | None = None,
    createdOnOrAfter: str | None = None,
):
    """jobStatus takes one status or a comma-separated list (ASSUMED), for example
    Scheduled,Dispatched,Working for open jobs."""
    where, params = [], []
    if customerId is not None:
        where.append("customer_id = ?")
        params.append(customerId)
    if locationId is not None:
        where.append("location_id = ?")
        params.append(locationId)
    if jobStatus:
        statuses = jobStatus.split(",")
        where.append(f"status IN ({','.join('?' * len(statuses))})")
        params += statuses
    if createdOnOrAfter:
        where.append("created_on >= ?")
        params.append(createdOnOrAfter)
    sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    return page_of(conn, sql, params, lambda r: shape_job(conn, r), *pg)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return shape_job(conn, _one(conn, "jobs", job_id, "Job"))


@router.get("/jobs/{job_id}/history")
def job_history(job_id: int, conn: sqlite3.Connection = Depends(get_conn), pg: tuple[int, int] = Depends(paging)):
    _one(conn, "jobs", job_id, "Job")
    return page_of(
        conn, "SELECT * FROM job_history WHERE job_id = ? ORDER BY date, id", [job_id],
        lambda r: {"id": r["id"], "jobId": r["job_id"], "date": r["date"],
                   "eventType": r["event_type"], "text": r["text"]},
        *pg,
    )


@router.get("/appointments")
def list_appointments(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    jobId: int | None = None,
    technicianId: int | None = None,
    status: str | None = None,
    startsOnOrAfter: str | None = None,
    startsOnOrBefore: str | None = None,
):
    where, params = [], []
    for sql, value in (
        ("job_id = ?", jobId), ("technician_id = ?", technicianId), ("status = ?", status),
        ("start >= ?", startsOnOrAfter), ("start <= ?", startsOnOrBefore),
    ):
        if value is not None:
            where.append(sql)
            params.append(value)
    sql = "SELECT * FROM appointments" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY start, id"
    return page_of(conn, sql, params, shape_appointment, *pg)


@router.get("/appointments/{appointment_id}")
def get_appointment(appointment_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return shape_appointment(_one(conn, "appointments", appointment_id, "Appointment"))


# ---- booking -----------------------------------------------------------------------------------


def _resolve_window(cfg: TenantConfig, now: datetime, priority: str, start: datetime, end: datetime):
    """Map the arrival window on the request to a configured window, and apply the timing rules."""
    matched = cfg.match_window(start, end)
    if not matched:
        names = ", ".join(w.id for w in cfg.windows)
        raise ApiError(
            400, "invalid_window",
            f"arrivalWindowStart and arrivalWindowEnd must match one of the configured windows ({names}).",
            {"arrivalWindowStart": "no matching window"},
        )
    day, window = matched
    if not cfg.window_offered(day, window):
        raise ApiError(422, "window_not_offered", f"The {window.id} window is not offered on {day}.")
    window_start, window_end = cfg.window_bounds(day, window)
    still_open = window_end > now if window.after_hours else window_start >= now
    if not still_open:
        raise ApiError(422, "slot_in_past", f"The {window.id} window on {day} has already started.")
    if window.after_hours and cfg.urgent_only_after_hours and priority != "Urgent":
        raise ApiError(422, "after_hours_urgent_only", "After-hours slots are only for urgent jobs.")
    return day, window


def _insert_appointment(
    conn: sqlite3.Connection, cfg: TenantConfig, now: datetime, job_id: int, number: str, tech_id: int,
    bu_id: int, day: date, window: Window, start: datetime | None, end: datetime | None,
) -> int:
    window_start, window_end = cfg.window_bounds(day, window)
    stamp = iso(now)
    try:
        cur = conn.execute(
            """INSERT INTO appointments (job_id, appointment_number, technician_id, business_unit_id,
                   window_date, window_id, start, end, arrival_window_start, arrival_window_end,
                   status, created_on, modified_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Scheduled', ?, ?)""",
            (job_id, number, tech_id, bu_id, day.isoformat(), window.id, iso(start or window_start),
             iso(end or window_end), iso(window_start), iso(window_end), stamp, stamp),
        )
    except sqlite3.IntegrityError:
        # The database constraint is the last line of defense against a double booking.
        raise ApiError(409, "slot_taken", f"Technician {tech_id} was just booked in that window.") from None
    return cur.lastrowid


@router.post("/jobs", status_code=201)
def create_job(
    body: JobIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now: datetime = Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    """Create a job and its appointments. Capacity is taken in the same transaction, so a slot
    cannot be sold twice. Send an Idempotency-Key header (ASSUMED; real ServiceTitan has none) so a
    retried call returns the first job instead of booking again."""

    def run(c: sqlite3.Connection):
        customer = rules.require_customer(c, body.customerId)
        location = c.execute(
            "SELECT * FROM locations WHERE id = ? AND customer_id = ?", (body.locationId, body.customerId)
        ).fetchone()
        if not location:
            raise ApiError(422, "unknown_reference",
                           f"Location {body.locationId} does not belong to customer {body.customerId}.",
                           {"locationId": "unknown"})
        rules.require_residential(customer)
        rules.require_in_area(cfg, location["zip"])
        rules.check_refs(cfg, body.businessUnitId, body.jobTypeId, body.campaignId)
        job_type = cfg.job_type(body.jobTypeId)
        priority = body.priority or job_type["priority"]

        stamp = iso(now)
        job_id = c.execute(
            """INSERT INTO jobs (job_number, customer_id, location_id, business_unit_id, job_type_id,
                                 priority, campaign_id, summary, status, created_on, modified_on)
               VALUES ('', ?, ?, ?, ?, ?, ?, ?, 'Scheduled', ?, ?)""",
            (body.customerId, body.locationId, body.businessUnitId, body.jobTypeId, priority,
             body.campaignId, body.summary, stamp, stamp),
        ).lastrowid
        c.execute("UPDATE jobs SET job_number = ? WHERE id = ?", (str(job_id), job_id))
        for n, appt in enumerate(body.appointments, 1):
            day, window = _resolve_window(cfg, now, priority, appt.arrivalWindowStart, appt.arrivalWindowEnd)
            tech_id = capacity.allocate(
                c, cfg, day, window, body.businessUnitId, job_type["skills"], priority,
                location["zone_id"], appt.technicianIds,
            )
            _insert_appointment(c, cfg, now, job_id, f"{job_id}-{n}", tech_id, body.businessUnitId,
                                day, window, appt.start, appt.end)
            _history(c, job_id, now, "AppointmentScheduled",
                     f"Scheduled in the {window.id} window on {day} with technician {tech_id}.")
        _history(c, job_id, now, "JobCreated", f"Job created with priority {priority}.")
        return 201, shape_job(c, _one(c, "jobs", job_id, "Job"))

    return write(conn, run, idem_key=idempotency_key, route=_route(request),
                 body=body.model_dump(mode="json"))


@router.put("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: int,
    request: Request,
    body: CancelIn | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now: datetime = Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    """Cancel a job and free its technicians. Cancelling a cancelled job is a no-op."""
    body = body or CancelIn()

    def run(c: sqlite3.Connection):
        job = _one(c, "jobs", job_id, "Job")
        if body.reasonId is not None and not any(r["id"] == body.reasonId for r in cfg.cancel_reasons):
            raise ApiError(422, "unknown_reference", f"Cancel reason {body.reasonId} does not exist.",
                           {"reasonId": "unknown"})
        if job["status"] == "Canceled":
            return 200, shape_job(c, job)
        if job["status"] in ("Done", "Working"):
            raise ApiError(409, "invalid_state", f"A job that is {job['status']} cannot be cancelled.")
        stamp = iso(now)
        c.execute(
            "UPDATE appointments SET status = 'Canceled', modified_on = ? WHERE job_id = ? AND status <> 'Canceled'",
            (stamp, job_id),
        )
        c.execute("UPDATE jobs SET status = 'Canceled', cancel_reason_id = ?, modified_on = ? WHERE id = ?",
                  (body.reasonId, stamp, job_id))
        _history(c, job_id, now, "JobCanceled", body.memo or "Job cancelled.")
        return 200, shape_job(c, _one(c, "jobs", job_id, "Job"))

    return write(conn, run, idem_key=idempotency_key, route=_route(request),
                 body={"job": job_id, **body.model_dump()})


@router.patch("/appointments/{appointment_id}/reschedule")
def reschedule_appointment(
    appointment_id: int,
    body: RescheduleIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now: datetime = Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    """Move an appointment to another window. A failed move leaves the booking where it was."""

    def run(c: sqlite3.Connection):
        appt = _one(c, "appointments", appointment_id, "Appointment")
        if appt["status"] != "Scheduled":
            raise ApiError(409, "invalid_state", f"A {appt['status']} appointment cannot be rescheduled.")
        job = _one(c, "jobs", appt["job_id"], "Job")
        location = _one(c, "locations", job["location_id"], "Location")
        job_type = cfg.job_type(job["job_type_id"])
        day, window = _resolve_window(cfg, now, job["priority"], body.arrivalWindowStart, body.arrivalWindowEnd)

        def take(technician_ids: list[int]) -> int:
            return capacity.allocate(
                c, cfg, day, window, appt["business_unit_id"], job_type["skills"], job["priority"],
                location["zone_id"], technician_ids, exclude_appointment=appointment_id,
            )

        if body.technicianIds:
            tech_id = take(body.technicianIds)
        else:
            try:
                tech_id = take([appt["technician_id"]])  # keep the same technician when possible
            except ApiError:
                tech_id = take([])
        window_start, window_end = cfg.window_bounds(day, window)
        try:
            c.execute(
                """UPDATE appointments SET technician_id = ?, window_date = ?, window_id = ?, start = ?,
                       end = ?, arrival_window_start = ?, arrival_window_end = ?, modified_on = ?
                   WHERE id = ?""",
                (tech_id, day.isoformat(), window.id, iso(body.start or window_start),
                 iso(body.end or window_end), iso(window_start), iso(window_end), iso(now), appointment_id),
            )
        except sqlite3.IntegrityError:
            raise ApiError(409, "slot_taken", f"Technician {tech_id} was just booked in that window.") from None
        c.execute("UPDATE jobs SET modified_on = ? WHERE id = ?", (iso(now), job["id"]))
        _history(c, job["id"], now, "AppointmentRescheduled",
                 f"Moved to the {window.id} window on {day} with technician {tech_id}.")
        return 200, shape_appointment(_one(c, "appointments", appointment_id, "Appointment"))

    return write(conn, run, idem_key=idempotency_key, route=_route(request),
                 body={"appointment": appointment_id, **body.model_dump(mode="json")})


