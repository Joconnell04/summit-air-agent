"""Lookups and reads against the full seeded Summit Air world."""

import sqlite3
from datetime import date, timedelta

from conftest import (
    CFG,
    NOW,
    START,
    TENANT_DIR,
    TOMORROW,
    TUNE_UP,
    Api,
    job_body,
    open_slots,
)

from servicetitan_mock.app import create_app
from servicetitan_mock.seed import build_world


def ids(response) -> list[int]:
    return [row["id"] for row in response.json()["data"]]


def test_the_world_has_the_promised_shape(world):
    api, scenarios = world
    assert api.get("/crm/customers", pageSize=1).json()["totalCount"] == 300
    types = [c["type"] for c in api.get("/crm/customers", pageSize=200).json()["data"]]
    types += [c["type"] for c in api.get("/crm/customers", pageSize=200, page=2).json()["data"]]
    assert types.count("Commercial") == 4 and types.count("Residential") >= 200
    assert api.get("/settings/technicians", pageSize=1).json()["totalCount"] == 40
    statuses = {
        m["status"] for m in api.get("/memberships/memberships", pageSize=200).json()["data"]
    }
    assert statuses == {"Active", "Expired", "Canceled"}
    memos = {
        c["memo"].split(":")[0]
        for customer in api.get("/crm/customers", pageSize=40).json()["data"]
        for c in api.get(f"/crm/customers/{customer['id']}/contacts").json()["data"]
        if c["type"] != "Email"
    }
    assert {"Owner", "Tenant"} <= memos, "who a number belongs to is written in the contact memo"
    assert "contacts" not in api.get("/crm/customers", pageSize=1).json()["data"][0]
    assert scenarios["activeMemberWithAdultChild"]["adultChildPhone"]


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
    contacts = api.get(f"/crm/customers/{park['customerId']}/contacts").json()["data"]
    memos = {c["memo"] for c in contacts if c["value"] == park["adultChildPhone"]}
    assert memos == {"Adult child: David Park"}
    assert set(contacts[0]) >= {"id", "type", "value", "memo", "modifiedOn", "phoneSettings"}
    assert "role" not in contacts[0] and "name" not in contacts[0]


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
    found = api.get("/memberships/memberships", customerIds=moretti["customerId"]).json()
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
            "/memberships/memberships", customerIds=santos["customerId"], status="Active"
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


def test_a_membership_type_has_servicetitans_fields_and_no_perks_block(world):
    """Perks (priority scheduling, a repair discount) are the company's, read from the tenant config
    by the tool server; ServiceTitan's membership type holds a discount and recurring services."""
    api, _ = world
    club = api.get("/memberships/membership-types/601").json()
    assert club["name"] == "Summit Standard Membership"
    assert "perks" not in club
    assert club["discount"] == 15
    assert [s["name"] for s in club["recurringServices"]] == [
        "Heating system tune-up",
        "Cooling system tune-up",
    ]


def test_a_member_has_service_visits_due(world):
    api, scenarios = world
    park = scenarios["activeMemberWithAdultChild"]
    events = api.get("/memberships/recurring-service-events", locationId=park["locationId"]).json()[
        "data"
    ]
    assert len(events) == 2
    assert {e["status"] for e in events} <= {"Won", "NotAttempted"}
    assert {e["membershipId"] for e in events} == {park["membershipId"]}
    assert set(events[0]) >= {"locationRecurringServiceId", "locationRecurringServiceName", "jobId"}
    assert (
        api.get("/memberships/recurring-service-events", status="NotAttempted").json()["totalCount"]
        > 0
    )


def test_equipment_lists_type_brand_and_install_date(world):
    api, scenarios = world
    maria = scenarios["repeatCallerOpenJob"]
    systems = api.get(
        "/equipmentsystems/installed-equipment", locationIds=maria["locationId"]
    ).json()["data"]
    assert systems
    assert {"type", "manufacturer", "installedOn", "status"} <= set(systems[0])
    assert not {"systemType", "installYear"} & set(systems[0])
    assert systems[0]["type"]["name"] in {"boiler", "furnace"}
    assert systems[0]["status"] == "Installed"
    assert (
        api.get("/equipmentsystems/installed-equipment", locationIds="999999").json()["data"] == []
    )


def test_the_out_of_area_customer_exists_and_the_mock_does_not_refuse_the_booking(world):
    """Serving a ZIP is the company's rule (the tool server checks it); ServiceTitan has no such check."""
    api, scenarios = world
    whitman = scenarios["outOfArea"]
    assert api.get(f"/crm/locations/{whitman['locationId']}").json()["address"]["zip"] == "11501"
    zips = {z for zone in api.get("/dispatch/zones").json()["data"] for z in zone["zips"]}
    assert "11501" not in zips
    response = api.post(
        "/jpm/jobs", job_body(whitman["customerId"], whitman["locationId"], TOMORROW, "1200-1600")
    )
    assert response.status_code == 200


def test_the_commercial_account_exists_and_has_a_lead(world):
    api, scenarios = world
    bakery = scenarios["commercial"]
    assert api.get(f"/crm/customers/{bakery['customerId']}").json()["type"] == "Commercial"
    leads = api.get("/crm/leads", leadPhone=bakery["phone"]).json()["data"]
    assert leads[0]["callReasonId"] == 704, "the Commercial call reason"
    assert leads[0]["leadCustomerName"] == "Harbor Bakery Group"
    assert leads[0]["customerId"] == bakery["customerId"] and leads[0]["status"] == "Open"


def test_a_repeat_caller_has_several_recent_calls_and_an_open_job(world):
    api, scenarios = world
    maria = scenarios["repeatCallerOpenJob"]
    since = (NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = "/telecom/v3/tenant/1000/calls"
    response = api.client.get(
        path,
        params={"callerPhoneNumber": "718-555-0142", "createdOnOrAfter": since},
        headers=api.headers,
    )
    calls = response.json()["data"]
    assert len(calls) == 3
    assert {c["leadCall"]["customer"]["id"] for c in calls} == {maria["customerId"]}
    assert calls[0]["jobNumber"] == str(maria["jobId"])
    open_jobs = [
        j
        for j in api.get("/jpm/jobs", customerId=maria["customerId"]).json()["data"]
        if j["jobStatus"] in ("Scheduled", "Dispatched", "InProgress", "Hold")
    ]
    assert [j["id"] for j in open_jobs] == [maria["jobId"]]
    appointment = api.get("/jpm/appointments", jobId=maria["jobId"]).json()["data"][0]
    assert appointment["status"] == "Scheduled"


def test_a_job_done_last_week_shows_as_completed_with_history(world):
    api, scenarios = world
    brennan = scenarios["jobDoneLastWeek"]
    job = api.get(f"/jpm/jobs/{brennan['jobId']}").json()
    assert job["jobStatus"] == "Completed"
    assert job["completedOn"].startswith((START - timedelta(days=6)).isoformat())
    assert (
        api.get("/jpm/appointments", jobId=brennan["jobId"]).json()["data"][0]["status"] == "Done"
    )
    history = [
        e["eventType"] for e in api.get(f"/jpm/jobs/{brennan['jobId']}/history").json()["data"]
    ]
    assert history == ["JobCreated", "JobCompleted"]


def test_two_weeks_of_capacity_with_some_days_nearly_full(world):
    """Nearly full means few normal slots: the schedule leaves the urgent hold free on top of them,
    which the tool server (not the mock) keeps back from normal jobs."""
    api, scenarios = world
    body = {
        "startsOnOrAfter": "2026-11-02T05:00:00Z",
        "endsOnOrBefore": "2026-11-16T05:00:00Z",
        "jobTypeId": TUNE_UP,
        "businessUnitIds": [11],
        "skillBasedAvailability": True,
    }
    frames = api.post("/dispatch/capacity", body).json()["availabilities"]

    def free(frame):
        return sum(
            t["status"] == "Available" and t["hasRequiredSkills"] for t in frame["technicians"]
        )

    by_day: dict[str, dict[str, int]] = {}
    for frame in frames:
        if not frame["start"].endswith(("08:00:00-05:00", "12:00:00-05:00", "16:00:00-05:00")):
            continue
        by_day.setdefault(frame["start"][:10], {})[frame["start"][11:13]] = free(frame)
    assert "2026-11-02" in by_day and "2026-11-14" in by_day
    hold = CFG.hold_for(date(2026, 11, 4), "0800-1200", 11)
    assert all(count == hold for count in by_day["2026-11-04"].values()), by_day["2026-11-04"]
    assert max(sum(d.values()) for d in by_day.values()) > 20
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

    before = open_slots(api, TOMORROW, "1200-1600")
    response = api.post(
        "/jpm/jobs",
        job_body(park["customerId"], park["locationId"], TOMORROW, "1200-1600"),
        key="x",
    )
    assert response.status_code == 200
    assert open_slots(api, TOMORROW, "1200-1600") == before - 1


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
    assert outside == [r["locationId"] for r in scenarios["outOfAreaResidents"]]


def test_there_are_a_few_commercial_accounts_of_different_kinds(world):
    api, scenarios = world
    accounts = scenarios["commercialAccounts"]
    assert {a["kind"] for a in accounts} >= {"restaurant", "school", "office"}
    for account in accounts:
        assert api.get(f"/crm/customers/{account['customerId']}").json()["type"] == "Commercial"


def test_a_property_manager_and_a_co_op_board_are_residential_accounts(world):
    """Eval round 2: they look after residential buildings, so they book like any home."""
    api, scenarios = world
    buildings = scenarios["residentialBuildingAccounts"]
    assert {a["kind"] for a in buildings} == {"property manager", "co-op board"}
    for account in buildings:
        assert api.get(f"/crm/customers/{account['customerId']}").json()["type"] == "Residential"


def test_a_few_residents_live_outside_the_service_area(world):
    api, scenarios = world
    residents = scenarios["outOfAreaResidents"]
    assert {r["region"] for r in residents} == {"Nassau", "Westchester", "New Jersey"}
    assert len(residents) >= 5
    area = {z for zone in api.get("/dispatch/zones").json()["data"] for z in zone["zips"]}
    for resident in residents:
        location = api.get(f"/crm/locations/{resident['locationId']}").json()
        assert location["address"]["zip"] == resident["zip"]
        assert resident["zip"] not in area


def test_seeded_customer_notes_are_read_with_the_customer(world):
    api, scenarios = world
    notes = scenarios["customerNotes"]
    assert set(notes) == {
        "tonePreference",
        "gateCode",
        "tenantLandlordApproves",
        "adultChildCalls",
        "noteThatAsksToSkipAGate",
    }
    for planted in notes.values():
        texts = [
            n["text"]
            for n in api.get(f"/crm/customers/{planted['customerId']}/notes").json()["data"]
        ]
        assert planted["text"] in texts


def test_notes_can_be_added_but_never_changed_or_removed(world):
    api, scenarios = world
    maria = scenarios["repeatCallerOpenJob"]
    path = f"/crm/customers/{maria['customerId']}/notes"
    before = api.get(path).json()["totalCount"]
    added = api.post(path, {"text": "Prefers mornings."}, key="note-1")
    assert added.status_code == 200
    assert added.json()["createdById"] == 9001, "the integration user, from the credentials"
    assert set(added.json()) == {"id", "text", "isPinned", "createdById", "createdOn", "modifiedOn"}
    assert (
        api.post(path, {"text": "Prefers mornings."}, key="note-1").json()["id"]
        == added.json()["id"]
    )
    note_id = added.json()["id"]
    for method in ("PUT", "PATCH", "DELETE"):
        assert api.request(method, f"{path}/{note_id}").status_code in (404, 405)
        assert api.request(method, path).status_code == 405
    assert api.get(path).json()["totalCount"] == before + 1


def test_a_job_note_belongs_to_the_job_and_has_no_id(world):
    api, scenarios = world
    maria = scenarios["repeatCallerOpenJob"]
    before = api.get(f"/crm/customers/{maria['customerId']}/notes").json()["totalCount"]
    added = api.post(f"/jpm/jobs/{maria['jobId']}/notes", {"text": "Use the side gate."})
    assert added.status_code == 200
    assert set(added.json()) == {"text", "isPinned", "createdById", "createdOn", "modifiedOn"}
    on_job = api.get(f"/jpm/jobs/{maria['jobId']}/notes").json()["data"]
    assert added.json() in on_job
    after = api.get(f"/crm/customers/{maria['customerId']}/notes").json()["totalCount"]
    assert after == before, "customer notes and job notes are separate lists"


def test_a_note_must_have_text_and_a_known_customer(world):
    api, _ = world
    assert api.post("/crm/customers/1/notes", {"text": ""}).status_code == 400
    assert api.post("/crm/customers/1/notes", {"text": "x" * 2001}).status_code == 400
    missing = api.post("/crm/customers/999999/notes", {"text": "hi"})
    assert missing.json()["data"]["code"] == "unknown_reference"


def test_every_seeded_address_in_the_city_is_in_the_area(seeded_db):
    """Retell eval suite round 1: a seeded customer's address came back out of area. The ones
    seeded outside the area on purpose are outside the city too (Jersey City, Westchester, Nassau)."""
    from conftest import CFG

    conn = sqlite3.connect(seeded_db)
    try:
        rows = conn.execute(
            "SELECT c.name, l.street, l.zip FROM locations l JOIN customers c ON c.id = l.customer_id"
        ).fetchall()
    finally:
        conn.close()
    city_prefixes = set(CFG.raw["address_lookup"]["zip_boroughs"])
    outside = [(name, z) for name, _, z in rows if z not in CFG.zone_by_zip]
    assert outside and all(z[:3] not in city_prefixes for _, z in outside), outside


def test_the_named_customers_live_where_the_citys_directory_puts_them(seeded_db):
    """Their ZIP is the one GeoSearch gives their house and street (evals/addresses.yaml): "620
    Bedford Ave, 11235" was made up, and the live lookup put it in 11249."""
    from app import geocode
    from evals import addresses

    book = addresses.load()
    conn = sqlite3.connect(seeded_db)
    try:
        for name in ("Maria Delgado", "Thomas Brennan", "Linda Park", "Frank Moretti",
                     "Gloria Santos", "Harbor Bakery Group"):
            street, zip_code = conn.execute(
                "SELECT l.street, l.zip FROM locations l JOIN customers c ON c.id = l.customer_id "
                "WHERE c.name = ? ORDER BY l.id LIMIT 1",
                (name,),
            ).fetchone()
            house, rest = geocode.split_address(street)
            places = [p for p in book[addresses.key(house, rest)]
                      if geocode.house_key(p.house) == geocode.house_key(house)]
            assert zip_code in {p.zip for p in places}, (name, street, zip_code)
    finally:
        conn.close()
