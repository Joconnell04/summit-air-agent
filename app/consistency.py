"""A check in code, run when a call ends: every visit the call booked matches the read-back the
caller confirmed (street, unit and phone). A mismatch is a big mistake, rare but costly (a
technician at the wrong door), so it becomes an Urgent person flag on the owner view and the
on-call alert, and the failure grader is told (Jackson, 2026-11-06)."""

import json
import re
import sqlite3

from .text import street_key, unit_key

FLAG_PREFIX = "Booking doesn't match the read-back"


def booking_mismatches(conn: sqlite3.Connection, call: sqlite3.Row) -> list[str]:
    """What differs between each standing booking on the call and the confirmed read-back."""
    if not call["readback_json"] or not call["readback_confirmed_at"]:
        return []
    said = json.loads(call["readback_json"])
    found = []
    rows = conn.execute(
        "SELECT request_json FROM bookings WHERE call_id = ? AND kind = 'book' "
        "AND status IN ('booked', 'queued')",
        (call["id"],),
    ).fetchall()
    for row in rows:
        booked = json.loads(row["request_json"])
        address = booked.get("address") or {}
        if street_key(address.get("street", "")) != street_key(said.get("street", "")):
            found.append(f"booked {address.get('street')}, read back {said.get('street')}")
        if unit_key(address.get("unit", "")) != unit_key(said.get("unit", "")):
            found.append(f"booked unit {address.get('unit') or 'none'}, read back unit "
                         f"{said.get('unit') or 'none'}")
        if _digits(booked.get("phone", ""))[-10:] != _digits(said.get("phone", ""))[-10:]:
            found.append(f"booked phone ending {_digits(booked.get('phone', ''))[-4:]}, read back "
                         f"ending {_digits(said.get('phone', ''))[-4:]}")
    return found


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")
