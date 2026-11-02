"""Lookups and reads against the full seeded Summit Air world."""

import sqlite3
from datetime import timedelta

from conftest import CFG, NOW, START, TENANT_DIR, TOMORROW, TUNE_UP, Api, open_slots

from servicetitan_mock.app import create_app
from servicetitan_mock.seed import build_world


def ids(response) -> list[int]:
    return [row["id"] for row in response.json()["data"]]


def test_the_world_has_the_promised_shape(world):
    api, _ = world
    assert api.get("/crm/customers", pageSize=1).json()["totalCount"] == 300
    assert api.get("/crm/customers", type="Commercial").json()["totalCount"] == 1
    assert api.get("/settings/technicians", pageSize=1).json()["totalCount"] == 40
    statuses = {
        m["status"] for m in api.get("/memberships/memberships", pageSize=200).json()["data"]
    }
    assert statuses == {"Active", "Expired", "Canceled"}
    roles = {
        c["role"]
        for r in api.get("/crm/customers", pageSize=200).json()["data"]
        for c in r["contacts"]
    }
    assert {"owner", "tenant", "adult_child"} <= roles


def test_a_shared_phone_number_returns_every_customer(world):
    api, scenarios = world
    shared = scenarios["sharedPhone"]
    found = api.get("/crm/customers", phone=shared["phone"])
    assert sorted(ids(found)) == sorted(shared["customerIds"])
    assert ids(api.get("/crm/customers", phone="+1 718-555-0119")) == ids(found)
    assert ids(api.get("/crm/customers", phone="7185550119")) == ids(found)


def test_an_unknown_phone_returns_an_empty_list_not_an_error(world):
    api, _ = world
    response = api.get("/crm/customers", phone="(212) 555-0999")
    assert response.status_code == 200
    assert response.json()["data"] == []


def test_an_adult_child_phone_finds_the_parents_record(world):
    api, scenarios = world
    park = scenarios["activeMemberWithAdultChild"]
    found = api.get("/crm/customers", phone=park["adultChildPhone"]).json()["data"]
    assert [c["id"] for c in found] == [park["customerId"]]
    roles = {c["role"] for c in found[0]["contacts"] if c["value"] == park["adultChildPhone"]}
    assert roles == {"adult_child"}


def test_address_lookup_works_when_the_phone_does_not_match(world):
    api, scenarios = world
    both = scenarios["ownerAndTenant"]
    building = api.get("/crm/locations", street=both["street"], zip=both["zip"])
    assert sorted(r["customerId"] for r in building.json()["data"]) == sorted(
        [both["ownerCustomerId"], both["tenantCustomerId"]]
    )
    tenant_only = api.get("/crm/locations", street=both["street"], zip=both["zip"], unit="Apt 2")
    assert [r["customerId"] for r in tenant_only.json()["data"]] == [both["tenantCustomerId"]]


def test_address_lookup_ignores_case_and_street_suffix_spelling(world):
    api, scenarios = world
    address = scenarios["sharedPhone"]
    street = (
        address["street"]
        .lower()
        .replace(" ave", " avenue")
        .replace(" st", " street")
        .replace(" blvd", " boulevard")
    )
    found = api.get("/crm/locations", street=street, zip=address["zip"])
    assert {r["customerId"] for r in found.json()["data"]} == set(address["customerIds"])


def test_an_expired_membership_reads_as_expired_not_missing(world):
    api, scenarios = world
    moretti = scenarios["expiredMember"]
    found = api.get("/memberships/memberships", customerId=moretti["customerId"]).json()
    assert found["totalCount"] == 1
    assert found["data"][0]["status"] == "Expired"
    assert found["data"][0]["active"] is False


def test_a_plan_past_its_end_date_reads_as_expired_even_if_stored_as_active(world):
    api, scenarios = world
    santos = scenarios["storedActiveButLapsed"]
    row = api.get(f"/memberships/memberships/{santos['membershipId']}").json()
    assert row["status"] == "Expired"
    stored = (
        sqlite3.connect(api.app.state.db_path)
        .execute("SELECT status FROM memberships WHERE id = ?", (santos["membershipId"],))
        .fetchone()[0]
    )
    assert stored == "Active"
    assert (
        api.get(
            "/memberships/memberships", locationId=santos["locationId"], status="Active"
        ).json()["data"]
        == []
    )


def test_there_is_one_membership_plan(world):
    """A caller is either a Summit member or not: one active plan type, and every membership uses it."""
    api, _ = world
    types = api.get("/memberships/membership-types").json()["data"]
    assert [t["id"] for t in types] == [601]
    plans = api.get("/memberships/memberships", pageSize=200).json()["data"]
    assert plans and {p["membershipTypeId"] for p in plans} == {601}


def test_a_membership_type_lists_its_perks(world):
    api, _ = world
    club = api.get("/memberships/membership-types/601").json()
    assert club["name"] == "Summit Standard Membership"
    assert club["perks"] == {
        "prioritySched": True,
        "visitsPerYear": 2,
        "repairDiscountPercent": 15,
        "afterHoursFeeWaived": True,
    }


def test_a_member_has_service_visits_due(world):
    api, scenarios = world
    park = scenarios["activeMemberWithAdultChild"]
    events = api.get(
        "/memberships/recurring-service-events", membershipId=park["membershipId"]
    ).json()["data"]
    assert len(events) == 2
    assert {e["status"] for e in events} <= {"Completed", "NotAttempted"}
    assert (
        api.get("/memberships/recurring-service-events", status="NotAttempted").json()["totalCount"]
        > 0
    )


def test_equipment_lists_system_type_brand_and_install_year(world):
    api, scenarios = world
    maria = scenarios["repeatCallerOpenJob"]
    systems = api.get(
        "/equipmentsystems/installed-equipment", locationId=maria["locationId"]
    ).json()["data"]
    assert systems
    assert {"systemType", "manufacturer", "installYear"} <= set(systems[0])
    assert systems[0]["systemType"] in {"boiler", "furnace"}


def test_the_out_of_area_customer_exists_but_cannot_be_booked(world):
    api, scenarios = world
    whitman = scenarios["outOfArea"]
    assert api.get(f"/crm/locations/{whitman['locationId']}").json()["address"]["zip"] == "11501"
    assert "11501" not in api.get("/settings/company-profile").json()["serviceAreaZips"]
    from conftest import job_body

    response = api.post(
        "/jpm/jobs", job_body(whitman["customerId"], whitman["locationId"], TOMORROW, "morning")
    )
    assert response.json()["code"] == "out_of_area"


def test_the_commercial_account_exists_and_has_a_lead(world):
    api, scenarios = world
    bakery = scenarios["commercial"]
    assert api.get(f"/crm/customers/{bakery['customerId']}").json()["type"] == "Commercial"
    leads = api.get("/crm/leads", phone=bakery["phone"]).json()["data"]
    assert leads[0]["kind"] == "Commercial"
    from conftest import job_body

    response = api.post(
        "/jpm/jobs", job_body(bakery["customerId"], bakery["locationId"], TOMORROW, "morning")
    )
    assert response.json()["code"] == "customer_type_not_supported"


def test_a_repeat_caller_has_several_recent_calls_and_an_open_job(world):
    api, scenarios = world
    maria = scenarios["repeatCallerOpenJob"]
    since = (NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    calls = api.get("/telecom/calls", phone="718-555-0142", receivedOnOrAfter=since).json()["data"]
    assert len(calls) == 3
    assert {c["customerId"] for c in calls} == {maria["customerId"]}
    assert calls[0]["jobId"] == maria["jobId"]
    open_jobs = api.get(
        "/jpm/jobs", customerId=maria["customerId"], jobStatus="Scheduled,Dispatched,Working"
    ).json()
    assert [j["id"] for j in open_jobs["data"]] == [maria["jobId"]]
    appointment = api.get("/jpm/appointments", jobId=maria["jobId"]).json()["data"][0]
    assert appointment["status"] == "Dispatched"


def test_a_job_done_last_week_shows_as_done_with_history(world):
    api, scenarios = world
    brennan = scenarios["jobDoneLastWeek"]
    job = api.get(f"/jpm/jobs/{brennan['jobId']}").json()
    assert job["jobStatus"] == "Done"
    assert job["completedOn"].startswith((START - timedelta(days=6)).isoformat())
    history = [
        e["eventType"] for e in api.get(f"/jpm/jobs/{brennan['jobId']}/history").json()["data"]
    ]
    assert history == ["JobCreated", "JobCompleted"]


def test_two_weeks_of_capacity_with_some_days_nearly_full(world):
    api, scenarios = world
    body = {
        "startsOnOrAfter": "2026-11-02T05:00:00Z",
        "endsOnOrBefore": "2026-11-16T05:00:00Z",
        "jobTypeId": TUNE_UP,
        "businessUnitIds": [11],
    }
    days = {d["date"]: d for d in api.post("/dispatch/capacity", body).json()["availabilities"]}
    assert "2026-11-02" in days and "2026-11-15" in days
    full_day = {s["windowId"]: s for s in days["2026-11-04"]["timeslots"]}
    assert [full_day[w]["openAvailability"] for w in ("morning", "afternoon", "evening")] == [
        0,
        0,
        0,
    ]
    urgent = api.post(
        "/dispatch/capacity",
        {**body, "priority": "Urgent", "endsOnOrBefore": "2026-11-05T05:00:00Z"},
    )
    urgent_day = {s["windowId"]: s for s in urgent.json()["availabilities"][2]["timeslots"]}
    assert all(urgent_day[w]["openAvailability"] >= 1 for w in ("morning", "afternoon", "evening"))
    open_by_day = [
        sum(s["openAvailability"] for s in d["timeslots"] if not s["afterHours"])
        for d in days.values()
    ]
    assert max(open_by_day) > 20 and min(open_by_day) == 0  # busy days and a Sunday with no one in
    assert set(scenarios["nearlyFullDays"]) == {"2026-11-04", "2026-11-05", "2026-11-11"}


def test_one_technician_carries_the_on_call_phone_each_week(world):
    api, scenarios = world
    shifts = api.get("/dispatch/technician-shifts", shiftType="OnCall", pageSize=50).json()["data"]
    assert len(shifts) == 14
    by_night = {s["start"]: s["technicianId"] for s in shifts}
    week_one = {
        by_night[f"2026-11-{d + 1:02d}T01:00:00Z"] for d in range(2, 9)
    }  # nights of Nov 2 to 8
    week_two = {by_night[f"2026-11-{d + 1:02d}T01:00:00Z"] for d in range(9, 16)}
    assert len(week_one) == 1 and len(week_two) == 1 and week_one != week_two
    assert scenarios["onCall"]["2026-11-02"] in week_one


def test_a_booking_in_the_full_world_works_end_to_end(world):
    api, scenarios = world
    park = scenarios["activeMemberWithAdultChild"]
    from conftest import job_body

    before = open_slots(api, TOMORROW, "afternoon")
    response = api.post(
        "/jpm/jobs",
        job_body(park["customerId"], park["locationId"], TOMORROW, "afternoon"),
        key="x",
    )
    assert response.status_code == 201
    assert open_slots(api, TOMORROW, "afternoon") == before - 1


def test_the_seed_is_deterministic(tmp_path):
    first, second = tmp_path / "a.db", tmp_path / "b.db"
    build_world(first, TENANT_DIR, START)
    build_world(second, TENANT_DIR, START)
    tables = ("customers", "contacts", "locations", "memberships", "jobs", "appointments", "calls")
    for table in tables:
        rows = [
            sqlite3.connect(p).execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for p in (first, second)
        ]
        assert rows[0] == rows[1], table


def test_a_different_start_date_moves_the_whole_world(tmp_path):
    build_world(tmp_path / "w.db", TENANT_DIR, START + timedelta(days=7))
    app = create_app(
        tmp_path / "w.db", TENANT_DIR, tmp_path / "w.log", clock=lambda: NOW + timedelta(days=7)
    )
    scenarios = Api(app).client.get("/debug/scenarios").json()
    assert scenarios["startDate"] == "2026-11-09"
    assert set(scenarios["nearlyFullDays"]) == {"2026-11-11", "2026-11-12", "2026-11-18"}


def test_every_customer_location_is_inside_or_deliberately_outside_the_service_area(world):
    api, scenarios = world
    locations = api.get("/crm/locations", pageSize=200).json()["data"]
    outside = [loc["id"] for loc in locations if loc["address"]["zip"] not in CFG.service_area_zips]
    assert outside == [scenarios["outOfArea"]["locationId"]]
