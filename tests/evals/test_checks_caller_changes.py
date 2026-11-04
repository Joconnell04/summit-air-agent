"""The checks that read ServiceTitan after the call (issue #128): moved, identity, job_contact and
booked_under, on a scratch mock database."""

import json
import sqlite3

import pytest

from evals.checks import CHECKS, Evidence, safety_config
from servicetitan_mock.db import create_schema

SAFETY = safety_config()
T = "2026-11-02T14:00:00Z"


@pytest.fixture
def st_db(tmp_path):
    path = tmp_path / "st.db"
    create_schema(path)
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("INSERT INTO customers (id, name, type, created_on, modified_on) "
                     "VALUES (1, 'Maria Delgado', 'Residential', ?, ?)", (T, T))
        for loc_id, street, zip_code, active in ((10, "620 Bedford Ave", "11235", 0),
                                                 (11, "380 Washington Ave", "11238", 1)):
            conn.execute(
                "INSERT INTO locations (id, customer_id, name, street, street_key, zip, active, "
                "created_on, modified_on) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)",
                (loc_id, street, street, street.lower(), zip_code, active, T, T),
            )
        conn.execute("INSERT INTO contacts (customer_id, type, value, phone_key, created_on) "
                     "VALUES (1, 'MobilePhone', '7185550100', '7185550100', ?)", (T,))
    conn.close()
    return path


def job(st_db, job_id, location_id, status="Scheduled", summary=""):
    conn = sqlite3.connect(st_db)
    with conn:
        conn.execute(
            "INSERT INTO jobs (id, job_number, customer_id, location_id, business_unit_id, "
            "job_type_id, priority, summary, status, created_on, modified_on) "
            "VALUES (?, ?, 1, ?, 11, 201, 'Normal', ?, ?, ?, ?)",
            (job_id, str(job_id), location_id, summary, status, T, T),
        )
    conn.close()


def move(in_area=True, zip_code="11238"):
    return {"customer_id": 1, "old_location_ids": json.dumps([10]), "street": "380 Washington Ave",
            "zip": zip_code, "in_area": int(in_area)}


def trace(**extra):
    base = {"call": {}, "events": [], "bookings": [], "flags": [], "callbacks": [],
            "out_of_area": []}
    return {**base, **extra}


def run(check, arg, t, st_db):
    return CHECKS[check].run(arg, Evidence(t, SAFETY, st_db))


def test_moved_in_area_passes_when_the_job_is_at_the_new_location(st_db):
    job(st_db, 100, 11)
    t = trace(address_moves=[move()])
    assert run("moved", {"in_area": True, "zip": "11238", "jobs": 1}, t, st_db) == []


def test_moved_in_area_fails_on_a_job_left_at_the_old_location(st_db):
    job(st_db, 100, 10)
    failures = run("moved", {"in_area": True, "jobs": 1}, trace(address_moves=[move()]), st_db)
    assert any("inactive location" in f for f in failures)


def test_moved_fails_without_a_move_or_without_servicetitan(st_db):
    assert run("moved", {"in_area": True}, trace(), st_db) == ["no move was recorded"]
    assert "in-process" in run("moved", {"in_area": True}, trace(address_moves=[move()]), None)[0]


def test_identity_reads_the_check_and_the_note(st_db):
    job(st_db, 100, 11)
    t = trace(identity_checks=[{"status": "verified"}],
              bookings=[{"kind": "cancel", "status": "done", "fsm_job_id": 100}])
    assert run("identity", "verified", t, st_db) == ["no unknown-number note on job 100"]
    assert run("identity", "none", trace(), st_db) == []
    assert run("identity", "mismatch", t, st_db)


def test_job_contact_and_booked_under(st_db):
    job(st_db, 100, 11, summary="Contact: my daughter Ana, (718) 555-0142\n"
                                "Account phone: (718) 555-0100")
    t = trace(bookings=[{"kind": "book", "status": "booked", "fsm_job_id": 100}])
    assert run("job_contact", {"phone": "7185550142", "for": "Ana"}, t, st_db) == []
    assert run("job_contact", {"phone": "7185550199"}, t, st_db)
    assert run("booked_under", "Maria Delgado", t, st_db) == []
    assert run("booked_under", "Hector Ruiz", t, st_db)
