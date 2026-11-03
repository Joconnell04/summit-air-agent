"""Memberships, recurring service visits, and installed equipment."""

import sqlite3
from datetime import datetime

from fastapi import Depends

from ..config import TenantConfig
from ..db import page_of
from ..deps import get_cfg, get_conn, get_now, paging
from ..errors import ApiError
from . import tenant_router

router = tenant_router("memberships")
equipment_router = tenant_router("equipmentsystems")


def shape_type(t: dict) -> dict:
    """A membership type as ServiceTitan has it: a discount, recurring services and billing
    options. It has no perks block; the company's perks (priority scheduling, a repair discount)
    come from the tool server's tenant config, not from here."""
    return {
        "id": t["id"],
        "name": t["name"],
        "active": t["active"],
        "discountMode": "Percentage",
        "discount": t["perks"]["repairDiscountPercent"],
        "discounts": [],
        "recurringServices": [{"name": v["name"], "months": v["months"]} for v in t["visits"]],
        "durationBilling": [],
    }


def effective_status(row: sqlite3.Row, today: str) -> str:
    """A membership stored as Active whose end date has passed reads as Expired, so a lapsed plan
    is never mistaken for a current one or for no plan at all."""
    if row["status"] == "Active" and row["to_date"] < today:
        return "Expired"
    return row["status"]


def shape_membership(r: sqlite3.Row, today: str) -> dict:
    status = effective_status(r, today)
    return {
        "id": r["id"],
        "customerId": r["customer_id"],
        "locationId": r["location_id"],
        "membershipTypeId": r["membership_type_id"],
        "status": status,
        "active": status == "Active",
        "from": r["from_date"],
        "to": r["to_date"],
        "memo": r["memo"],
        "createdOn": r["created_on"],
        "modifiedOn": r["modified_on"],
    }


@router.get("/membership-types")
def list_membership_types(cfg: TenantConfig = Depends(get_cfg)):
    types = [shape_type(t) for t in cfg.membership_types]
    return {
        "page": 1,
        "pageSize": len(types),
        "hasMore": False,
        "totalCount": len(types),
        "data": types,
    }


@router.get("/membership-types/{type_id}")
def get_membership_type(type_id: int, cfg: TenantConfig = Depends(get_cfg)):
    found = next((t for t in cfg.membership_types if t["id"] == type_id), None)
    if not found:
        raise ApiError(404, "not_found", f"Membership type {type_id} does not exist.")
    return shape_type(found)


@router.get("/memberships")
def list_memberships(
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now: datetime = Depends(get_now),
    pg: tuple[int, int] = Depends(paging),
    customerIds: str | None = None,
    status: str | None = None,
):
    """customerIds is a comma-separated list, as in ServiceTitan; there is no location filter.
    Every status is returned unless status is given, so an expired plan shows up as Expired."""
    today = now.astimezone(cfg.tz).date().isoformat()
    where, params = [], []
    if customerIds:
        wanted = [int(v) for v in customerIds.split(",") if v.strip().isdigit()]
        where.append(f"customer_id IN ({','.join('?' * len(wanted))})")
        params += wanted
    sql = (
        "SELECT * FROM memberships"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY id"
    )
    # status is derived from the end date, so it is filtered after the query.
    rows = [shape_membership(r, today) for r in conn.execute(sql, params)]
    if status:
        rows = [r for r in rows if r["status"] == status]
    start = (pg[0] - 1) * pg[1]
    return {
        "page": pg[0],
        "pageSize": pg[1],
        "hasMore": start + pg[1] < len(rows),
        "totalCount": len(rows),
        "data": rows[start : start + pg[1]],
    }


@router.get("/memberships/{membership_id}")
def get_membership(
    membership_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now: datetime = Depends(get_now),
):
    row = conn.execute("SELECT * FROM memberships WHERE id = ?", (membership_id,)).fetchone()
    if not row:
        raise ApiError(404, "not_found", f"Membership {membership_id} does not exist.")
    return shape_membership(row, now.astimezone(cfg.tz).date().isoformat())


def shape_event(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "locationRecurringServiceId": r["id"],
        "locationRecurringServiceName": r["name"],
        "membershipId": r["membership_id"],
        "membershipName": None,
        "status": r["status"],
        "date": r["date"],
        "jobId": r["job_id"],
        "createdOn": r["date"],
        "modifiedOn": r["date"],
    }


@router.get("/recurring-service-events")
def list_recurring_service_events(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    locationId: int | None = None,
    jobId: int | None = None,
    status: str | None = None,
    createdOnOrAfter: str | None = None,
):
    """Service visits a membership includes. status NotAttempted means due and not yet booked; Won
    is a visit that was booked and done."""
    where, params = [], []
    for sql, value in (
        ("location_id = ?", locationId),
        ("job_id = ?", jobId),
        ("status = ?", status),
        ("date >= ?", createdOnOrAfter),
    ):
        if value is not None:
            where.append(sql)
            params.append(value)
    sql = (
        "SELECT * FROM recurring_service_events"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY date, id"
    )
    return page_of(conn, sql, params, shape_event, *pg)


def shape_equipment(cfg: TenantConfig, r: sqlite3.Row) -> dict:
    """Installed equipment. The kind of system is the type object (an id and the company's name for
    it), not a code; the install year is read from installedOn."""
    types = sorted(cfg.equipment_labels)
    kind = r["system_type"]
    return {
        "id": r["id"],
        "active": True,
        "locationId": r["location_id"],
        "customerId": r["customer_id"],
        "name": r["name"],
        "type": {
            "id": types.index(kind) + 1 if kind in types else 0,
            "name": cfg.equipment_label(kind),
        },
        "installedOn": r["installed_on"],
        "createdOn": r["installed_on"],
        "modifiedOn": r["installed_on"],
        "serialNumber": "",
        "memo": r["memo"],
        "manufacturer": r["manufacturer"],
        "model": r["model"],
        "cost": 0,
        "status": r["status"],
    }


@equipment_router.get("/installed-equipment")
def list_installed_equipment(
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    pg: tuple[int, int] = Depends(paging),
    locationIds: str | None = None,
):
    """locationIds is a comma-separated list; there is no customer filter."""
    where, params = [], []
    if locationIds:
        wanted = [int(v) for v in locationIds.split(",") if v.strip().isdigit()]
        where.append(f"location_id IN ({','.join('?' * len(wanted))})")
        params += wanted
    sql = (
        "SELECT * FROM installed_equipment"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY id"
    )
    return page_of(conn, sql, params, lambda r: shape_equipment(cfg, r), *pg)
