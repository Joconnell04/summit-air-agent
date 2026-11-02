"""Booking rules, on a tiny three-technician company where every window has one normal slot open
(three technicians, two held for urgent jobs)."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from conftest import (
    NO_COOLING,
    NO_HEAT,
    TOMORROW,
    TUNE_UP,
    build_small_world,
    job_body,
    open_slots,
    window_times,
)


def book(api, ids, window="morning", key=None, who="home", **extra):
    customer_id, location_id = ids[who]
    return api.post("/jpm/jobs", job_body(customer_id, location_id, TOMORROW, window, **extra), key=key)


def test_booking_creates_a_job_and_takes_capacity(small):
    api, ids = small
    assert open_slots(api, TOMORROW, "morning") == 1
    response = book(api, ids)
    assert response.status_code == 201
    job = response.json()
    assert job["jobStatus"] == "Scheduled"
    appointment = api.get("/jpm/appointments", jobId=job["id"]).json()["data"][0]
    assert appointment["status"] == "Scheduled"
    assert appointment["technicianIds"][0] in (7001, 7002, 7003)
    assert open_slots(api, TOMORROW, "morning") == 0


def test_same_idempotency_key_returns_the_first_booking(small):
    api, ids = small
    first = book(api, ids, key="call-1-book")
    second = book(api, ids, key="call-1-book")
    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert api.get("/jpm/jobs").json()["totalCount"] == 1
    assert open_slots(api, TOMORROW, "morning") == 0


def test_idempotency_key_with_a_different_body_is_rejected(small):
    api, ids = small
    book(api, ids, key="k1")
    response = book(api, ids, key="k1", window="afternoon")
    assert response.status_code == 422
    assert response.json()["code"] == "idempotency_key_reused"


def test_same_key_in_parallel_books_once(small):
    api, ids = small
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: book(api, ids, key="same"), range(8)))
    assert {r.status_code for r in results} == {201}
    assert len({r.json()["id"] for r in results}) == 1
    assert api.get("/jpm/jobs").json()["totalCount"] == 1


def test_eight_parallel_bookings_for_one_open_slot_one_wins(small):
    api, ids = small
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda i: book(api, ids, key=f"race-{i}"), range(8)))
    codes = sorted(r.status_code for r in results)
    assert codes == [201] + [409] * 7
    assert {r.json()["code"] for r in results if r.status_code == 409} == {"no_capacity"}
    assert api.get("/jpm/appointments").json()["totalCount"] == 1


def test_eight_parallel_bookings_for_one_named_technician_one_wins(small):
    api, ids = small
    customer_id, location_id = ids["home"]

    def attempt(i):
        body = job_body(customer_id, location_id, TOMORROW, "afternoon", priority="Urgent")
        body["appointments"][0]["technicianIds"] = [7002]
        return api.post("/jpm/jobs", body, key=f"named-{i}")

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sorted(r.status_code for r in results) == [201] + [409] * 7
    assert {r.json()["code"] for r in results if r.status_code == 409} == {"slot_taken"}


def test_database_refuses_a_second_live_appointment_for_one_slot(tmp_path):
    ids = build_small_world(tmp_path / "w.db")
    conn = sqlite3.connect(tmp_path / "w.db")
    insert = """INSERT INTO appointments (job_id, appointment_number, technician_id, business_unit_id,
        window_date, window_id, start, end, arrival_window_start, arrival_window_end, status, created_on,
        modified_on) VALUES (1, 'a', 7001, 11, '2026-11-03', 'morning', 's', 'e', 's', 'e', ?, 'x', 'x')"""
    conn.execute("INSERT INTO jobs (id, job_number, customer_id, location_id, business_unit_id, job_type_id,"
                 " priority, status, created_on, modified_on) VALUES (1, '1', ?, ?, 11, 203, 'Normal', 'Scheduled', 'x', 'x')",
                 (ids["home"][0], ids["home"][1]))
    conn.execute(insert, ("Scheduled",))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("Scheduled",))
    conn.execute(insert, ("Canceled",))  # a cancelled appointment does not hold the slot


def test_cancel_gives_the_capacity_back(small):
    api, ids = small
    job = book(api, ids).json()
    assert open_slots(api, TOMORROW, "morning") == 0
    cancelled = api.request("PUT", f"/jpm/jobs/{job['id']}/cancel", json={"reasonId": 401, "memo": "changed mind"})
    assert cancelled.status_code == 200
    assert cancelled.json()["jobStatus"] == "Canceled"
    assert open_slots(api, TOMORROW, "morning") == 1
    again = api.request("PUT", f"/jpm/jobs/{job['id']}/cancel")
    assert again.status_code == 200
    assert api.get("/jpm/appointments", jobId=job["id"]).json()["data"][0]["status"] == "Canceled"


def test_a_finished_job_cannot_be_cancelled(world):
    api, scenarios = world
    done = scenarios["jobDoneLastWeek"]["jobId"]
    response = api.request("PUT", f"/jpm/jobs/{done}/cancel")
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_state"


def test_reschedule_moves_capacity_from_one_window_to_the_other(small):
    api, ids = small
    job = book(api, ids, window="morning").json()
    appointment = api.get("/jpm/appointments", jobId=job["id"]).json()["data"][0]
    assert (open_slots(api, TOMORROW, "morning"), open_slots(api, TOMORROW, "afternoon")) == (0, 1)
    moved = api.request("PATCH", f"/jpm/appointments/{appointment['id']}/reschedule",
                        json=window_times(TOMORROW, "afternoon"))
    assert moved.status_code == 200
    assert moved.json()["arrivalWindowStart"] == window_times(TOMORROW, "afternoon")["arrivalWindowStart"]
    assert (open_slots(api, TOMORROW, "morning"), open_slots(api, TOMORROW, "afternoon")) == (1, 0)


def test_reschedule_into_a_full_window_leaves_the_booking_alone(small):
    api, ids = small
    book(api, ids, window="afternoon")
    job = book(api, ids, window="morning").json()
    appointment = api.get("/jpm/appointments", jobId=job["id"]).json()["data"][0]
    response = api.request("PATCH", f"/jpm/appointments/{appointment['id']}/reschedule",
                           json=window_times(TOMORROW, "afternoon"))
    assert response.status_code == 409
    unchanged = api.get(f"/jpm/appointments/{appointment['id']}").json()
    assert unchanged["arrivalWindowStart"] == appointment["arrivalWindowStart"]
    assert open_slots(api, TOMORROW, "morning") == 0


def test_normal_jobs_cannot_use_the_urgent_hold_but_urgent_jobs_can(small):
    api, ids = small
    assert book(api, ids).status_code == 201
    assert book(api, ids).status_code == 409
    urgent = book(api, ids, priority="Urgent")
    assert urgent.status_code == 201
    assert urgent.json()["priority"] == "Urgent"
    assert open_slots(api, TOMORROW, "morning", priority="Urgent") == 1


def test_after_hours_is_for_urgent_jobs_and_uses_the_on_call_technician(small):
    api, ids = small
    normal = book(api, ids, window="after_hours")
    assert normal.status_code == 422
    assert normal.json()["code"] == "after_hours_urgent_only"
    urgent = book(api, ids, window="after_hours", priority="Urgent", job_type=NO_HEAT)
    assert urgent.status_code == 201
    appointment = api.get("/jpm/appointments", jobId=urgent.json()["id"]).json()["data"][0]
    assert appointment["technicianIds"] == [7001]


def test_a_job_needs_a_technician_with_the_right_skill(tmp_path):
    from conftest import NOW, TENANT_DIR, Api

    from servicetitan_mock.app import create_app
    ids = build_small_world(tmp_path / "w.db", skills=("maintenance", "heating"))
    api = Api(create_app(tmp_path / "w.db", TENANT_DIR, tmp_path / "w.log", clock=lambda: NOW))
    assert book(api, ids, job_type=NO_COOLING, priority="Urgent").json()["code"] == "no_capacity"
    assert book(api, ids, job_type=NO_HEAT, priority="Urgent").status_code == 201


def test_commercial_customers_cannot_get_appointments(small):
    api, ids = small
    response = book(api, ids, who="commercial")
    assert response.status_code == 422
    assert response.json()["code"] == "commercial_not_supported"
    assert api.get("/jpm/appointments").json()["totalCount"] == 0


def test_a_commercial_caller_can_be_stored_as_a_lead(small):
    api, _ = small
    body = {"kind": "Commercial", "customerType": "Commercial", "name": "Corner Deli", "phone": "(718) 555-0199",
            "summary": "Walk-in cooler is down. Wants a service quote."}
    response = api.post("/crm/leads", body)
    assert response.status_code == 201
    assert api.get("/crm/leads", customerType="Commercial").json()["data"][0]["name"] == "Corner Deli"
    mismatched = api.post("/crm/leads", {**body, "customerType": "Residential"})
    assert mismatched.status_code == 400


def test_out_of_area_zips_are_rejected_on_booking(small):
    api, ids = small
    response = book(api, ids, who="far")
    assert response.status_code == 422
    assert response.json()["code"] == "out_of_area"
    intake = api.post("/crm/booking-provider/501/bookings", {
        "name": "Far Away", "address": {"street": "1 Main St", "zip": "11501"}, "summary": "No heat"})
    assert intake.status_code == 422
    assert intake.json()["code"] == "out_of_area"


def test_an_arrival_window_must_match_a_configured_window(small):
    api, ids = small
    customer_id, location_id = ids["home"]
    body = job_body(customer_id, location_id, TOMORROW, "morning")
    body["appointments"][0]["arrivalWindowEnd"] = "2026-11-03T18:00:00Z"
    response = api.post("/jpm/jobs", body)
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_window"


@pytest.mark.parametrize("day, window", [(date(2026, 11, 7), "evening"), (date(2026, 11, 8), "morning")])
def test_a_window_outside_business_hours_cannot_be_booked(small, day, window):
    """Saturday evening and Sunday morning are never offered by capacity, so booking refuses them too."""
    api, ids = small
    customer_id, location_id = ids["home"]
    response = api.post("/jpm/jobs", job_body(customer_id, location_id, day, window))
    assert response.status_code == 422
    assert response.json()["code"] == "window_not_offered"


def test_a_window_that_has_started_cannot_be_booked(small):
    from conftest import START
    api, ids = small
    customer_id, location_id = ids["home"]
    response = api.post("/jpm/jobs", job_body(customer_id, location_id, START, "morning"))
    assert response.status_code == 422
    assert response.json()["code"] == "slot_in_past"


def test_job_history_records_what_happened(small):
    api, ids = small
    job = book(api, ids).json()
    api.request("PUT", f"/jpm/jobs/{job['id']}/cancel", json={"memo": "no longer needed"})
    events = [e["eventType"] for e in api.get(f"/jpm/jobs/{job['id']}/history").json()["data"]]
    assert events == ["AppointmentScheduled", "JobCreated", "JobCanceled"]


def test_an_intake_booking_takes_no_capacity_and_repeats_return_the_first(small):
    api, _ = small
    body = {"externalId": "call-77", "source": "voice-agent", "name": "Pat Home", "summary": "Furnace noise",
            "address": {"street": "1 Main St", "zip": "11201"}, "contacts": [{"type": "MobilePhone", "value": "718-555-0111"}]}
    first = api.post("/crm/booking-provider/501/bookings", body)
    second = api.post("/crm/booking-provider/501/bookings", body)
    assert first.status_code == 201 and second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert open_slots(api, TOMORROW, "morning") == 1


def test_capacity_lists_every_window_with_the_numbers_behind_it(small):
    api, _ = small
    body = {"startsOnOrAfter": "2026-11-03T05:00:00Z", "endsOnOrBefore": "2026-11-04T05:00:00Z",
            "jobTypeId": TUNE_UP}
    day = api.post("/dispatch/capacity", body).json()["availabilities"][0]
    assert day["date"] == "2026-11-03"
    morning = day["timeslots"][0]
    assert (morning["totalAvailability"], morning["heldForUrgent"], morning["openAvailability"]) == (3, 2, 1)
    after_hours = next(s for s in day["timeslots"] if s["windowId"] == "after_hours")
    assert after_hours["isAvailable"] is False  # normal priority cannot use it
    urgent = api.post("/dispatch/capacity", {**body, "priority": "Urgent"}).json()["availabilities"][0]
    assert next(s for s in urgent["timeslots"] if s["windowId"] == "after_hours")["technicianIds"] == [7001]
