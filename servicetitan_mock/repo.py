"""Row inserts and the JSON shapes for CRM records. Shared by the routes and the seed script."""
import sqlite3
from datetime import datetime

from .config import TenantConfig, iso
from .util import phone_key, street_key, unit_key


def insert_customer(conn: sqlite3.Connection, now: datetime, name: str, kind: str = "Residential") -> int:
    stamp = iso(now)
    cur = conn.execute(
        "INSERT INTO customers (name, type, created_on, modified_on) VALUES (?, ?, ?, ?)",
        (name, kind, stamp, stamp),
    )
    return cur.lastrowid


def insert_location(
    conn: sqlite3.Connection, cfg: TenantConfig, now: datetime, customer_id: int, name: str, address: dict
) -> int:
    zone = cfg.zone_by_zip.get(address["zip"])
    stamp = iso(now)
    unit = address.get("unit", "")
    cur = conn.execute(
        """INSERT INTO locations (customer_id, name, street, street_key, unit, unit_key, city, state,
                                  zip, county, zone_id, created_on, modified_on)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            customer_id, name, address["street"], street_key(address["street"]), unit, unit_key(unit),
            address.get("city", ""), address.get("state", ""), address["zip"],
            zone["county"] if zone else address.get("county"), zone["id"] if zone else None, stamp, stamp,
        ),
    )
    return cur.lastrowid


def insert_contact(
    conn: sqlite3.Connection, now: datetime, customer_id: int, contact: dict, location_id: int | None = None
) -> int:
    value = contact["value"]
    cur = conn.execute(
        """INSERT INTO contacts (customer_id, location_id, type, value, phone_key, role, name, memo, created_on)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            customer_id, location_id, contact["type"], value,
            phone_key(value) if contact["type"] != "Email" else "",
            contact.get("role"), contact.get("name"), contact.get("memo", ""), iso(now),
        ),
    )
    return cur.lastrowid


def shape_address(r: sqlite3.Row) -> dict:
    return {
        "street": r["street"], "unit": r["unit"], "city": r["city"], "state": r["state"],
        "zip": r["zip"], "country": "USA", "county": r["county"],
    }


def shape_location(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "customerId": r["customer_id"], "name": r["name"], "address": shape_address(r),
        "zoneId": r["zone_id"], "active": bool(r["active"]),
        "createdOn": r["created_on"], "modifiedOn": r["modified_on"],
    }


def shape_contact(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "customerId": r["customer_id"], "locationId": r["location_id"],
        "type": r["type"], "value": r["value"], "role": r["role"], "name": r["name"], "memo": r["memo"],
    }


def shape_customer(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    first = conn.execute(
        "SELECT * FROM locations WHERE customer_id = ? ORDER BY id LIMIT 1", (r["id"],)
    ).fetchone()
    contacts = conn.execute("SELECT * FROM contacts WHERE customer_id = ? ORDER BY id", (r["id"],))
    return {
        "id": r["id"], "name": r["name"], "type": r["type"], "active": bool(r["active"]),
        "address": shape_address(first) if first else None,
        "contacts": [shape_contact(c) for c in contacts],
        "balance": 0, "doNotMail": False, "doNotService": False,
        "createdOn": r["created_on"], "modifiedOn": r["modified_on"],
    }
