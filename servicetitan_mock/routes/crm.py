"""CRM: customers, locations, contacts, bookings and leads."""

import json
import sqlite3
from typing import Literal

from fastapi import Depends, Header, Request
from pydantic import BaseModel, Field, model_validator

from .. import repo, rules
from ..config import TenantConfig, iso
from ..db import page_of, write
from ..deps import get_cfg, get_conn, get_now, paging
from ..errors import ApiError
from ..util import phone_key, street_key, unit_key
from . import tenant_router

router = tenant_router("crm")


class Address(BaseModel):
    street: str = Field(min_length=1)
    unit: str = ""
    city: str = ""
    state: str = ""
    zip: str = Field(pattern=r"^\d{5}$")
    country: str = "USA"


class ContactIn(BaseModel):
    type: Literal["Phone", "MobilePhone", "Email"]
    value: str = Field(min_length=3)
    memo: str = ""
    role: str | None = None  # owner, tenant, adult_child, ... (ASSUMED field)
    name: str | None = None

    @model_validator(mode="after")
    def phone_has_ten_digits(self):
        if self.type != "Email" and len(phone_key(self.value)) != 10:
            raise ValueError("a phone number needs 10 digits")
        return self


class LocationIn(BaseModel):
    name: str | None = None
    address: Address
    contacts: list[ContactIn] = []


class CustomerIn(BaseModel):
    name: str = Field(min_length=1)
    type: Literal["Residential", "Commercial"] = "Residential"
    locations: list[LocationIn] = []
    contacts: list[ContactIn] = []


class LocationCreate(LocationIn):
    customerId: int


class BookingIn(BaseModel):
    source: str = ""
    externalId: str | None = None
    name: str = Field(min_length=1)
    summary: str = ""
    start: str | None = None
    priority: Literal["Low", "Normal", "High", "Urgent"] = "Normal"
    customerId: int | None = None
    businessUnitId: int | None = None
    jobTypeId: int | None = None
    campaignId: int | None = None
    address: Address
    contacts: list[ContactIn] = []


class LeadIn(BaseModel):
    kind: str  # one of the tenant's lead_kinds, checked in the route
    customerType: Literal["Residential", "Commercial"] = "Residential"  # ServiceTitan's own enum
    customerId: int | None = None
    name: str = Field(min_length=1)
    phone: str = ""
    street: str = ""
    zip: str = ""
    summary: str = Field(min_length=1)
    campaignId: int | None = None


def _route(request: Request) -> str:
    return f"{request.method} {request.url.path}"


def _one(conn: sqlite3.Connection, table: str, row_id: int, what: str) -> sqlite3.Row:
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if not row:
        raise ApiError(404, "not_found", f"{what} {row_id} does not exist.")
    return row


# ---- customers and locations -------------------------------------------------------------------


def _add_location(conn, cfg, now, customer_id: int, loc: LocationIn) -> int:
    name = loc.name or loc.address.street
    location_id = repo.insert_location(conn, cfg, now, customer_id, name, loc.address.model_dump())
    for contact in loc.contacts:
        repo.insert_contact(conn, now, customer_id, contact.model_dump(), location_id)
    return location_id


@router.get("/customers")
def list_customers(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    name: str | None = None,
    phone: str | None = None,
    street: str | None = None,
    unit: str | None = None,
    zip: str | None = None,
    type: str | None = None,
    active: bool | None = None,
):
    """phone matches any contact on the customer or its locations, in any format.
    street, unit and zip match any of the customer's locations."""
    where, params = [], []
    if name:
        where.append("c.name LIKE ?")
        params.append(f"%{name}%")
    if phone:
        where.append(
            "EXISTS (SELECT 1 FROM contacts k WHERE k.customer_id = c.id AND k.phone_key = ?)"
        )
        params.append(phone_key(phone))
    if street or unit or zip:
        sub, sub_params = _location_filter(street, unit, zip)
        where.append(f"EXISTS (SELECT 1 FROM locations l WHERE l.customer_id = c.id AND {sub})")
        params += sub_params
    if type:
        where.append("c.type = ?")
        params.append(type)
    if active is not None:
        where.append("c.active = ?")
        params.append(int(active))
    sql = (
        "SELECT c.* FROM customers c"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY c.id"
    )
    return page_of(conn, sql, params, lambda r: repo.shape_customer(conn, r), *pg)


@router.get("/customers/{customer_id}")
def get_customer(customer_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return repo.shape_customer(conn, _one(conn, "customers", customer_id, "Customer"))


@router.post("/customers", status_code=201)
def create_customer(
    body: CustomerIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now=Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    def run(c):
        customer_id = repo.insert_customer(c, now, body.name, body.type)
        for contact in body.contacts:
            repo.insert_contact(c, now, customer_id, contact.model_dump())
        for loc in body.locations:
            _add_location(c, cfg, now, customer_id, loc)
        result = repo.shape_customer(c, _one(c, "customers", customer_id, "Customer"))
        result["locations"] = [
            repo.shape_location(r)
            for r in c.execute(
                "SELECT * FROM locations WHERE customer_id = ? ORDER BY id", (customer_id,)
            )
        ]
        return 201, result

    return write(conn, run, idem_key=idempotency_key, route=_route(request), body=body.model_dump())


def _location_filter(
    street: str | None, unit: str | None, zip_code: str | None
) -> tuple[str, list]:
    where, params = ["1 = 1"], []
    if street:
        where.append("l.street_key = ?")
        params.append(street_key(street))
    if unit:
        where.append("l.unit_key = ?")
        params.append(unit_key(unit))
    if zip_code:
        where.append("l.zip = ?")
        params.append(zip_code)
    return " AND ".join(where), params


@router.get("/locations")
def list_locations(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    customerId: int | None = None,
    street: str | None = None,
    unit: str | None = None,
    zip: str | None = None,
    active: bool | None = None,
):
    """Address lookup: street is compared after normalizing (St and Street match). Leave unit out
    to get every unit in the building."""
    sub, params = _location_filter(street, unit, zip)
    where = [sub]
    if customerId is not None:
        where.append("l.customer_id = ?")
        params.append(customerId)
    if active is not None:
        where.append("l.active = ?")
        params.append(int(active))
    sql = f"SELECT l.* FROM locations l WHERE {' AND '.join(where)} ORDER BY l.id"
    return page_of(conn, sql, params, repo.shape_location, *pg)


@router.get("/locations/{location_id}")
def get_location(location_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return repo.shape_location(_one(conn, "locations", location_id, "Location"))


@router.post("/locations", status_code=201)
def create_location(
    body: LocationCreate,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now=Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    def run(c):
        rules.require_customer(c, body.customerId)
        location_id = _add_location(c, cfg, now, body.customerId, body)
        return 201, repo.shape_location(_one(c, "locations", location_id, "Location"))

    return write(conn, run, idem_key=idempotency_key, route=_route(request), body=body.model_dump())


@router.get("/customers/{customer_id}/contacts")
def customer_contacts(
    customer_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
):
    _one(conn, "customers", customer_id, "Customer")
    return page_of(
        conn,
        "SELECT * FROM contacts WHERE customer_id = ? ORDER BY id",
        [customer_id],
        repo.shape_contact,
        *pg,
    )


@router.post("/customers/{customer_id}/contacts", status_code=201)
def add_customer_contact(
    customer_id: int,
    body: ContactIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    now=Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    def run(c):
        rules.require_customer(c, customer_id)
        contact_id = repo.insert_contact(c, now, customer_id, body.model_dump())
        return 201, repo.shape_contact(_one(c, "contacts", contact_id, "Contact"))

    return write(conn, run, idem_key=idempotency_key, route=_route(request), body=body.model_dump())


@router.get("/locations/{location_id}/contacts")
def location_contacts(
    location_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
):
    _one(conn, "locations", location_id, "Location")
    return page_of(
        conn,
        "SELECT * FROM contacts WHERE location_id = ? ORDER BY id",
        [location_id],
        repo.shape_contact,
        *pg,
    )


# ---- bookings ----------------------------------------------------------------------------------


def shape_booking(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "providerId": r["provider_id"],
        "externalId": r["external_id"],
        "source": r["source"],
        "status": r["status"],
        "customerId": r["customer_id"],
        "name": r["name"],
        "address": {
            "street": r["street"],
            "unit": r["unit"],
            "city": r["city"],
            "state": r["state"],
            "zip": r["zip"],
            "country": "USA",
        },
        "contacts": json.loads(r["contacts_json"]),
        "summary": r["summary"],
        "priority": r["priority"],
        "start": r["requested_start"],
        "businessUnitId": r["business_unit_id"],
        "jobTypeId": r["job_type_id"],
        "campaignId": r["campaign_id"],
        "createdOn": r["created_on"],
    }


@router.get("/bookings")
def list_bookings(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    status: str | None = None,
    externalId: str | None = None,
    customerId: int | None = None,
):
    where, params = [], []
    for column, value in (
        ("status", status),
        ("external_id", externalId),
        ("customer_id", customerId),
    ):
        if value is not None:
            where.append(f"{column} = ?")
            params.append(value)
    sql = (
        "SELECT * FROM bookings"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY id"
    )
    return page_of(conn, sql, params, shape_booking, *pg)


@router.get("/bookings/{booking_id}")
def get_booking(booking_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return shape_booking(_one(conn, "bookings", booking_id, "Booking"))


@router.post("/booking-provider/{provider_id}/bookings", status_code=201)
def create_booking(
    provider_id: int,
    body: BookingIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now=Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    """An intake request, not a job: it takes no capacity. A repeated externalId returns the first
    booking."""

    def run(c):
        if not any(p["id"] == provider_id and p["active"] for p in cfg.booking_providers):
            raise ApiError(404, "not_found", f"Booking provider {provider_id} does not exist.")
        if body.externalId:
            existing = c.execute(
                "SELECT * FROM bookings WHERE provider_id = ? AND external_id = ?",
                (provider_id, body.externalId),
            ).fetchone()
            if existing:
                return 200, shape_booking(existing)
        if body.customerId is not None:
            rules.require_bookable_type(cfg, rules.require_customer(c, body.customerId))
        rules.require_in_area(cfg, body.address.zip)
        rules.check_refs(cfg, body.businessUnitId, body.jobTypeId, body.campaignId)
        cur = c.execute(
            """INSERT INTO bookings (provider_id, external_id, source, customer_id, name, street, unit,
                                     city, state, zip, contacts_json, summary, priority, requested_start,
                                     business_unit_id, job_type_id, campaign_id, created_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                provider_id,
                body.externalId,
                body.source,
                body.customerId,
                body.name,
                body.address.street,
                body.address.unit,
                body.address.city,
                body.address.state,
                body.address.zip,
                json.dumps(
                    [k.model_dump(include={"type", "value", "memo"}) for k in body.contacts]
                ),
                body.summary,
                body.priority,
                body.start,
                body.businessUnitId,
                body.jobTypeId,
                body.campaignId,
                iso(now),
            ),
        )
        return 201, shape_booking(_one(c, "bookings", cur.lastrowid, "Booking"))

    return write(conn, run, idem_key=idempotency_key, route=_route(request), body=body.model_dump())


# ---- leads -------------------------------------------------------------------------------------


def shape_lead(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "status": r["status"],
        "kind": r["kind"],
        "customerType": r["customer_type"],
        "customerId": r["customer_id"],
        "name": r["name"],
        "phone": r["phone"],
        "address": {"street": r["street"], "zip": r["zip"]},
        "summary": r["summary"],
        "campaignId": r["campaign_id"],
        "createdOn": r["created_on"],
    }


@router.get("/leads")
def list_leads(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    status: str | None = None,
    customerType: str | None = None,
    kind: str | None = None,
    phone: str | None = None,
):
    where, params = [], []
    for column, value in (("status", status), ("customer_type", customerType), ("kind", kind)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    if phone:
        where.append("phone_key = ?")
        params.append(phone_key(phone))
    sql = (
        "SELECT * FROM leads" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    )
    return page_of(conn, sql, params, shape_lead, *pg)


@router.get("/leads/{lead_id}")
def get_lead(lead_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return shape_lead(_one(conn, "leads", lead_id, "Lead"))


@router.post("/leads", status_code=201)
def create_lead(
    body: LeadIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now=Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    """Replacement and install interest, or a commercial caller. Real ServiceTitan ties a lead to an
    existing customer; this mock also takes a bare name and phone (ASSUMED)."""
    rejected = cfg.rejected_customer_types
    if body.kind not in cfg.lead_kinds:
        raise ApiError(
            400,
            "validation_error",
            "The request was not valid.",
            {"kind": f"must be one of {', '.join(cfg.lead_kinds)}"},
        )
    if (body.kind in rejected) != (body.customerType in rejected):
        raise ApiError(
            400,
            "validation_error",
            "The request was not valid.",
            {
                "kind": "a lead of a customer type the company does not book carries "
                "that type as its kind, and no other lead does"
            },
        )

    def run(c):
        if body.customerId is not None:
            rules.require_customer(c, body.customerId)
        rules.check_refs(cfg, None, None, body.campaignId)
        cur = c.execute(
            """INSERT INTO leads (kind, customer_type, customer_id, name, phone, phone_key, street, zip,
                                  summary, campaign_id, created_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                body.kind,
                body.customerType,
                body.customerId,
                body.name,
                body.phone,
                phone_key(body.phone),
                body.street,
                body.zip,
                body.summary,
                body.campaignId,
                iso(now),
            ),
        )
        return 201, shape_lead(_one(c, "leads", cur.lastrowid, "Lead"))

    return write(conn, run, idem_key=idempotency_key, route=_route(request), body=body.model_dump())
