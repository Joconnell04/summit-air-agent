"""Sending a booking to the FSM. Shared by book_appointment and the retry queue, so a booking queued
while the FSM was down is sent the same way, with the same idempotency keys, when it comes back."""

import json
import sqlite3
from datetime import datetime

from . import capacity
from .db import iso, parse_iso
from .fsm import FsmAdapter, FsmRejected
from .tenant import Tenant
from .text import phone_display, street_key, unit_key

# Tries at naming a technician for one window when others book the same window at the same moment.
BOOKING_ATTEMPTS = 4


def same_address(location: dict, address: dict) -> bool:
    loc = location["address"]
    return (
        street_key(loc["street"]) == street_key(address["street"])
        and unit_key(loc["unit"]) == unit_key(address.get("unit", ""))
        and loc["zip"] == address["zip"]
    )


def job_summary(request: dict) -> str:
    """The intake every booking carries (spec S-13), as labelled lines a technician can scan."""
    details, address = request["details"], request["address"]
    place = address["street"] + (f", {address['unit']}" if address.get("unit") else "")
    lines = [
        f"Issue: {details['issue']}",
        f"Since: {details['since']}",
        f"Address: {place}, {address['zip']}",
    ]
    # A number for someone else ("call my daughter at ..."): the job's contact, with the account's
    # own number beside it, so the technician knows whom each one reaches (issue #128).
    if request.get("contact_for"):
        lines.append(f"Contact: {request['contact_for']}, {phone_display(request['phone'])}")
        if request.get("account_phone"):
            lines.append(f"Account phone: {phone_display(request['account_phone'])}")
    else:
        lines.append(f"Contact: {phone_display(request['phone'])}")
    lines += [
        f"System: {details.get('system_type') or 'not known'}",
        f"Tried: {details['tried']}",
        *([f"Caller also noted: {details['also_noted']}"] if details.get("also_noted") else []),
        f"Home: {details['who_home']}",
        f"Approves: {details['approver']}",
        f"On arrival: {details.get('arrival_notes') or 'nothing noted'}",
    ]
    if details.get("inside_temp_f") is not None:
        lines.append(f"Inside: {details['inside_temp_f']:g} F")
    priority = request["priority"]
    reasons = f" ({'; '.join(request['priority_reasons'])})" if request["priority_reasons"] else ""
    lines.append(f"Priority: {priority}{reasons}")
    if request.get("raised_after_wait"):
        lines.append("Note: priority raised by a fact the caller gave after hearing the wait")
    if request.get("contact_name"):
        lines.append(f"Contact: booked by {request['contact_name']}, who stays a contact")
    lines += [f"Note: {note}" for note in request.get("notes", [])]
    lines.append(f"Booked by the phone agent, call {request['call_id']}.")
    return "\n".join(lines)


def check_live(fsm: FsmAdapter, location_id: int) -> None:
    """A job goes to a location only while it is active. One the customer moved out of is
    inactive (tools.record_move), so nothing is booked or moved to the old address."""
    if not fsm.location(location_id).get("active", True):
        raise FsmRejected(409, "location_superseded", f"Location {location_id} is inactive.")


def _superseded(fsm: FsmAdapter, request: dict) -> None:
    """The address is one a customer matched on the call moved out of. A booking queued before
    the move, or a model that books the old address, stops here instead of making a new location
    there."""
    address = request["address"]
    for customer_id in request["scoped_customer_ids"]:
        for location in fsm.locations(customer_id, include_inactive=True):
            if not location.get("active", True) and same_address(location, address):
                raise FsmRejected(
                    409, "location_superseded", f"Location {location['id']} is inactive."
                )


def _customer_and_location(fsm: FsmAdapter, tenant: Tenant, request: dict, key: str):
    """The customer and location to book: an address already on file for a customer matched on the
    call, then the same address anywhere in the FSM, then a new location or a new customer."""
    address = request["address"]
    for customer_id in request["scoped_customer_ids"]:
        for location in fsm.locations(customer_id):
            if same_address(location, address):
                return fsm.customer(customer_id), location["id"]
    _superseded(fsm, request)
    on_file = [
        loc
        for loc in fsm.locations_by_address(address["street"], address["zip"], address.get("unit"))
        if same_address(loc, address)
    ]
    if len({loc["customerId"] for loc in on_file}) == 1:
        return fsm.customer(on_file[0]["customerId"]), on_file[0]["id"]
    fsm_address = {k: address.get(k, "") for k in ("street", "unit", "city", "zip")}
    # A caller on file booking somewhere new: the address goes on its own account with the caller
    # as its contact when they chose that (a parent's house), otherwise under the caller's account.
    if len(request["scoped_customer_ids"]) == 1 and request.get("account") != "own":
        customer = fsm.customer(request["scoped_customer_ids"][0])
        location = fsm.create_location(f"{key}:location", customer["id"], fsm_address)
        return customer, location["id"]
    # When the visit's number reaches someone else, a new customer's own number is the one they
    # called from, and the other number stays the job's contact. With no caller ID it is the only
    # number there is, kept with whom it reaches.
    phone, memo = request["phone"], request.get("contact_name")
    if request.get("contact_for"):
        if request.get("account_phone"):
            phone = request["account_phone"]
        else:
            memo = memo or request["contact_for"]
    created = fsm.create_customer(f"{key}:customer", request["details"]["name"], fsm_address,
                                  phone, memo)
    return created, created["locations"][0]["id"]


def send(fsm: FsmAdapter, tenant: Tenant, request: dict, key: str) -> dict:
    """Create the job. Raises FsmUnavailable (queue and retry) or FsmRejected (the FSM said no)."""
    customer, location_id = _customer_and_location(fsm, tenant, request, key)
    message = tenant.rejected_customer_types.get(customer["type"])
    if message:
        raise FsmRejected(422, "customer_type_not_supported", message)
    job_type = tenant.job_types[request["job_type"]]
    unit = job_type["businessUnitIds"][0]
    start, end = parse_iso(request["window_start"]), parse_iso(request["window_end"])
    # An earlier attempt may have made the job and lost the answer; ServiceTitan would book twice.
    job = fsm.find_job(customer["id"], f"{key}:job")
    for attempt in range(1, BOOKING_ATTEMPTS + 1):
        if job is not None:
            break
        # ServiceTitan's capacity is advisory, so look again right before booking, and apply the
        # company's rules to who may take the window (app/capacity.py).
        technician = capacity.pick_technician(
            tenant, fsm.capacity(start, end, unit, job_type["id"]), start, end, request["priority"]
        )
        if technician is None:
            raise FsmRejected(409, "slot_taken", "No technician may take that window.")
        try:
            job = fsm.create_job(
                f"{key}:job",
                customer["id"],
                location_id,
                unit,
                job_type["id"],
                request["priority"],
                tenant.fsm["campaign_id"],
                job_summary(request),
                start,
                end,
                [technician],
                _tags(tenant, request),
            )
        except FsmRejected as exc:
            # Someone else just took that technician. Look again: another may still be free, and
            # only a window with no one left is a taken slot.
            if exc.code != "slot_taken" or attempt == BOOKING_ATTEMPTS:
                raise
    return {
        "customer_id": customer["id"],
        "job_id": job["id"],
        "appointment_id": job["firstAppointmentId"],
    }


def _tags(tenant: Tenant, request: dict) -> list[int]:
    """The job's label as the FSM tag type the tenant maps it to (fsm.label_tag_ids), if any."""
    tag = (tenant.fsm.get("label_tag_ids") or {}).get(request.get("label", ""))
    return [tag] if tag else []


def mark_booked(conn: sqlite3.Connection, booking_id: int, sent: dict, now: datetime) -> None:
    conn.execute(
        """UPDATE bookings SET status = 'booked', fsm_job_id = ?, fsm_appointment_id = ?,
               customer_id = ?, attempts = attempts + 1, last_error = NULL, updated_at = ?
           WHERE id = ?""",
        (sent["job_id"], sent["appointment_id"], sent["customer_id"], iso(now), booking_id),
    )


def mark_attempt(
    conn: sqlite3.Connection, booking_id: int, status: str, error: str, now: datetime
) -> None:
    conn.execute(
        "UPDATE bookings SET status = ?, attempts = attempts + 1, last_error = ?, updated_at = ? "
        "WHERE id = ?",
        (status, error, iso(now), booking_id),
    )


def request_of(row: sqlite3.Row) -> dict:
    return json.loads(row["request_json"])
