"""Settings (business units, technicians, company profile), marketing campaigns, and telecom."""
import json
import sqlite3
from datetime import datetime
from typing import Literal

from fastapi import Depends, Header, Request
from pydantic import AwareDatetime, BaseModel

from .. import rules
from ..config import TenantConfig, iso
from ..db import page_of, write
from ..deps import get_cfg, get_conn, get_now, paging
from ..errors import ApiError
from ..util import phone_key
from . import tenant_router

router = tenant_router("settings")
marketing_router = tenant_router("marketing")
telecom_router = tenant_router("telecom")


def _list(data: list) -> dict:
    return {"page": 1, "pageSize": len(data), "hasMore": False, "totalCount": len(data), "data": data}


@router.get("/business-units")
def list_business_units(cfg: TenantConfig = Depends(get_cfg)):
    return _list(cfg.business_units)


def shape_technician(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "name": r["name"], "businessUnitId": r["business_unit_id"], "active": bool(r["active"]),
        "phoneNumber": r["phone"], "email": r["email"], "homeZoneId": r["home_zone_id"],
        "skills": json.loads(r["skills_json"]),
    }


@router.get("/technicians")
def list_technicians(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    businessUnitId: int | None = None,
    active: bool | None = None,
):
    where, params = [], []
    if businessUnitId is not None:
        where.append("business_unit_id = ?")
        params.append(businessUnitId)
    if active is not None:
        where.append("active = ?")
        params.append(int(active))
    sql = "SELECT * FROM technicians" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    return page_of(conn, sql, params, shape_technician, *pg)


@router.get("/technicians/{technician_id}")
def get_technician(technician_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM technicians WHERE id = ?", (technician_id,)).fetchone()
    if not row:
        raise ApiError(404, "not_found", f"Technician {technician_id} does not exist.")
    return shape_technician(row)


@router.get("/company-profile")
def company_profile(cfg: TenantConfig = Depends(get_cfg)):
    """Hours, service-area ZIPs, after-hours rules and configured fees. Not a ServiceTitan
    endpoint (ASSUMED); with real ServiceTitan this comes from the per-company config file."""
    return cfg.profile()


@marketing_router.get("/campaigns")
def list_campaigns(cfg: TenantConfig = Depends(get_cfg)):
    return _list(cfg.campaigns)


# ---- telecom -----------------------------------------------------------------------------------


class CallIn(BaseModel):
    externalId: str | None = None
    direction: Literal["Inbound", "Outbound"] = "Inbound"
    fromNumber: str
    toNumber: str = ""
    receivedOn: AwareDatetime | None = None
    duration: int = 0
    callType: Literal["Booked", "Unbooked", "Excused"] = "Unbooked"
    customerId: int | None = None
    jobId: int | None = None
    reason: str = ""


def shape_call(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "externalId": r["external_id"], "direction": r["direction"],
        "from": r["from_number"], "to": r["to_number"], "receivedOn": r["received_on"],
        "duration": r["duration"], "callType": r["call_type"], "customerId": r["customer_id"],
        "jobId": r["job_id"], "reason": r["reason"],
    }


@telecom_router.get("/calls")
def list_calls(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    phone: str | None = None,
    customerId: int | None = None,
    jobId: int | None = None,
    receivedOnOrAfter: str | None = None,
):
    """The call log. phone is the caller's number in any format. Newest first."""
    where, params = [], []
    if phone:
        where.append("from_key = ?")
        params.append(phone_key(phone))
    if customerId is not None:
        where.append("customer_id = ?")
        params.append(customerId)
    if jobId is not None:
        where.append("job_id = ?")
        params.append(jobId)
    if receivedOnOrAfter:
        where.append("received_on >= ?")
        params.append(receivedOnOrAfter)
    sql = "SELECT * FROM calls" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY received_on DESC, id DESC"
    return page_of(conn, sql, params, shape_call, *pg)


@telecom_router.get("/calls/{call_id}")
def get_call(call_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    if not row:
        raise ApiError(404, "not_found", f"Call {call_id} does not exist.")
    return shape_call(row)


@telecom_router.post("/calls", status_code=201)
def log_call(
    body: CallIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    now: datetime = Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    """Record a call. Real ServiceTitan fills its call log from the phone system, so this write
    endpoint is ASSUMED. A repeated externalId returns the first record."""
    if len(phone_key(body.fromNumber)) != 10:
        raise ApiError(400, "validation_error", "fromNumber needs 10 digits.", {"fromNumber": "bad phone"})

    def run(c: sqlite3.Connection):
        if body.externalId:
            existing = c.execute("SELECT * FROM calls WHERE external_id = ?", (body.externalId,)).fetchone()
            if existing:
                return 200, shape_call(existing)
        if body.customerId is not None:
            rules.require_customer(c, body.customerId)
        if body.jobId is not None and not c.execute("SELECT 1 FROM jobs WHERE id = ?", (body.jobId,)).fetchone():
            raise ApiError(422, "unknown_reference", f"Job {body.jobId} does not exist.", {"jobId": "unknown"})
        cur = c.execute(
            """INSERT INTO calls (external_id, direction, from_number, from_key, to_number, received_on,
                                  duration, call_type, customer_id, job_id, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (body.externalId, body.direction, body.fromNumber, phone_key(body.fromNumber), body.toNumber,
             iso(body.receivedOn or now), body.duration, body.callType, body.customerId, body.jobId, body.reason),
        )
        return 201, shape_call(c.execute("SELECT * FROM calls WHERE id = ?", (cur.lastrowid,)).fetchone())

    return write(conn, run, idem_key=idempotency_key, route=f"{request.method} {request.url.path}",
                 body=body.model_dump(mode="json"))
