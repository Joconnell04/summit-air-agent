"""Row inserts and the JSON shapes for CRM records. Shared by the routes and the seed script."""

import sqlite3
from datetime import datetime

from .config import TenantConfig, iso
from .util import phone_key, street_key, unit_key

# User ids that notes carry in createdById. The agent's integration user is the one the mock's
# credentials belong to (auth.user_id in the tenant config reads the same number).
STAFF_USER_ID = 4001
AGENT_USER_ID = 9001


def insert_customer(
    conn: sqlite3.Connection, now: datetime, name: str, kind: str = "Residential"
) -> int:
    stamp = iso(now)
    cur = conn.execute(
        "INSERT INTO customers (name, type, created_on, modified_on) VALUES (?, ?, ?, ?)",
        (name, kind, stamp, stamp),
    )
    return cur.lastrowid


def insert_location(
    conn: sqlite3.Connection,
    cfg: TenantConfig,
    now: datetime,
    customer_id: int,
    name: str,
    address: dict,
) -> int:
    zone = cfg.zone_by_zip.get(address["zip"])
    stamp = iso(now)
    unit = address.get("unit", "")
    cur = conn.execute(
        """INSERT INTO locations (customer_id, name, street, street_key, unit, unit_key, city, state,
                                  zip, zone_id, created_on, modified_on)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            customer_id,
            name,
            address["street"],
            street_key(address["street"]),
            unit,
            unit_key(unit),
            address.get("city", ""),
            address.get("state", ""),
            address["zip"],
            zone["id"] if zone else None,
            stamp,
            stamp,
        ),
    )
    return cur.lastrowid


def insert_contact(
    conn: sqlite3.Connection,
    now: datetime,
    customer_id: int,
    contact: dict,
    location_id: int | None = None,
) -> int:
    value = contact["value"]
    cur = conn.execute(
        """INSERT INTO contacts (customer_id, location_id, type, value, phone_key, memo, created_on)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            customer_id,
            location_id,
            contact["type"],
            value,
            phone_key(value) if contact["type"] not in ("Email", "Fax") else "",
            contact.get("memo") or "",
            iso(now),
        ),
    )
    return cur.lastrowid


def insert_note(
    conn: sqlite3.Connection,
    now: datetime,
    customer_id: int,
    text: str,
    created_by_id: int = STAFF_USER_ID,
    job_id: int | None = None,
    pin_to_top: bool = False,
) -> int:
    cur = conn.execute(
        """INSERT INTO notes (customer_id, job_id, text, is_pinned, created_by_id, created_on)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (customer_id, job_id, text, int(pin_to_top), created_by_id, iso(now)),
    )
    return cur.lastrowid


def shape_note(r: sqlite3.Row, with_id: bool = True) -> dict:
    """A customer note has an id; a job note, in ServiceTitan's spec, has none."""
    note = {
        "text": r["text"],
        "isPinned": bool(r["is_pinned"]),
        "createdById": r["created_by_id"],
        "createdOn": r["created_on"],
        "modifiedOn": r["created_on"],
    }
    return {"id": r["id"], **note} if with_id else note


def shape_address(r: sqlite3.Row) -> dict:
    return {
        "street": r["street"],
        "unit": r["unit"],
        "city": r["city"],
        "state": r["state"],
        "zip": r["zip"],
        "country": "USA",
    }


def shape_location(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "customerId": r["customer_id"],
        "name": r["name"],
        "address": shape_address(r),
        "zoneId": r["zone_id"],
        "active": bool(r["active"]),
        "createdOn": r["created_on"],
        "modifiedOn": r["modified_on"],
    }


def shape_contact(r: sqlite3.Row) -> dict:
    """A contact is a type, a value and a memo; ServiceTitan has no role or name on it."""
    return {
        "id": r["id"],
        "type": r["type"],
        "value": r["value"],
        "memo": r["memo"],
        "modifiedOn": r["created_on"],
        "createdOn": r["created_on"],
        "phoneSettings": {
            "phoneNumber": r["phone_key"] if r["type"] in ("Phone", "MobilePhone") else "",
            "doNotText": False,
        },
    }


def shape_customer(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    """The customer record. Contacts are not inline: they come from customers/{id}/contacts."""
    # The address is the first active location's, so a customer who moved shows the new one.
    first = conn.execute(
        "SELECT * FROM locations WHERE customer_id = ? ORDER BY active DESC, id LIMIT 1",
        (r["id"],),
    ).fetchone()
    return {
        "id": r["id"],
        "name": r["name"],
        "type": r["type"],
        "active": bool(r["active"]),
        "address": shape_address(first) if first else None,
        "balance": 0,
        "taxExempt": False,
        "tagTypeIds": [],
        "doNotMail": False,
        "doNotService": False,
        "nationalAccount": False,
        "createdOn": r["created_on"],
        "createdById": STAFF_USER_ID,
        "modifiedOn": r["modified_on"],
        "customFields": [],
        "externalData": [],
    }
