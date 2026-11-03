"""Settings (business units, technicians, tag types), call reasons, campaigns, and telecom.

The call log is ServiceTitan's phone system's: this API can read calls and classify one (call type,
reason, tag), never create one. The mock's own way to add a call is POST /debug/calls in app.py."""

import json
import sqlite3
from datetime import datetime
from typing import Literal

from fastapi import Depends
from pydantic import AwareDatetime, BaseModel

from ..config import TenantConfig, iso
from ..db import page_of
from ..deps import get_cfg, get_conn, get_now, paging
from ..errors import ApiError
from ..util import phone_key
from . import tenant_router

router = tenant_router("settings")
marketing_router = tenant_router("marketing")
jbce_router = tenant_router("jbce")
telecom_router = tenant_router("telecom")  # v2: one call, and classifying it
telecom_v3_router = tenant_router("telecom", "v3")  # v3: the call feed

CallType = Literal["Abandoned", "Unbooked", "Excused", "Booked", "NotLead"]


def _list(data: list) -> dict:
    return {
        "page": 1,
        "pageSize": len(data),
        "hasMore": False,
        "totalCount": len(data),
        "data": data,
    }


@router.get("/business-units")
def list_business_units(cfg: TenantConfig = Depends(get_cfg)):
    return _list(cfg.business_units)


@router.get("/tag-types")
def list_tag_types(cfg: TenantConfig = Depends(get_cfg)):
    """Tag types a person can put on a call, customer or job."""
    return _list(
        [
            {
                **t,
                "importance": "Medium",
                "isConversionOpportunity": False,
                "allowToUseOnTimesheetActivity": False,
                "isVisibleOnDispatchBoard": False,
                "createdOn": "2026-01-01T00:00:00Z",
                "modifiedOn": "2026-01-01T00:00:00Z",
            }
            for t in cfg.tag_types
        ]
    )


def shape_technician(r: sqlite3.Row) -> dict:
    skills = json.loads(r["skills_json"])
    return {
        "id": r["id"],
        "userId": r["id"],
        "name": r["name"],
        "businessUnitId": r["business_unit_id"],
        "mainZoneId": r["home_zone_id"],
        "zoneIds": [r["home_zone_id"]] if r["home_zone_id"] else [],
        "active": bool(r["active"]),
        "phoneNumber": r["phone"],
        "email": r["email"],
        "skills": [{"id": n, "name": name} for n, name in enumerate(skills, 1)],
        "createdOn": "2026-01-01T00:00:00Z",
        "modifiedOn": "2026-01-01T00:00:00Z",
    }


@router.get("/technicians")
def list_technicians(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    ids: str | None = None,
    name: str | None = None,
    active: bool | None = None,
):
    """The filters ServiceTitan has: ids, name, active. There is no business unit filter."""
    where, params = [], []
    if ids:
        wanted = [int(v) for v in ids.split(",") if v.strip().isdigit()]
        where.append(f"id IN ({','.join('?' * len(wanted))})")
        params += wanted
    if name:
        where.append("name LIKE ?")
        params.append(f"%{name}%")
    if active is not None:
        where.append("active = ?")
        params.append(int(active))
    sql = (
        "SELECT * FROM technicians"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY id"
    )
    return page_of(conn, sql, params, shape_technician, *pg)


@router.get("/technicians/{technician_id}")
def get_technician(technician_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM technicians WHERE id = ?", (technician_id,)).fetchone()
    if not row:
        raise ApiError(404, "not_found", f"Technician {technician_id} does not exist.")
    return shape_technician(row)


@marketing_router.get("/campaigns")
def list_campaigns(cfg: TenantConfig = Depends(get_cfg)):
    return _list(cfg.campaigns)


@jbce_router.get("/call-reasons")
def list_call_reasons(cfg: TenantConfig = Depends(get_cfg)):
    return _list(
        [
            {
                "id": r["id"],
                "name": r["name"],
                "isLead": r["isLead"],
                "active": r["active"],
                "createdOn": "2026-01-01T00:00:00Z",
                "modifiedOn": "2026-01-01T00:00:00Z",
            }
            for r in cfg.call_reasons
        ]
    )


# ---- telecom -----------------------------------------------------------------------------------


def _duration(seconds: int) -> str:
    """A time span, as ServiceTitan writes one: HH:MM:SS."""
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _call_parts(conn: sqlite3.Connection, cfg: TenantConfig, r: sqlite3.Row) -> dict:
    reason = next((x for x in cfg.call_reasons if x["id"] == r["reason_id"]), None)
    customer = (
        conn.execute("SELECT id, name FROM customers WHERE id = ?", (r["customer_id"],)).fetchone()
        if r["customer_id"]
        else None
    )
    campaign = next((c for c in cfg.campaigns if c["id"] == r["campaign_id"]), None)
    tag_ids = [
        t[0]
        for t in conn.execute("SELECT tag_type_id FROM call_tags WHERE call_id = ?", (r["id"],))
    ]
    tags = [{"id": t["id"], "typeName": t["name"]} for t in cfg.tag_types if t["id"] in tag_ids]
    return {
        "reason": {
            "id": reason["id"],
            "name": reason["name"],
            "lead": reason["isLead"],
            "active": reason["active"],
        }
        if reason
        else None,
        "customer": {"id": customer["id"], "name": customer["name"]} if customer else None,
        "campaign": {"id": campaign["id"], "name": campaign["name"]} if campaign else None,
        "tags": tags,
    }


def shape_call(conn: sqlite3.Connection, cfg: TenantConfig, r: sqlite3.Row) -> dict:
    """The v3 call: a bundle (job link, business unit) around the lead call, which holds the call
    itself. Duration is a time span, the reason an object, and an outbound call has no callType."""
    parts = _call_parts(conn, cfg, r)
    return {
        "id": r["id"],
        "jobNumber": str(r["job_id"]) if r["job_id"] else None,
        "projectId": 0,
        "businessUnit": None,
        "type": None,
        "leadCall": {
            "createdOn": r["received_on"],
            "active": True,
            "id": r["id"],
            "receivedOn": r["received_on"],
            "duration": _duration(r["duration"]),
            "from": r["from_number"],
            "to": r["to_number"],
            "direction": r["direction"],
            "callType": r["call_type"],
            "reason": parts["reason"],
            "recordingUrl": None,
            "voiceMailUrl": None,
            "createdBy": None,
            "customer": parts["customer"],
            "campaign": parts["campaign"],
        },
        "modifiedOn": r["modified_on"] or r["received_on"],
        "agent": None,
        "sid": r["sid"],
        "tags": parts["tags"],
    }


def shape_call_detail(conn: sqlite3.Connection, cfg: TenantConfig, r: sqlite3.Row) -> dict:
    """The v2 single-call shape: the lead call's fields at the top level."""
    bundle = shape_call(conn, cfg, r)
    return {
        **{k: v for k, v in bundle["leadCall"].items() if k not in ("createdOn", "active")},
        "modifiedOn": bundle["modifiedOn"],
        "agent": None,
        "sid": r["sid"],
        "tags": bundle["tags"],
    }


@telecom_v3_router.get("/calls")
def list_calls(
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    pg: tuple[int, int] = Depends(paging),
    callerPhoneNumber: str | None = None,
    phoneNumberCalled: str | None = None,
    createdOnOrAfter: str | None = None,
    createdBefore: str | None = None,
    modifiedOnOrAfter: str | None = None,
    sid: str | None = None,
    tag: str | None = None,
    campaignId: int | None = None,
    minDuration: int | None = None,
):
    """The call feed, newest first. callerPhoneNumber is who called (the from number) and
    phoneNumberCalled is the number dialed, in any format; tag is a tag type name. There is no
    customer or job filter."""
    where, params = [], []
    if callerPhoneNumber:
        where.append("from_key = ?")
        params.append(phone_key(callerPhoneNumber))
    if phoneNumberCalled:
        where.append("to_key = ?")
        params.append(phone_key(phoneNumberCalled))
    if createdOnOrAfter:
        where.append("received_on >= ?")
        params.append(createdOnOrAfter)
    if createdBefore:
        where.append("received_on < ?")
        params.append(createdBefore)
    if modifiedOnOrAfter:
        where.append("COALESCE(NULLIF(modified_on, ''), received_on) >= ?")
        params.append(modifiedOnOrAfter)
    if sid:
        where.append("sid = ?")
        params.append(sid)
    if campaignId is not None:
        where.append("campaign_id = ?")
        params.append(campaignId)
    if minDuration is not None:
        where.append("duration >= ?")
        params.append(minDuration)
    if tag:
        wanted = [t["id"] for t in cfg.tag_types if t["name"] == tag]
        where.append(
            f"id IN (SELECT call_id FROM call_tags WHERE tag_type_id IN ({','.join('?' * len(wanted))}))"
            if wanted
            else "0"
        )
        params += wanted
    sql = (
        "SELECT * FROM calls"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY received_on DESC, id DESC"
    )
    return page_of(conn, sql, params, lambda r: shape_call(conn, cfg, r), *pg)


def _call(conn: sqlite3.Connection, call_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    if not row:
        raise ApiError(404, "not_found", f"Call {call_id} does not exist.")
    return row


@telecom_router.get("/calls/{call_id}")
def get_call(
    call_id: int, conn: sqlite3.Connection = Depends(get_conn), cfg: TenantConfig = Depends(get_cfg)
):
    return shape_call_detail(conn, cfg, _call(conn, call_id))


class ReasonIn(BaseModel):
    name: str
    lead: bool


class CallUpdate(BaseModel):
    """ServiceTitan's classify-a-call body. The real one also requires customer and location
    objects; the mock reads only the fields below and ignores those two (ASSUMED to be enough)."""

    callType: CallType | None = None
    excuseMemo: str | None = None
    campaignId: int | None = None
    jobId: int | None = None
    reason: ReasonIn | None = None
    tag: str | None = None
    customer: dict | None = None
    location: dict | None = None
    agentId: int | None = None
    callId: int | None = None


@telecom_router.put("/calls/{call_id}")
def classify_call(
    call_id: int,
    body: CallUpdate,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now: datetime = Depends(get_now),
):
    """Classify a call: set its call type, reason or job, and put a tag on it. This is the only
    write the API offers on a call. A tag is added, never replaced."""
    row = _call(conn, call_id)
    reason_id = row["reason_id"]
    if body.reason is not None and body.reason.name:
        match = next(
            (r for r in cfg.call_reasons if r["name"] == body.reason.name and r["active"]), None
        )
        if not match:
            raise ApiError(
                422,
                "unknown_reference",
                f"Call reason {body.reason.name!r} is not available.",
                {"reason": "unknown"},
            )
        reason_id = match["id"]
    tag_type = None
    if body.tag:
        tag_type = next((t for t in cfg.tag_types if t["name"] == body.tag and t["active"]), None)
        if not tag_type:
            raise ApiError(
                422,
                "unknown_reference",
                f"Tag type {body.tag!r} is not available.",
                {"tag": "unknown"},
            )
    if (
        body.jobId is not None
        and not conn.execute("SELECT 1 FROM jobs WHERE id = ?", (body.jobId,)).fetchone()
    ):
        raise ApiError(
            422, "unknown_reference", f"Job {body.jobId} does not exist.", {"jobId": "unknown"}
        )
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE calls SET call_type = ?, reason_id = ?, job_id = ?, campaign_id = ?, modified_on = ? WHERE id = ?",
            (
                body.callType or row["call_type"],
                reason_id,
                body.jobId if body.jobId is not None else row["job_id"],
                body.campaignId if body.campaignId is not None else row["campaign_id"],
                iso(now),
                call_id,
            ),
        )
        if tag_type:
            conn.execute(
                "INSERT OR IGNORE INTO call_tags (call_id, tag_type_id) VALUES (?, ?)",
                (call_id, tag_type["id"]),
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return shape_call_detail(conn, cfg, _call(conn, call_id))


class CallIn(BaseModel):
    """Not a ServiceTitan body: the call as the phone system would log it. Used by POST
    /debug/calls only."""

    sid: str | None = None
    direction: Literal["Inbound", "Outbound"]
    fromNumber: str
    toNumber: str
    receivedOn: AwareDatetime | None = None
    durationSeconds: int = 0
    callType: CallType | None = None
    customerId: int | None = None
    jobId: int | None = None
    reason: str | None = None
    tags: list[str] = []
