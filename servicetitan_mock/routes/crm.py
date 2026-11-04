"""CRM: customers, locations, contacts, notes, bookings and leads. Field names are ServiceTitan's."""

import json
import sqlite3
from typing import Literal

from fastapi import Depends, Header, Request
from pydantic import BaseModel, Field, model_validator

from .. import repo, rules
from ..config import TenantConfig, iso
from ..db import page_of, write
from ..deps import get_cfg, get_conn, get_now, get_user_id, paging
from ..errors import ApiError
from ..util import phone_key, street_key, unit_key
from . import tenant_router

router = tenant_router("crm")

Priority = Literal["Low", "Normal", "High", "Urgent"]


class Address(BaseModel):
    """street, city, state, zip and country are all required in ServiceTitan; unit is optional."""

    street: str = Field(min_length=1)
    unit: str | None = None
    city: str
    state: str
    zip: str = Field(pattern=r"^\d{5}$")
    country: str


class ContactIn(BaseModel):
    type: Literal["Phone", "MobilePhone", "Email", "Fax"]
    value: str = Field(min_length=3)
    memo: str | None = None

    @model_validator(mode="after")
    def phone_has_ten_digits(self):
        if self.type in ("Phone", "MobilePhone") and len(phone_key(self.value)) != 10:
            raise ValueError("a phone number needs 10 digits")
        return self


class LocationIn(BaseModel):
    name: str
    address: Address
    contacts: list[ContactIn] | None = None


class CustomerIn(BaseModel):
    name: str = Field(min_length=1)
    type: Literal["Residential", "Commercial"] | None = None
    address: Address
    locations: list[LocationIn] = Field(min_length=1)
    contacts: list[ContactIn] | None = None


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    pinToTop: bool | None = None
    addToLocations: bool | None = None


class LocationCreate(LocationIn):
    customerId: int


class LocationUpdate(BaseModel):
    """A partial update, as ServiceTitan's PATCH locations/{id} takes: a location that is no
    longer the customer's is made inactive, which keeps its history and equipment."""

    name: str | None = None
    active: bool | None = None


class BookingIn(BaseModel):
    source: str
    name: str = Field(min_length=1)
    summary: str
    externalId: str
    isFirstTimeClient: bool
    address: Address | None = None
    contacts: list[ContactIn] | None = None
    customerType: Literal["Residential", "Commercial"] | None = None
    start: str | None = None
    priority: Priority | None = None
    businessUnitId: int | None = None
    jobTypeId: int | None = None
    campaignId: int | None = None


class LeadIn(BaseModel):
    """Who the lead is about is the lead* fields, or customerId. What it is about is callReasonId."""

    customerId: int | None = None
    locationId: int | None = None
    campaignId: int
    businessUnitId: int | None = None
    jobTypeId: int | None = None
    summary: str = Field(min_length=1)
    priority: Priority | None = None
    callReasonId: int | None = None
    leadCustomerName: str | None = None
    leadPhone: str | None = None
    leadEmail: str | None = None
    leadStreet: str | None = None
    leadUnit: str | None = None
    leadCity: str | None = None
    leadState: str | None = None
    leadZip: str | None = None
    leadCountry: str | None = None


def _route(request: Request) -> str:
    return f"{request.method} {request.url.path}"


def _one(conn: sqlite3.Connection, table: str, row_id: int, what: str) -> sqlite3.Row:
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if not row:
        raise ApiError(404, "not_found", f"{what} {row_id} does not exist.")
    return row


# ---- customers and locations -------------------------------------------------------------------


def _add_location(conn, cfg, now, customer_id: int, loc: LocationIn) -> int:
    address = {k: v or "" for k, v in loc.address.model_dump().items()}
    location_id = repo.insert_location(conn, cfg, now, customer_id, loc.name, address)
    for contact in loc.contacts or []:
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
    active: bool | None = None,
):
    """phone matches any contact on the customer or its locations, in any format.
    street, unit and zip match any of the customer's locations. Contacts are not in the list: read
    customers/{id}/contacts."""
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


@router.post("/customers")
def create_customer(
    body: CustomerIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now=Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    def run(c):
        customer_id = repo.insert_customer(c, now, body.name, body.type or "Residential")
        for contact in body.contacts or []:
            repo.insert_contact(c, now, customer_id, contact.model_dump())
        for loc in body.locations:
            _add_location(c, cfg, now, customer_id, loc)
        result = repo.shape_customer(c, _one(c, "customers", customer_id, "Customer"))
        # The create response carries the contacts and locations that were just made.
        result["contacts"] = [
            repo.shape_contact(k)
            for k in c.execute(
                "SELECT * FROM contacts WHERE customer_id = ? AND location_id IS NULL ORDER BY id",
                (customer_id,),
            )
        ]
        result["locations"] = [
            {
                **repo.shape_location(r),
                "contacts": [
                    repo.shape_contact(k)
                    for k in c.execute(
                        "SELECT * FROM contacts WHERE location_id = ? ORDER BY id", (r["id"],)
                    )
                ],
            }
            for r in c.execute(
                "SELECT * FROM locations WHERE customer_id = ? ORDER BY id", (customer_id,)
            )
        ]
        return 200, result

    return write(
        conn,
        run,
        idem_key=idempotency_key,
        route=_route(request),
        body=body.model_dump(),
    )


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
    active: Literal["True", "Any", "False"] = "True",
):
    """Address lookup: street is compared after normalizing (St and Street match). Leave unit out
    to get every unit in the building. There is no phone filter on locations. Only active
    locations unless active is Any or False, as in ServiceTitan."""
    sub, params = _location_filter(street, unit, zip)
    where = [sub]
    if customerId is not None:
        where.append("l.customer_id = ?")
        params.append(customerId)
    if active != "Any":
        where.append("l.active = ?")
        params.append(int(active == "True"))
    sql = f"SELECT l.* FROM locations l WHERE {' AND '.join(where)} ORDER BY l.id"
    return page_of(conn, sql, params, repo.shape_location, *pg)


@router.get("/locations/{location_id}")
def get_location(location_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return repo.shape_location(_one(conn, "locations", location_id, "Location"))


@router.post("/locations")
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
        return 200, repo.shape_location(_one(c, "locations", location_id, "Location"))

    return write(
        conn,
        run,
        idem_key=idempotency_key,
        route=_route(request),
        body=body.model_dump(),
    )


@router.patch("/locations/{location_id}")
def update_location(
    location_id: int,
    body: LocationUpdate,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    now=Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    def run(c):
        _one(c, "locations", location_id, "Location")
        stamp = iso(now)
        if body.name is not None:
            c.execute(
                "UPDATE locations SET name = ?, modified_on = ? WHERE id = ?",
                (body.name, stamp, location_id),
            )
        if body.active is not None:
            c.execute(
                "UPDATE locations SET active = ?, modified_on = ? WHERE id = ?",
                (int(body.active), stamp, location_id),
            )
        return 200, repo.shape_location(_one(c, "locations", location_id, "Location"))

    return write(
        conn,
        run,
        idem_key=idempotency_key,
        route=_route(request),
        body={"location": location_id, **body.model_dump()},
    )


@router.get("/customers/{customer_id}/contacts")
def customer_contacts(
    customer_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
):
    """The customer's contacts, every one of them (also those on its locations). This is the only
    place a customer's phone numbers come from."""
    _one(conn, "customers", customer_id, "Customer")
    return page_of(
        conn,
        "SELECT * FROM contacts WHERE customer_id = ? ORDER BY id",
        [customer_id],
        repo.shape_contact,
        *pg,
    )


@router.post("/customers/{customer_id}/contacts")
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
        return 200, repo.shape_contact(_one(c, "contacts", contact_id, "Contact"))

    return write(
        conn,
        run,
        idem_key=idempotency_key,
        route=_route(request),
        body=body.model_dump(),
    )


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


# ---- customer notes ----------------------------------------------------------------------------


@router.get("/customers/{customer_id}/notes")
def customer_notes(
    customer_id: int,
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
):
    """The customer's own notes, oldest first. Notes about a job are on the job (jobs/{id}/notes)."""
    _one(conn, "customers", customer_id, "Customer")
    return page_of(
        conn,
        "SELECT * FROM notes WHERE customer_id = ? AND job_id IS NULL ORDER BY id",
        [customer_id],
        repo.shape_note,
        *pg,
    )


@router.post("/customers/{customer_id}/notes")
def add_customer_note(
    customer_id: int,
    body: NoteIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    now=Depends(get_now),
    user_id: int = Depends(get_user_id),
    idempotency_key: str | None = Header(None),
):
    """Notes are append-only here: this mock has no endpoint that edits or removes one (the real
    API can delete a customer note, which the agent never does)."""

    def run(c):
        rules.require_customer(c, customer_id)
        note_id = repo.insert_note(
            c, now, customer_id, body.text, user_id, pin_to_top=bool(body.pinToTop)
        )
        return 200, repo.shape_note(_one(c, "notes", note_id, "Note"))

    return write(
        conn,
        run,
        idem_key=idempotency_key,
        route=_route(request),
        body=body.model_dump(),
    )


# ---- bookings ----------------------------------------------------------------------------------


def shape_booking(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "bookingProviderId": r["provider_id"],
        "externalId": r["external_id"],
        "source": r["source"],
        "status": r["status"],
        "name": r["name"],
        "address": {
            "street": r["street"],
            "unit": r["unit"],
            "city": r["city"],
            "state": r["state"],
            "zip": r["zip"],
            "country": "USA",
        },
        "customerType": r["customer_type"],
        "summary": r["summary"],
        "priority": r["priority"],
        "start": r["requested_start"] or r["created_on"],
        "businessUnitId": r["business_unit_id"],
        "jobTypeId": r["job_type_id"],
        "campaignId": r["campaign_id"],
        "isFirstTimeClient": bool(r["is_first_time_client"]),
        "jobId": r["job_id"],
        "dismissingReasonId": None,
        "createdOn": r["created_on"],
        "modifiedOn": r["modified_on"] or r["created_on"],
    }


@router.get("/bookings")
def list_bookings(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    externalId: str | None = None,
    ids: str | None = None,
    createdOnOrAfter: str | None = None,
):
    """The filters ServiceTitan has: external id, ids and dates. There is no status or customer
    filter."""
    where, params = [], []
    if externalId is not None:
        where.append("external_id = ?")
        params.append(externalId)
    if ids:
        wanted = [int(v) for v in ids.split(",") if v.strip().isdigit()]
        where.append(f"id IN ({','.join('?' * len(wanted))})")
        params += wanted
    if createdOnOrAfter:
        where.append("created_on >= ?")
        params.append(createdOnOrAfter)
    sql = (
        "SELECT * FROM bookings"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY id"
    )
    return page_of(conn, sql, params, shape_booking, *pg)


@router.get("/bookings/{booking_id}")
def get_booking(booking_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return shape_booking(_one(conn, "bookings", booking_id, "Booking"))


@router.post("/booking-provider/{provider_id}/bookings")
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
    booking (ASSUMED; ServiceTitan's rule for a repeated externalId is not documented)."""

    def run(c):
        if not any(p["id"] == provider_id and p["active"] for p in cfg.booking_providers):
            raise ApiError(404, "not_found", f"Booking provider {provider_id} does not exist.")
        existing = c.execute(
            "SELECT * FROM bookings WHERE provider_id = ? AND external_id = ?",
            (provider_id, body.externalId),
        ).fetchone()
        if existing:
            return 200, shape_booking(existing)
        rules.check_refs(cfg, body.businessUnitId, body.jobTypeId, body.campaignId)
        address = {k: v or "" for k, v in body.address.model_dump().items()} if body.address else {}
        cur = c.execute(
            """INSERT INTO bookings (provider_id, external_id, source, customer_type,
                                     is_first_time_client, name, street, unit, city, state, zip,
                                     contacts_json, summary, priority, requested_start,
                                     business_unit_id, job_type_id, campaign_id, created_on,
                                     modified_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                provider_id,
                body.externalId,
                body.source,
                body.customerType,
                int(body.isFirstTimeClient),
                body.name,
                address.get("street", ""),
                address.get("unit", ""),
                address.get("city", ""),
                address.get("state", ""),
                address.get("zip", ""),
                json.dumps([k.model_dump() for k in body.contacts or []]),
                body.summary,
                body.priority or "Normal",
                body.start,
                body.businessUnitId,
                body.jobTypeId,
                body.campaignId,
                iso(now),
                iso(now),
            ),
        )
        return 200, shape_booking(_one(c, "bookings", cur.lastrowid, "Booking"))

    return write(
        conn,
        run,
        idem_key=idempotency_key,
        route=_route(request),
        body=body.model_dump(),
    )


# ---- leads -------------------------------------------------------------------------------------


def shape_lead(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "status": r["status"],
        "priority": r["priority"],
        "customerId": r["customer_id"],
        "locationId": r["location_id"],
        "businessUnitId": r["business_unit_id"],
        "jobTypeId": r["job_type_id"],
        "campaignId": r["campaign_id"],
        "callReasonId": r["call_reason_id"],
        "summary": r["summary"],
        "leadCustomerName": r["lead_customer_name"],
        "leadPhone": r["lead_phone"],
        "leadStreet": r["lead_street"],
        "leadUnit": r["lead_unit"],
        "leadCity": r["lead_city"],
        "leadState": r["lead_state"],
        "leadZip": r["lead_zip"],
        "createdById": 4001,
        "createdOn": r["created_on"],
        "modifiedOn": r["modified_on"],
    }


@router.get("/leads")
def list_leads(
    conn: sqlite3.Connection = Depends(get_conn),
    pg: tuple[int, int] = Depends(paging),
    status: str | None = None,
    customerId: int | None = None,
    leadPhone: str | None = None,
    leadCustomerName: str | None = None,
    createdOnOrAfter: str | None = None,
):
    where, params = [], []
    for column, value in (
        ("status", status),
        ("customer_id", customerId),
        ("lead_customer_name", leadCustomerName),
    ):
        if value is not None:
            where.append(f"{column} = ?")
            params.append(value)
    if leadPhone:
        where.append("phone_key = ?")
        params.append(phone_key(leadPhone))
    if createdOnOrAfter:
        where.append("created_on >= ?")
        params.append(createdOnOrAfter)
    sql = (
        "SELECT * FROM leads" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    )
    return page_of(conn, sql, params, shape_lead, *pg)


@router.get("/leads/{lead_id}")
def get_lead(lead_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    return shape_lead(_one(conn, "leads", lead_id, "Lead"))


@router.post("/leads")
def create_lead(
    body: LeadIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    cfg: TenantConfig = Depends(get_cfg),
    now=Depends(get_now),
    idempotency_key: str | None = Header(None),
):
    """Replacement and install interest, or a commercial caller. A lead may stand on its own (a name
    and phone) or point at a customer."""

    def run(c):
        if body.customerId is not None:
            rules.require_customer(c, body.customerId)
        rules.check_refs(cfg, body.businessUnitId, body.jobTypeId, body.campaignId)
        if body.callReasonId is not None and not any(
            r["id"] == body.callReasonId and r["active"] for r in cfg.call_reasons
        ):
            raise ApiError(
                422,
                "unknown_reference",
                f"Call reason {body.callReasonId} is not available.",
                {"callReasonId": "unknown or inactive"},
            )
        cur = c.execute(
            """INSERT INTO leads (priority, customer_id, location_id, business_unit_id, job_type_id,
                                  call_reason_id, lead_customer_name, lead_phone, phone_key,
                                  lead_street, lead_unit, lead_city, lead_state, lead_zip, summary,
                                  campaign_id, created_on, modified_on)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                body.priority or "Normal",
                body.customerId,
                body.locationId,
                body.businessUnitId,
                body.jobTypeId,
                body.callReasonId,
                body.leadCustomerName,
                body.leadPhone,
                phone_key(body.leadPhone or ""),
                body.leadStreet,
                body.leadUnit,
                body.leadCity,
                body.leadState,
                body.leadZip,
                body.summary,
                body.campaignId,
                iso(now),
                iso(now),
            ),
        )
        return 200, shape_lead(_one(c, "leads", cur.lastrowid, "Lead"))

    return write(
        conn,
        run,
        idem_key=idempotency_key,
        route=_route(request),
        body=body.model_dump(),
    )
