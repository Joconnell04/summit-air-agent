"""Jobs and appointments (JPM): create, cancel, reschedule, history."""

import sqlite3
from datetime import date, datetime
from typing import Literal

from fastapi import Depends, Header, Request
from pydantic import AwareDatetime, BaseModel, Field

from .. import capacity, repo, rules
from ..config import TenantConfig, Window, iso
from ..db import page_of, write
from ..deps import get_cfg, get_conn, get_now, get_user_id, paging
from ..errors import ApiError
from . import tenant_router

router = tenant_router("jpm")

Priority = Literal["Low", "Normal", "High", "Urgent"]
JOB_STATUSES = ("Scheduled", "Dispatched", "InProgress", "Hold", "Completed", "Canceled")
APPOINTMENT_STATUSES = ("Scheduled", "Dispatched", "Working", "Hold", "Done", "Canceled")


class AppointmentIn(BaseModel):
    """start and end are required, as in ServiceTitan. The arrival window is optional there; the
    mock sells arrival windows, so it reads the window from the arrival times when they are sent
    and from start and end otherwise."""

    start: AwareDatetime
    end: AwareDatetime
    arrivalWindowStart: AwareDatetime | None = None
    arrivalWindowEnd: AwareDatetime | None = None
    technicianIds: list[int] | None = None  # the mock picks one when this is left out (not real)


class JobIn(BaseModel):
    customerId: int
    locationId: int
    businessUnitId: int
    jobTypeId: int
    priority: Priority
    campaignId: int
    summary: str | None = None
    appointments: list[AppointmentIn] = Field(min_length=1)


class CancelIn(BaseModel):
    reasonId: int
    memo: str


class JobUpdate(BaseModel):
    """A partial update, as ServiceTitan's PATCH jobs/{id} takes. The mock applies only these two;
    the real endpoint takes more fields."""

    locationId: int | None = None
    summary: str | None = None


class JobNoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    pinToTop: bool | None = None


class RescheduleIn(BaseModel):
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    arrivalWindowStart: AwareDatetime | None = None
    arrivalWindowEnd: AwareDatetime | None = None


def _times(
    start: datetime | None,
    end: datetime | None,
    window_start: datetime | None,
    window_end: datetime | None,
) -> tuple[datetime, datetime]:
    """The times that name the arrival window: the arrival times if sent, otherwise start and end."""
    if window_start and window_end:
        return window_start, window_end
    if start and end:
        return start, end
    raise ApiError(
        400,
        "validation_error",
        "Send start and end, or arrivalWindowStart and arrivalWindowEnd.",
        {"start": "required"},
    )


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
        "id": r["id"],
        "jobNumber": r["job_number"],
        "customerId": r["customer_id"],
        "locationId": r["location_id"],
        "jobStatus": r["status"],
        "completedOn": r["completed_on"],
        "businessUnitId": r["business_unit_id"],
        "jobTypeId": r["job_type_id"],
        "priority": r["priority"],
        "campaignId": r["campaign_id"],
        "summary": r["summary"],
        "firstAppointmentId": appts[0]["id"] if appts else None,
        "lastAppointmentId": appts[-1]["id"] if appts else None,
        "appointmentCount": len(appts),
        "noCharge": False,
        "notificationsEnabled": True,
        "createdOn": r["created_on"],
        "createdById": 4001,
        "modifiedOn": r["modified_on"],
        "tagTypeIds": [],
        "customFields": [],
        "externalData": [],
    }


def shape_appointment(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "jobId": r["job_id"],
        "appointmentNumber": r["appointment_number"],
        "start": r["start"],
        "end": r["end"],
        "arrivalWindowStart": r["arrival_window_start"],
        "arrivalWindowEnd": r["arrival_window_end"],
        "status": r["status"],
        "specialInstructions": None,
        "createdOn": r["created_on"],
        "modifiedOn": r["modified_on"],
        "customerId": dict(r).get("customer_id"),
        "unused": False,
        "createdById": 4001,
        "isConfirmed": False,
        "active": r["status"] != "Canceled",
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
    return {
        "page": 1,
        "pageSize": len(data),
        "hasMore": False,
        "totalCount": len(data),
        "data": data,
    }


@router.get("/job-types/{job_type_id}")
def get_job_type(job_type_id: int, cfg: TenantConfig = Depends(get_cfg)):
    found = cfg.job_type(job_type_id)
    if not found:
        raise ApiError(404, "not_found", f"Job type {job_type_id} does not exist.")
    return found


@router.get("/job-cancel-reasons")
def list_cancel_reasons(cfg: TenantConfig = Depends(get_cfg)):
    data = cfg.cancel_reasons
    return {
        "page": 1,
        "pageSize": len(data),
        "hasMore": False,
        "totalCount": len(data),
        "data": data,
    }


# ---- reading jobs ------------------------------------------------------------------------------


@router.get("/jobs")
def list_jobs(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    customerId: int | None = None,
    locationId: int | None = None,
    jobStatus: str | None = None,
    appointmentStatus: str | None = None,
    priority: str | None = None,
    createdOnOrAfter: str | None = None,
):
    """jobStatus takes one status, as in ServiceTitan; ask for open jobs by reading the customer's
    jobs and keeping the statuses you want."""
    where, params = [], []
    if customerId is not None:
        where.append("customer_id = ?")
        params.append(customerId)
    if locationId is not None:
        where.append("location_id = ?")
        params.append(locationId)
    if jobStatus:
        if jobStatus not in JOB_STATUSES:
            raise ApiError(
                400,
                "validation_error",
                "The request was not valid.",
                {"jobStatus": f"one of {', '.join(JOB_STATUSES)}"},
            )
        where.append("status = ?")
        params.append(jobStatus)
    if appointmentStatus:
        where.append("id IN (SELECT job_id FROM appointments WHERE status = ?)")
        params.append(appointmentStatus)
    if priority:
        where.append("priority = ?")
        params.append(priority)
    if createdOnOrAfter:
        where.append("created_on >= ?")
        params.append(createdOnOrAfter)
    sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    return page_of(conn, sql, params, lambda r: shape_job(conn, r), *pg)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return shape_job(conn, _one(conn, "jobs", job_id, "Job"))


@router.get("/jobs/{job_id}/history")
def job_history(
    job_id: int, conn: sqlite3.Connection = Depends(get_conn), pg: tuple[int, int] = Depends(paging)
):
    _one(conn, "jobs", job_id, "Job")
    return page_of(
        conn,
        "SELECT * FROM job_history WHERE job_id = ? ORDER BY date, id",
        [job_id],
        lambda r: {
            "id": r["id"],
            "jobId": r["job_id"],
            "date": r["date"],
            "eventType": r["event_type"],
            "text": r["text"],
        },
        *pg,
    )


@router.get("/jobs/{job_id}/notes")
def job_notes(
    job_id: int, conn: sqlite3.Connection = Depends(get_conn), pg: tuple[int, int] = Depends(paging)
):
    _one(conn, "jobs", job_id, "Job")
    return page_of(
        conn,
        "SELECT * FROM notes WHERE job_id = ? ORDER BY id",
        [job_id],
        lambda r: repo.shape_note(r, with_id=False),
        *pg,
    )


@router.post("/jobs/{job_id}/notes")
def add_job_note(
    job_id: int,
    body: JobNoteIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    now: datetime = Depends(get_now),
    user_id: int = Depends(get_user_id),
    idempotency_key: str | None = Header(None),
):
    """A job note belongs to the job; ServiceTitan's job note has no id."""

    def run(c: sqlite3.Connection):
        job = _one(c, "jobs", job_id, "Job")
        note_id = repo.insert_note(
            c,
            now,
            job["customer_id"],
            body.text,
            user_id,
            job_id=job_id,
            pin_to_top=bool(body.pinToTop),
        )
        return 200, repo.shape_note(_one(c, "notes", note_id, "Note"), with_id=False)

    return write(conn, run, idem_key=idempotency_key, route=_route(request), body=body.model_dump())


@router.get("/appointments")
def list_appointments(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    jobId: int | None = None,
    technicianId: int | None = None,
    customerId: int | None = None,
    status: str | None = None,
    startsOnOrAfter: str | None = None,
    startsBefore: str | None = None,
):
    where, params = [], []
    for sql, value in (
        ("a.job_id = ?", jobId),
        ("a.technician_id = ?", technicianId),
        ("j.customer_id = ?", customerId),
        ("a.status = ?", status),
        ("a.start >= ?", startsOnOrAfter),
        ("a.start < ?", startsBefore),
    ):
        if value is not None:
            where.append(sql)
            params.append(value)
    sql = (
        "SELECT a.*, j.customer_id AS customer_id FROM appointments a JOIN jobs j ON j.id = a.job_id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY a.start, a.id"
    )
    return page_of(conn, sql, params, shape_appointment, *pg)


@router.get("/appointments/{appointment_id}")
def get_appointment(appointment_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    row = _one(conn, "appointments", appointment_id, "Appointment")
    customer = conn.execute(
        "SELECT customer_id FROM jobs WHERE id = ?", (row["job_id"],)
    ).fetchone()
    return shape_appointment({**dict(row), "customer_id": customer["customer_id"]})


# ---- booking -----------------------------------------------------------------------------------


def _resolve_window(cfg: TenantConfig, now: datetime, start: datetime, end: datetime):
    """Map the arrival window on the request to a configured window, and apply the timing rules.
    Who may take an after-hours window is the company's rule, not the mock's."""
    matched = cfg.match_window(start, end)
    if not matched:
        names = ", ".join(w.id for w in cfg.windows)
        raise ApiError(
            400,
            "invalid_window",
            f"arrivalWindowStart and arrivalWindowEnd must match one of the configured windows ({names}).",
            {"arrivalWindowStart": "no matching window"},
        )
    day, window = matched
    if not cfg.window_offered(day, window):
        raise ApiError(
            422, "window_not_offered", f"The {window.id} window is not offered on {day}."
        )
    window_start, window_end = cfg.window_bounds(day, window)
    still_open = window_end > now if window.after_hours else window_start >= now
    if not still_open:
        raise ApiError(422, "slot_in_past", f"The {window.id} window on {day} has already started.")
    return day, window


def _insert_appointment(
    conn: sqlite3.Connection,
    cfg: TenantConfig,
    now: datetime,
    job_id: int,
    number: str,
    tech_id: int,
    bu_id: int,
    day: date,
    window: Window,
    start: datetime | None,
    end: datetime | None,
) -> int:
    window_start, window_end = cfg.window_bounds(day, window)
    stamp = iso(now)
    try:
        cur = conn.execute(
            """INSERT INTO appointments (job_id, appointment_number, technician_id, business_unit_id,
                   window_date, window_id, start, end, arrival_window_start, arrival_window_end,
                   status, created_on, modified_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Scheduled', ?, ?)""",
            (
                job_id,
                number,
                tech_id,
                bu_id,
                day.isoformat(),
                window.id,
                iso(start or window_start),
                iso(end or window_end),
                iso(window_start),
                iso(window_end),
                stamp,
                stamp,
            ),
        )
    except sqlite3.IntegrityError:
        # The database constraint is the last line of defense against a double booking.
        raise ApiError(
            409, "slot_taken", f"Technician {tech_id} was just booked in that window."
        ) from None
    return cur.lastrowid


@router.post("/jobs")
def create_job(
    body: JobIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now: datetime = Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    """Create a job and its appointments. Capacity is taken in the same transaction, so a slot
    cannot be sold twice (real ServiceTitan lets a job save past capacity). An Idempotency-Key
    header makes a retried call return the first job; it is not in the real API, and callers must
    not depend on it."""

    def run(c: sqlite3.Connection):
        rules.require_customer(c, body.customerId)
        location = c.execute(
            "SELECT * FROM locations WHERE id = ? AND customer_id = ?",
            (body.locationId, body.customerId),
        ).fetchone()
        if not location:
            raise ApiError(
                422,
                "unknown_reference",
                f"Location {body.locationId} does not belong to customer {body.customerId}.",
                {"locationId": "unknown"},
            )
        rules.check_refs(cfg, body.businessUnitId, body.jobTypeId, body.campaignId)
        job_type = cfg.job_type(body.jobTypeId)
        priority = body.priority

        stamp = iso(now)
        job_id = c.execute(
            """INSERT INTO jobs (job_number, customer_id, location_id, business_unit_id, job_type_id,
                                 priority, campaign_id, summary, status, created_on, modified_on)
               VALUES ('', ?, ?, ?, ?, ?, ?, ?, 'Scheduled', ?, ?)""",
            (
                body.customerId,
                body.locationId,
                body.businessUnitId,
                body.jobTypeId,
                priority,
                body.campaignId,
                body.summary or "",
                stamp,
                stamp,
            ),
        ).lastrowid
        c.execute("UPDATE jobs SET job_number = ? WHERE id = ?", (str(job_id), job_id))
        for n, appt in enumerate(body.appointments, 1):
            day, window = _resolve_window(
                cfg,
                now,
                *_times(appt.start, appt.end, appt.arrivalWindowStart, appt.arrivalWindowEnd),
            )
            tech_id = capacity.allocate(
                c, cfg, day, window, body.businessUnitId, job_type["skills"], appt.technicianIds
            )
            _insert_appointment(
                c,
                cfg,
                now,
                job_id,
                f"{job_id}-{n}",
                tech_id,
                body.businessUnitId,
                day,
                window,
                appt.start,
                appt.end,
            )
            _history(
                c,
                job_id,
                now,
                "AppointmentScheduled",
                f"Scheduled in the {window.id} window on {day} with technician {tech_id}.",
            )
        _history(c, job_id, now, "JobCreated", f"Job created with priority {priority}.")
        return 200, shape_job(c, _one(c, "jobs", job_id, "Job"))

    return write(
        conn,
        run,
        idem_key=idempotency_key,
        route=_route(request),
        body=body.model_dump(mode="json"),
    )


@router.put("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: int,
    request: Request,
    body: CancelIn,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now: datetime = Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    """Cancel a job and free its technicians. Cancelling a cancelled job is a no-op. Like the real
    endpoint it answers with an empty body."""

    def run(c: sqlite3.Connection):
        job = _one(c, "jobs", job_id, "Job")
        if not any(r["id"] == body.reasonId for r in cfg.cancel_reasons):
            raise ApiError(
                422,
                "unknown_reference",
                f"Cancel reason {body.reasonId} does not exist.",
                {"reasonId": "unknown"},
            )
        if job["status"] == "Canceled":
            return 200, {}
        if job["status"] in ("Completed", "InProgress"):
            raise ApiError(
                409, "invalid_state", f"A job that is {job['status']} cannot be cancelled."
            )
        stamp = iso(now)
        c.execute(
            "UPDATE appointments SET status = 'Canceled', modified_on = ? WHERE job_id = ? AND status <> 'Canceled'",
            (stamp, job_id),
        )
        c.execute(
            "UPDATE jobs SET status = 'Canceled', cancel_reason_id = ?, modified_on = ? WHERE id = ?",
            (body.reasonId, stamp, job_id),
        )
        _history(c, job_id, now, "JobCanceled", body.memo or "Job cancelled.")
        return 200, {}

    return write(
        conn,
        run,
        idem_key=idempotency_key,
        route=_route(request),
        body={"job": job_id, **body.model_dump()},
    )


@router.patch("/jobs/{job_id}")
def update_job(
    job_id: int,
    body: JobUpdate,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    now: datetime = Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    """Change a job's location or summary. The new location must be an active one of the job's
    customer, and a closed job can't change."""

    def run(c: sqlite3.Connection):
        job = _one(c, "jobs", job_id, "Job")
        if job["status"] in ("Completed", "Canceled"):
            raise ApiError(409, "invalid_state", f"A {job['status']} job cannot be changed.")
        stamp = iso(now)
        if body.locationId is not None and body.locationId != job["location_id"]:
            location = c.execute(
                "SELECT * FROM locations WHERE id = ? AND customer_id = ?",
                (body.locationId, job["customer_id"]),
            ).fetchone()
            if not location or not location["active"]:
                raise ApiError(
                    422,
                    "unknown_reference",
                    f"Location {body.locationId} is not an active location of customer "
                    f"{job['customer_id']}.",
                    {"locationId": "unknown or inactive"},
                )
            c.execute(
                "UPDATE jobs SET location_id = ?, modified_on = ? WHERE id = ?",
                (body.locationId, stamp, job_id),
            )
            _history(
                c, job_id, now, "LocationChanged",
                f"Location changed from {job['location_id']} to {body.locationId}.",
            )
        if body.summary is not None:
            c.execute(
                "UPDATE jobs SET summary = ?, modified_on = ? WHERE id = ?",
                (body.summary, stamp, job_id),
            )
        return 200, shape_job(c, _one(c, "jobs", job_id, "Job"))

    return write(
        conn,
        run,
        idem_key=idempotency_key,
        route=_route(request),
        body={"job": job_id, **body.model_dump()},
    )


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
    """Move an appointment to another window. A failed move leaves the booking where it was. The
    appointment keeps its technician when that technician is free in the new window and otherwise
    gets the least booked free one; both are the mock's doing (a real appointment is reassigned by
    dispatch). To choose, call dispatch appointment-assignments/assign-technicians afterwards."""

    def run(c: sqlite3.Connection):
        appt = _one(c, "appointments", appointment_id, "Appointment")
        if appt["status"] != "Scheduled":
            raise ApiError(
                409, "invalid_state", f"A {appt['status']} appointment cannot be rescheduled."
            )
        job = _one(c, "jobs", appt["job_id"], "Job")
        job_type = cfg.job_type(job["job_type_id"])
        day, window = _resolve_window(
            cfg,
            now,
            *_times(body.start, body.end, body.arrivalWindowStart, body.arrivalWindowEnd),
        )

        def take(technician_ids: list[int]) -> int:
            return capacity.allocate(
                c,
                cfg,
                day,
                window,
                appt["business_unit_id"],
                job_type["skills"],
                technician_ids,
                exclude_appointment=appointment_id,
            )

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
                (
                    tech_id,
                    day.isoformat(),
                    window.id,
                    iso(body.start or window_start),
                    iso(body.end or window_end),
                    iso(window_start),
                    iso(window_end),
                    iso(now),
                    appointment_id,
                ),
            )
        except sqlite3.IntegrityError:
            raise ApiError(
                409, "slot_taken", f"Technician {tech_id} was just booked in that window."
            ) from None
        c.execute("UPDATE jobs SET modified_on = ? WHERE id = ?", (iso(now), job["id"]))
        _history(
            c,
            job["id"],
            now,
            "AppointmentRescheduled",
            f"Moved to the {window.id} window on {day} with technician {tech_id}.",
        )
        return 200, shape_appointment(
            {
                **dict(_one(c, "appointments", appointment_id, "Appointment")),
                "customer_id": job["customer_id"],
            }
        )

    return write(
        conn,
        run,
        idem_key=idempotency_key,
        route=_route(request),
        body={"appointment": appointment_id, **body.model_dump(mode="json")},
    )
