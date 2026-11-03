"""Booking rules, on a tiny three-technician company where every window has three technicians free.
The mock sells technician-windows (DECISIONS 003); the company's own rules (urgent holds, after-hours,
customer types, service area) are not ServiceTitan's and are tested in the tool server."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from conftest import (
    NO_COOLING,
    NO_HEAT,
    NOW,
    START,
    TENANT_DIR,
    TOMORROW,
    TUNE_UP,
    Api,
    build_small_world,
    capacity_frame,
    job_body,
    open_slots,
    window_times,
)


def book(api, ids, window="morning", key=None, who="home", **extra):
    customer_id, location_id = ids[who]
    return api.post(
        "/jpm/jobs", job_body(customer_id, location_id, TOMORROW, window, **extra), key=key
    )


def appointment_of(api, job_id):
    return api.get("/jpm/appointments", jobId=job_id).json()["data"][0]


def technician_of(api, appointment_id):
    assigned = api.get("/dispatch/appointment-assignments", appointmentIds=appointment_id)
    return assigned.json()["data"][0]["technicianId"]


def test_booking_creates_a_job_and_takes_capacity(small):
    api, ids = small
    assert open_slots(api, TOMORROW, "morning") == 3
    response = book(api, ids)
    assert response.status_code == 200, "ServiceTitan answers a create with 200"
    job = response.json()
    assert job["jobStatus"] == "Scheduled"
    appointment = appointment_of(api, job["id"])
    assert appointment["status"] == "Scheduled"
    assert "technicianIds" not in appointment, "assignments are read from dispatch"
    assert technician_of(api, appointment["id"]) in (7001, 7002, 7003)
    assert open_slots(api, TOMORROW, "morning") == 2


def test_a_job_needs_the_appointment_start_and_end_priority_and_a_campaign(small):
    api, ids = small
    customer_id, location_id = ids["home"]
    body = job_body(customer_id, location_id, TOMORROW, "morning")
    for field in ("start", "end"):
        without = {**body, "appointments": [{**body["appointments"][0]}]}
        del without["appointments"][0][field]
        assert api.post("/jpm/jobs", without).status_code == 400
    for field in ("priority", "campaignId"):
        assert (
            api.post("/jpm/jobs", {k: v for k, v in body.items() if k != field}).status_code == 400
        )
    # The arrival window is optional: start and end name the window when it is left out.
    times = window_times(TOMORROW, "morning")
    only = {**body, "appointments": [{"start": times["start"], "end": times["end"]}]}
    assert api.post("/jpm/jobs", only).status_code == 200


def test_same_idempotency_key_returns_the_first_booking(small):
    """Not in the real API (README): the app finds a job again by the reference in its summary."""
    api, ids = small
    first = book(api, ids, key="call-1-book")
    second = book(api, ids, key="call-1-book")
    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert api.get("/jpm/jobs").json()["totalCount"] == 1
    assert open_slots(api, TOMORROW, "morning") == 2


def test_idempotency_key_with_a_different_body_is_rejected(small):
    api, ids = small
    book(api, ids, key="k1")
    response = book(api, ids, key="k1", window="afternoon")
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "idempotency_key_reused"


def test_a_failed_request_is_not_stored_under_its_key_so_a_retry_can_succeed(small):
    api, ids = small
    holders = [book(api, ids).json() for _ in range(3)]  # all three technicians are booked
    failed = book(api, ids, key="retry-me")
    assert failed.status_code == 409
    assert failed.json()["data"]["code"] == "no_capacity"
    conn = sqlite3.connect(api.app.state.db_path)
    assert conn.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0] == 0
    conn.close()

    api.request("PUT", f"/jpm/jobs/{holders[0]['id']}/cancel", json={"reasonId": 401, "memo": "x"})
    retried = book(api, ids, key="retry-me")
    assert retried.status_code == 200
    assert "Idempotent-Replayed" not in retried.headers
    assert book(api, ids, key="retry-me").headers["Idempotent-Replayed"] == "true"


def test_same_key_in_parallel_books_once(small):
    api, ids = small
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: book(api, ids, key="same"), range(8)))
    assert {r.status_code for r in results} == {200}
    assert len({r.json()["id"] for r in results}) == 1
    assert api.get("/jpm/jobs").json()["totalCount"] == 1


def test_eight_parallel_bookings_for_three_open_slots_three_win(small):
    api, ids = small
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda i: book(api, ids, key=f"race-{i}"), range(8)))
    codes = sorted(r.status_code for r in results)
    assert codes == [200] * 3 + [409] * 5
    assert {r.json()["data"]["code"] for r in results if r.status_code == 409} == {"no_capacity"}
    assert api.get("/jpm/appointments").json()["totalCount"] == 3


def test_eight_parallel_bookings_for_one_named_technician_one_wins(small):
    api, ids = small
    customer_id, location_id = ids["home"]

    def attempt(i):
        body = job_body(customer_id, location_id, TOMORROW, "afternoon", priority="Urgent")
        body["appointments"][0]["technicianIds"] = [7002]
        return api.post("/jpm/jobs", body, key=f"named-{i}")

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sorted(r.status_code for r in results) == [200] + [409] * 7
    assert {r.json()["data"]["code"] for r in results if r.status_code == 409} == {"slot_taken"}


def test_database_refuses_a_second_live_appointment_for_one_slot(tmp_path):
    ids = build_small_world(tmp_path / "w.db")
    conn = sqlite3.connect(tmp_path / "w.db")
    insert = """INSERT INTO appointments (job_id, appointment_number, technician_id, business_unit_id,
        window_date, window_id, start, end, arrival_window_start, arrival_window_end, status, created_on,
        modified_on) VALUES (1, 'a', 7001, 11, '2026-11-03', 'morning', 's', 'e', 's', 'e', ?, 'x', 'x')"""
    conn.execute(
        "INSERT INTO jobs (id, job_number, customer_id, location_id, business_unit_id, job_type_id,"
        " priority, status, created_on, modified_on) VALUES (1, '1', ?, ?, 11, 203, 'Normal', 'Scheduled', 'x', 'x')",
        (ids["home"][0], ids["home"][1]),
    )
    conn.execute(insert, ("Scheduled",))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("Scheduled",))
    conn.execute(insert, ("Canceled",))  # a cancelled appointment does not hold the slot


def test_cancel_gives_the_capacity_back(small):
    api, ids = small
    job = book(api, ids).json()
    assert open_slots(api, TOMORROW, "morning") == 2
    cancelled = api.request(
        "PUT", f"/jpm/jobs/{job['id']}/cancel", json={"reasonId": 401, "memo": "changed mind"}
    )
    assert cancelled.status_code == 200
    assert cancelled.json() == {}, "the real cancel answers with no body"
    assert api.get(f"/jpm/jobs/{job['id']}").json()["jobStatus"] == "Canceled"
    assert open_slots(api, TOMORROW, "morning") == 3
    again = api.request("PUT", f"/jpm/jobs/{job['id']}/cancel", json={"reasonId": 401, "memo": "x"})
    assert again.status_code == 200
    assert appointment_of(api, job["id"])["status"] == "Canceled"
    assert api.request("PUT", f"/jpm/jobs/{job['id']}/cancel").status_code == 400, (
        "reasonId and memo are both required"
    )


def test_a_finished_job_cannot_be_cancelled(world):
    api, scenarios = world
    done = scenarios["jobDoneLastWeek"]["jobId"]
    assert api.get(f"/jpm/jobs/{done}").json()["jobStatus"] == "Completed"
    response = api.request("PUT", f"/jpm/jobs/{done}/cancel", json={"reasonId": 401, "memo": "x"})
    assert response.status_code == 409
    assert response.json()["data"]["code"] == "invalid_state"


def test_reschedule_moves_capacity_from_one_window_to_the_other(small):
    api, ids = small
    job = book(api, ids, window="morning").json()
    appointment = appointment_of(api, job["id"])
    assert (open_slots(api, TOMORROW, "morning"), open_slots(api, TOMORROW, "afternoon")) == (2, 3)
    moved = api.request(
        "PATCH",
        f"/jpm/appointments/{appointment['id']}/reschedule",
        json=window_times(TOMORROW, "afternoon"),
    )
    assert moved.status_code == 200
    assert (
        moved.json()["arrivalWindowStart"]
        == window_times(TOMORROW, "afternoon")["arrivalWindowStart"]
    )
    assert (open_slots(api, TOMORROW, "morning"), open_slots(api, TOMORROW, "afternoon")) == (3, 2)


def test_reschedule_keeps_the_technician_when_that_technician_is_free(small):
    api, ids = small
    job = book(api, ids, window="morning").json()
    appointment = appointment_of(api, job["id"])
    before = technician_of(api, appointment["id"])
    api.request(
        "PATCH",
        f"/jpm/appointments/{appointment['id']}/reschedule",
        json=window_times(TOMORROW, "afternoon"),
    )
    assert technician_of(api, appointment["id"]) == before


def test_reschedule_into_a_full_window_leaves_the_booking_alone(small):
    api, ids = small
    for _ in range(3):
        book(api, ids, window="afternoon")
    job = book(api, ids, window="morning").json()
    appointment = appointment_of(api, job["id"])
    response = api.request(
        "PATCH",
        f"/jpm/appointments/{appointment['id']}/reschedule",
        json=window_times(TOMORROW, "afternoon"),
    )
    assert response.status_code == 409
    unchanged = api.get(f"/jpm/appointments/{appointment['id']}").json()
    assert unchanged["arrivalWindowStart"] == appointment["arrivalWindowStart"]
    assert open_slots(api, TOMORROW, "morning") == 2


def test_a_technician_can_be_assigned_through_dispatch(small):
    api, ids = small
    job = book(api, ids, window="morning").json()
    appointment = appointment_of(api, job["id"])
    current = technician_of(api, appointment["id"])
    other = next(t for t in (7001, 7002, 7003) if t != current)
    assigned = api.post(
        "/dispatch/appointment-assignments/assign-technicians",
        {"jobAppointmentId": appointment["id"], "technicianIds": [other]},
    )
    assert assigned.status_code == 200
    assert assigned.json()["id"] == appointment["id"]
    assert technician_of(api, appointment["id"]) == other
    row = api.get("/dispatch/appointment-assignments", jobId=job["id"]).json()["data"][0]
    assert row["status"] == "Scheduled" and row["active"] is True
    assert row["appointmentId"] == appointment["id"] and row["jobId"] == job["id"]
    assert set(row) >= {"technicianId", "technicianName", "assignedOn", "isPaused"}


def test_assigning_a_technician_who_is_busy_is_a_conflict(small):
    api, ids = small
    first = appointment_of(api, book(api, ids, window="morning").json()["id"])
    second = appointment_of(api, book(api, ids, window="morning").json()["id"])
    busy = technician_of(api, first["id"])
    response = api.post(
        "/dispatch/appointment-assignments/assign-technicians",
        {"jobAppointmentId": second["id"], "technicianIds": [busy]},
    )
    assert response.status_code == 409
    assert technician_of(api, second["id"]) != busy


def test_the_mock_does_not_apply_the_companys_own_rules(small):
    """Urgent holds, the urgent-only night window, refused customer types and the service area are
    the company's rules, applied by the tool server. ServiceTitan itself would take all of these."""
    api, ids = small
    assert [book(api, ids).status_code for _ in range(3)] == [200, 200, 200], "no urgent hold"
    night = book(api, ids, window="after_hours", priority="Normal", job_type=NO_HEAT)
    assert night.status_code == 200
    assert technician_of(api, appointment_of(api, night.json()["id"])["id"]) == 7001
    assert book(api, ids, who="commercial", window="afternoon").status_code == 200
    assert book(api, ids, who="far", window="afternoon").status_code == 200


def test_a_job_needs_a_technician_with_the_right_skill(tmp_path):
    from servicetitan_mock.app import create_app

    ids = build_small_world(tmp_path / "w.db", skills=("maintenance", "heating"))
    api = Api(create_app(tmp_path / "w.db", TENANT_DIR, tmp_path / "w.log", clock=lambda: NOW))
    assert book(api, ids, job_type=NO_COOLING, priority="Urgent").json()["data"]["code"] == (
        "no_capacity"
    )
    assert book(api, ids, job_type=NO_HEAT, priority="Urgent").status_code == 200


def test_an_arrival_window_must_match_a_configured_window(small):
    api, ids = small
    customer_id, location_id = ids["home"]
    body = job_body(customer_id, location_id, TOMORROW, "morning")
    body["appointments"][0]["arrivalWindowEnd"] = "2026-11-03T18:00:00Z"
    response = api.post("/jpm/jobs", body)
    assert response.status_code == 400
    assert response.json()["data"]["code"] == "invalid_window"


@pytest.mark.parametrize(
    "day, window", [(date(2026, 11, 7), "evening"), (date(2026, 11, 8), "morning")]
)
def test_a_window_outside_business_hours_cannot_be_booked(small, day, window):
    """Saturday evening and Sunday morning are never offered by capacity, so booking refuses them too."""
    api, ids = small
    customer_id, location_id = ids["home"]
    response = api.post("/jpm/jobs", job_body(customer_id, location_id, day, window))
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "window_not_offered"


def test_a_window_that_has_started_cannot_be_booked(small):
    api, ids = small
    customer_id, location_id = ids["home"]
    response = api.post("/jpm/jobs", job_body(customer_id, location_id, START, "morning"))
    assert response.status_code == 422
    assert response.json()["data"]["code"] == "slot_in_past"


def test_job_history_records_what_happened(small):
    api, ids = small
    job = book(api, ids).json()
    api.request(
        "PUT", f"/jpm/jobs/{job['id']}/cancel", json={"reasonId": 401, "memo": "no longer needed"}
    )
    events = [e["eventType"] for e in api.get(f"/jpm/jobs/{job['id']}/history").json()["data"]]
    assert events == ["AppointmentScheduled", "JobCreated", "JobCanceled"]


def test_the_job_list_filters_on_one_status_and_refuses_a_list(small):
    api, ids = small
    kept, dropped = book(api, ids).json(), book(api, ids).json()
    api.request("PUT", f"/jpm/jobs/{dropped['id']}/cancel", json={"reasonId": 401, "memo": "x"})
    scheduled = api.get("/jpm/jobs", jobStatus="Scheduled").json()["data"]
    assert [j["id"] for j in scheduled] == [kept["id"]]
    assert [j["id"] for j in api.get("/jpm/jobs", jobStatus="Canceled").json()["data"]] == [
        dropped["id"]
    ]
    assert api.get("/jpm/jobs", jobStatus="Scheduled,Dispatched").status_code == 400
    assert api.get("/jpm/jobs", jobStatus="Working").status_code == 400, "a job is InProgress"
    assert api.get("/jpm/jobs", appointmentStatus="Scheduled").json()["totalCount"] == 1


def test_an_intake_booking_takes_no_capacity_and_repeats_return_the_first(small):
    api, _ = small
    body = {
        "externalId": "call-77",
        "source": "voice-agent",
        "name": "Pat Home",
        "summary": "Furnace noise",
        "isFirstTimeClient": True,
        "address": {
            "street": "1 Main St",
            "city": "Brooklyn",
            "state": "NY",
            "zip": "11201",
            "country": "USA",
        },
        "contacts": [{"type": "MobilePhone", "value": "718-555-0111"}],
    }
    first = api.post("/crm/booking-provider/501/bookings", body)
    second = api.post("/crm/booking-provider/501/bookings", body)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    booking = first.json()
    assert booking["status"] == "New" and booking["bookingProviderId"] == 501
    assert booking["isFirstTimeClient"] is True and "providerId" not in booking
    assert api.get("/crm/bookings", externalId="call-77").json()["totalCount"] == 1
    assert open_slots(api, TOMORROW, "morning") == 3
    required = {"source", "name", "summary", "isFirstTimeClient", "externalId"}
    for field in required:
        assert (
            api.post(
                "/crm/booking-provider/501/bookings", {k: v for k, v in body.items() if k != field}
            ).status_code
            == 400
        )


def test_capacity_answers_with_flat_time_frames_and_named_technicians(small):
    api, ids = small
    book(api, ids)  # one technician is booked in the morning
    frame = capacity_frame(api, TOMORROW, "morning")
    assert set(frame) == {
        "start",
        "end",
        "startUtc",
        "endUtc",
        "businessUnitIds",
        "totalAvailability",
        "openAvailability",
        "technicians",
        "isAvailable",
        "isExceedingIdealBookingPercentage",
    }
    assert frame["businessUnitIds"] == [11]
    assert (
        frame["start"] == "2026-11-03T08:00:00-05:00"
        and frame["startUtc"] == "2026-11-03T13:00:00Z"
    )
    assert frame["isAvailable"] is True
    statuses = sorted(t["status"] for t in frame["technicians"])
    assert statuses == ["Available", "Available", "Unavailable"]
    assert {t["hasRequiredSkills"] for t in frame["technicians"]} == {True}
    assert set(frame["technicians"][0]) == {"id", "name", "status", "hasRequiredSkills"}
    # hours: three technicians in a four-hour frame, one of them booked (ASSUMED unit)
    assert (frame["totalAvailability"], frame["openAvailability"]) == (12, 8)


def test_capacity_is_one_flat_list_ordered_by_time_with_the_night_frame_last(small):
    api, _ = small
    body = {
        "startsOnOrAfter": "2026-11-03T05:00:00Z",
        "endsOnOrBefore": "2026-11-04T13:00:00Z",
        "jobTypeId": TUNE_UP,
        "skillBasedAvailability": True,
    }
    response = api.post("/dispatch/capacity", body).json()
    assert "timeStamp" in response
    frames = response["availabilities"]
    assert [f["startUtc"] for f in frames] == sorted(f["startUtc"] for f in frames)
    assert [f["start"][11:16] for f in frames[:4]] == ["08:00", "12:00", "16:00", "20:00"]
    night = frames[3]
    assert [t["id"] for t in night["technicians"]] == [7001], "only the on-call technician"
    assert night["end"] == "2026-11-04T08:00:00-05:00"


def test_capacity_requires_skill_based_availability_and_has_no_priority_or_zip(small):
    api, _ = small
    body = {"startsOnOrAfter": "2026-11-03T05:00:00Z", "endsOnOrBefore": "2026-11-04T05:00:00Z"}
    assert api.post("/dispatch/capacity", body).status_code == 400
    with_extras = {**body, "skillBasedAvailability": False, "priority": "Urgent", "zip": "11201"}
    frames = api.post("/dispatch/capacity", with_extras).json()["availabilities"]
    assert all(t["hasRequiredSkills"] is None for f in frames for t in f["technicians"])
