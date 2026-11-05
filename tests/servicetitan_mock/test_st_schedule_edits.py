"""Writes the owner view and the bench make (issue #152): a non-job appointment takes a technician
out of capacity, and jobs, locations, customers and contacts change in place."""

from conftest import CFG, TOMORROW, TUNE_UP, capacity_frame, job_body, open_slots

from servicetitan_mock.db import add_missing_tables, connect, create_schema


def _block(api, technician_id: int, window_id: str = "morning", **extra):
    start, end = CFG.window_bounds(TOMORROW, CFG.window(window_id))
    hours = int((end - start).total_seconds() // 3600)
    body = {
        "technicianId": technician_id,
        "start": start.isoformat(),
        "name": "Training",
        "duration": f"{hours:02d}:00:00",
        "removeTechnicianFromCapacityPlanning": True,
        **extra,
    }
    return api.post("/dispatch/non-job-appointments", body)


def test_a_block_takes_its_technician_out_of_the_window_and_only_that_window(small):
    api, _ = small
    assert open_slots(api, TOMORROW, "morning") == 3
    made = _block(api, 7001)
    assert made.status_code == 200, made.text
    assert made.json()["duration"] == "04:00:00" and made.json()["active"] is True
    frame = capacity_frame(api, TOMORROW, "morning")
    assert {t["id"]: t["status"] for t in frame["technicians"]}[7001] == "Unavailable"
    assert open_slots(api, TOMORROW, "morning") == 2
    assert open_slots(api, TOMORROW, "afternoon") == 3


def test_a_job_cannot_be_booked_with_a_blocked_technician(small):
    api, ids = small
    for tech in (7001, 7002, 7003):
        _block(api, tech)
    booked = api.post("/jpm/jobs", job_body(*ids["home"], TOMORROW, "morning"))
    assert booked.status_code == 409
    chosen = api.post(
        "/jpm/jobs", job_body(*ids["home"], TOMORROW, "afternoon", appointments=[
            {**job_body(*ids["home"], TOMORROW, "afternoon")["appointments"][0],
             "technicianIds": [7001]}
        ])
    )
    assert chosen.status_code == 200


def test_a_block_taken_off_the_schedule_frees_the_window_and_stays_listed(small):
    api, _ = small
    made = _block(api, 7001).json()
    body = {k: made[k] for k in ("technicianId", "start", "name", "duration")}
    off = api.request(
        "PUT", f"/dispatch/non-job-appointments/{made['id']}", json={**body, "active": False}
    )
    assert off.status_code == 200 and off.json()["active"] is False
    assert open_slots(api, TOMORROW, "morning") == 3
    assert api.get("/dispatch/non-job-appointments").json()["data"] == []
    every = api.get("/dispatch/non-job-appointments", activeOnly="false").json()["data"]
    assert [b["id"] for b in every] == [made["id"]]
    assert api.request("DELETE", f"/dispatch/non-job-appointments/{made['id']}").status_code == 405


def test_a_block_that_leaves_capacity_alone_is_only_on_the_schedule(small):
    api, _ = small
    _block(api, 7001, removeTechnicianFromCapacityPlanning=False)
    assert open_slots(api, TOMORROW, "morning") == 3


def test_a_block_needs_a_real_technician_and_a_sensible_length(small):
    api, _ = small
    assert _block(api, 9999).status_code == 422
    assert _block(api, 7001, duration="soon").status_code == 400
    assert _block(api, 7001, duration="00:00:00").status_code == 400
    assert _block(api, 7001, duration="8.00:00:00").status_code == 400
    assert _block(api, 7001, duration="1.00:00:00").status_code == 200


def test_blocks_list_by_start_and_technician(small):
    api, _ = small
    _block(api, 7001)
    _block(api, 7002, "afternoon")
    start, _ = CFG.window_bounds(TOMORROW, CFG.window("afternoon"))
    later = api.get("/dispatch/non-job-appointments", startsOnOrAfter=start.isoformat()).json()
    assert [b["technicianId"] for b in later["data"]] == [7002]
    mine = api.get("/dispatch/non-job-appointments", technicianId=7001).json()["data"]
    assert [b["technicianId"] for b in mine] == [7001]


def test_a_job_changes_type_priority_and_summary_in_place(small):
    api, ids = small
    job = api.post("/jpm/jobs", job_body(*ids["home"], TOMORROW, "morning")).json()
    patched = api.request(
        "PATCH",
        f"/jpm/jobs/{job['id']}",
        json={"jobTypeId": 204, "priority": "High", "summary": "Gate code 4411"},
    )
    assert patched.status_code == 200, patched.text
    assert (patched.json()["jobTypeId"], patched.json()["priority"]) == (204, "High")
    assert patched.json()["summary"] == "Gate code 4411"
    events = [h["eventType"] for h in api.get(f"/jpm/jobs/{job['id']}/history").json()["data"]]
    assert "JobTypeChanged" in events and "PriorityChanged" in events


def test_a_job_type_from_another_business_unit_is_refused(small):
    api, ids = small
    job = api.post("/jpm/jobs", job_body(*ids["home"], TOMORROW, "morning")).json()
    moved = api.request("PATCH", f"/jpm/jobs/{job['id']}", json={"jobTypeId": 205})
    assert moved.status_code == 422
    assert api.get(f"/jpm/jobs/{job['id']}").json()["jobTypeId"] == TUNE_UP


def test_a_location_address_is_corrected_and_found_by_its_new_street(small):
    api, ids = small
    _, location_id = ids["home"]
    fixed = {"street": "12 Union Street", "unit": "2F", "city": "Brooklyn", "state": "NY",
             "zip": "11231", "country": "USA"}
    patched = api.request("PATCH", f"/crm/locations/{location_id}", json={"address": fixed})
    assert patched.status_code == 200, patched.text
    assert patched.json()["address"]["street"] == "12 Union Street"
    assert patched.json()["zoneId"] is not None
    found = api.get("/crm/locations", street="12 Union St", zip="11231").json()["data"]
    assert [loc["id"] for loc in found] == [location_id]


def test_a_customer_is_renamed(small):
    api, ids = small
    customer_id, _ = ids["home"]
    renamed = api.request("PATCH", f"/crm/customers/{customer_id}", json={"name": "Pat Homer"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Pat Homer"
    assert api.request("PATCH", f"/crm/customers/{customer_id}", json={"name": ""}).status_code == 400


def test_a_changed_phone_number_finds_the_customer_and_the_old_one_does_not(small):
    api, ids = small
    customer_id, _ = ids["home"]
    contact = api.post(
        f"/crm/customers/{customer_id}/contacts", {"type": "MobilePhone", "value": "718-555-0101"}
    ).json()
    changed = api.request(
        "PATCH",
        f"/crm/customers/{customer_id}/contacts/{contact['id']}",
        json={"value": "(917) 555-0188"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["phoneSettings"]["phoneNumber"] == "9175550188"
    assert [c["id"] for c in api.get("/crm/customers", phone="9175550188").json()["data"]] == [
        customer_id
    ]
    assert api.get("/crm/customers", phone="7185550101").json()["data"] == []


def test_a_phone_number_needs_ten_digits_and_a_contact_of_that_customer(small):
    api, ids = small
    customer_id, _ = ids["home"]
    contact = api.post(
        f"/crm/customers/{customer_id}/contacts", {"type": "MobilePhone", "value": "718-555-0101"}
    ).json()
    path = f"/crm/customers/{customer_id}/contacts/{contact['id']}"
    assert api.request("PATCH", path, json={"value": "555-01"}).status_code == 400
    other = ids["far"][0]
    wrong = f"/crm/customers/{other}/contacts/{contact['id']}"
    assert api.request("PATCH", wrong, json={"value": "917-555-0188"}).status_code == 404


def test_an_existing_world_gains_the_new_table_without_losing_its_data(tmp_path):
    path = tmp_path / "old.db"
    create_schema(path)
    conn = connect(path)
    conn.execute("DROP TABLE non_job_appointments")
    conn.execute("INSERT INTO meta VALUES ('kept', 'yes')")
    conn.close()
    add_missing_tables(path)
    conn = connect(path)
    try:
        assert conn.execute("SELECT count(*) FROM non_job_appointments").fetchone()[0] == 0
        assert conn.execute("SELECT value FROM meta WHERE key = 'kept'").fetchone()[0] == "yes"
    finally:
        conn.close()


def test_a_block_over_a_technicians_visit_is_refused(small):
    api, ids = small
    booked = api.post("/jpm/jobs", job_body(*ids["home"], TOMORROW, "morning", appointments=[
        {**job_body(*ids["home"], TOMORROW, "morning")["appointments"][0], "technicianIds": [7001]}
    ]))
    assert booked.status_code == 200
    refused = _block(api, 7001)
    assert refused.status_code == 409 and refused.json()["data"]["code"] == "technician_busy"
    assert _block(api, 7002).status_code == 200
    assert _block(api, 7001, "afternoon").status_code == 200
