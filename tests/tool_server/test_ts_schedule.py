"""Editing the schedule and customers from the owner view and the bench (issue #152). Each change
goes through the FSM adapter, is logged with who made it, and reaches the agent's next find_slots
or call start, past the slot cache."""

from datetime import date

from toolkit import ready_to_book, sign_in

from app import schedule
from app.text import phone_key

NEW_NUMBER = "(929) 555-0161"  # on no customer in the seeded world


def _post(agent, path: str, **form):
    return agent.client.post(path, data=form, follow_redirects=False)


def _offered(agent, call_id: str, job_type: str = "tune_up") -> set[tuple[str, str]]:
    found = agent.tool("find_slots", call_id, job_type=job_type)
    assert found["ok"], found
    return {(d["date"], w) for d in found["data"]["days"] for w in d["windows"]}


def _edits(agent) -> list[dict]:
    conn = agent.db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM schedule_edits ORDER BY id")]
    finally:
        conn.close()


def _mock_db(mock):
    import sqlite3

    conn = sqlite3.connect(mock[0].state.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_a_window_blocked_on_the_owner_view_is_not_offered_by_the_next_find_slots(agent):
    assert agent.app.state.services.settings.slot_cache_seconds > 0  # the cache is on
    sign_in(agent, "staff")
    call_id = ready_to_book(agent)
    before = _offered(agent, call_id)
    day, window = min(before)
    blocked = _post(agent, "/owner/schedule/block", day=day, window=window, next="/owner/schedule")
    assert blocked.status_code == 303 and blocked.headers["location"] == "/owner/schedule?edit=1"
    after = _offered(agent, call_id)
    assert (day, window) not in after
    assert after  # other windows are still offered


def test_taking_the_block_off_offers_the_window_again(agent, mock):
    sign_in(agent, "staff")
    call_id = ready_to_book(agent)
    day, window = min(_offered(agent, call_id))
    _post(agent, "/owner/schedule/block", day=day, window=window)
    conn = _mock_db(mock)
    try:
        ids = [r["id"] for r in conn.execute("SELECT id FROM non_job_appointments WHERE active = 1")]
    finally:
        conn.close()
    assert len(ids) > 1  # one block per free technician
    _post(agent, f"/owner/schedule/blocks/{ids[0]}/remove", day=day, group="1")
    assert (day, window) in _offered(agent, call_id)
    conn = _mock_db(mock)
    try:
        assert conn.execute("SELECT count(*) FROM non_job_appointments").fetchone()[0] == len(ids)
        assert conn.execute(
            "SELECT count(*) FROM non_job_appointments WHERE active = 1"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_a_customers_new_phone_number_is_recognized_on_the_next_call(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    assert agent.start(maria["phone"])["data"]["customers"]  # warm: known by the old number
    sign_in(agent, "admin")
    detail = schedule.customer_detail(
        agent.app.state.services.fsm["summit-air"], maria["customerId"]
    )
    contact = next(p for p in detail["phones"] if phone_key(p["value"]) == phone_key(maria["phone"]))
    saved = _post(
        agent, f"/owner/customers/{maria['customerId']}", contact=str(contact["id"]),
        phone=NEW_NUMBER, next="/owner/schedule",
    )
    assert saved.status_code == 303
    [customer] = agent.start(NEW_NUMBER)["data"]["customers"]
    assert customer["customer_id"] == maria["customerId"]
    assert agent.start(maria["phone"])["data"]["customers"] == []


def test_find_customer_sees_a_new_phone_number_at_once(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    sign_in(agent, "admin")
    detail = schedule.customer_detail(
        agent.app.state.services.fsm["summit-air"], maria["customerId"]
    )
    contact = detail["phones"][0]
    _post(agent, f"/owner/customers/{maria['customerId']}", contact=str(contact["id"]),
          phone=NEW_NUMBER)
    call_id = agent.start()["data"]["call_id"]
    found = agent.tool("find_customer", call_id, phone=NEW_NUMBER)
    assert found["ok"], found
    assert [c["customer_id"] for c in found["data"]["matches"]] == [maria["customerId"]]


def test_staff_cannot_change_a_phone_number(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    sign_in(agent, "staff")
    detail = schedule.customer_detail(
        agent.app.state.services.fsm["summit-air"], maria["customerId"]
    )
    refused = _post(
        agent, f"/owner/customers/{maria['customerId']}", contact=str(detail["phones"][0]["id"]),
        phone=NEW_NUMBER,
    )
    assert refused.status_code == 403
    assert agent.start(NEW_NUMBER)["data"]["customers"] == []
    assert _edits(agent) == []


def test_the_schedule_module_refuses_a_phone_change_from_staff_too(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    services = agent.app.state.services
    fsm, cache = services.fsm["summit-air"], services.caches["summit-air"]
    tenant = services.tenants.default
    staff = schedule.Who(None, "Sam", "staff", "owner")
    conn = agent.db()
    try:
        try:
            schedule.edit_customer(conn, fsm, cache, tenant, staff, services.clock(),
                                   maria["customerId"], name="Someone Else")
            raise AssertionError("staff changed a customer")
        except schedule.EditError as exc:
            assert "admin" in str(exc)
    finally:
        conn.close()
    [edit] = _edits(agent)
    assert (edit["ok"], edit["author"], edit["role"]) == (0, "Sam", "staff")


def test_every_edit_is_logged_with_who_made_it_and_where(agent):
    sign_in(agent, "staff", "dana")
    call_id = ready_to_book(agent)
    day, window = min(_offered(agent, call_id))
    _post(agent, "/owner/schedule/block", day=day, window=window, name="Team meeting",
          next="/owner/schedule")
    _post(agent, "/owner/schedule/block", day="2026-11-01", window="morning")  # already past
    [made, refused] = _edits(agent)
    assert (made["author"], made["role"], made["surface"], made["ok"]) == ("Dana", "staff", "owner", 1)
    assert "Team meeting" in made["detail"] and made["fsm_modified_on"]
    assert refused["ok"] == 0 and refused["error"] == "That window has already started."


def test_an_edit_from_the_bench_is_logged_as_the_bench(agent):
    sign_in(agent, "admin")
    call_id = ready_to_book(agent)
    day, window = min(_offered(agent, call_id))
    _post(agent, "/owner/schedule/block", day=day, window=window, next="/bench/schedule")
    assert _edits(agent)[0]["surface"] == "bench"


def test_a_job_added_on_the_owner_view_takes_the_window_in_servicetitan(agent, mock, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    sign_in(agent, "staff")
    call_id = ready_to_book(agent)
    day, window = min(_offered(agent, call_id))
    added = _post(agent, "/owner/schedule/jobs", day=day, window=window, phone=maria["phone"],
                  job_type="tune_up", notes="Side gate")
    assert added.status_code == 303
    [edit] = _edits(agent)
    assert edit["ok"] == 1, edit
    conn = _mock_db(mock)
    try:
        job = conn.execute(
            "SELECT * FROM jobs WHERE customer_id = ? ORDER BY id DESC LIMIT 1",
            (maria["customerId"],),
        ).fetchone()
        appt = conn.execute("SELECT * FROM appointments WHERE job_id = ?", (job["id"],)).fetchone()
    finally:
        conn.close()
    assert "Side gate" in job["summary"] and (appt["window_date"], appt["window_id"]) == (day, window)


def test_a_job_for_an_unknown_number_is_refused_with_a_reason(agent):
    sign_in(agent, "staff")
    call_id = ready_to_book(agent)
    day, window = min(_offered(agent, call_id))
    sent = _post(agent, "/owner/schedule/jobs", day=day, window=window, phone="(212) 555-0000",
                 job_type="tune_up")
    [edit] = _edits(agent)
    assert edit["error"] == "No customer has that phone number."
    assert "No customer has that phone number." in agent.client.get(sent.headers["location"]).text
    assert "No customer has that phone number." not in agent.client.get("/owner/schedule").text


def test_an_edits_result_shows_only_to_the_person_who_made_it(agent):
    sign_in(agent, "staff", "dana")
    call_id = ready_to_book(agent)
    day, window = min(_offered(agent, call_id))
    sent = _post(agent, "/owner/schedule/block", day=day, window=window, name="Team meeting")
    assert "Saved" in agent.client.get(sent.headers["location"]).text
    sign_in(agent, "staff", "lee")
    assert "Saved" not in agent.client.get(sent.headers["location"]).text


def test_a_job_changes_window_type_notes_and_address_and_keeps_its_reference(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    services = agent.app.state.services
    fsm = services.fsm["summit-air"]
    sign_in(agent, "staff")
    job = schedule.job_detail(fsm, services.tenants.default, maria["jobId"], services.clock())
    fsm.change_job("seed-ref", maria["jobId"], {"summary": "Old notes\nRef: 0123456789ab"})
    target = next(c["value"] for c in job["choices"] if c["value"] != job["window"])
    saved = _post(
        agent, f"/owner/jobs/{maria['jobId']}", window=target, job_type="safety_check",
        notes="Ring twice", street=job["address"]["street"], unit="4B",
        city=job["address"]["city"], zip=job["address"]["zip"],
    )
    assert saved.status_code == 303
    after = schedule.job_detail(fsm, services.tenants.default, maria["jobId"], services.clock())
    assert (after["window"], after["type"], after["notes"]) == (target, "safety_check", "Ring twice")
    assert after["address"]["unit"] == "4B"
    assert "Ref: 0123456789ab" in fsm.job(maria["jobId"])["summary"]
    [edit] = _edits(agent)
    assert "window" in edit["detail"] and "service location" in edit["detail"]


def test_a_moved_job_shows_in_its_new_window_on_the_next_call_start(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    services = agent.app.state.services
    sign_in(agent, "staff")
    job = schedule.job_detail(services.fsm["summit-air"], services.tenants.default,
                              maria["jobId"], services.clock())
    target = next(c for c in job["choices"] if c["value"].endswith("|morning"))
    _post(agent, f"/owner/jobs/{maria['jobId']}", window=target["value"])
    [customer] = agent.start(maria["phone"])["data"]["customers"]
    assert customer["open_jobs"][0]["window"]["label"] == "8 to 12"
    assert customer["open_jobs"][0]["window"]["date"] == target["value"].split("|")[0]


def test_the_schedule_page_shows_jobs_blocks_and_what_is_open(agent):
    sign_in(agent, "staff")
    call_id = ready_to_book(agent)
    day, window = min(_offered(agent, call_id))
    _post(agent, "/owner/schedule/block", day=day, window=window, name="Team meeting")
    page = agent.client.get(f"/owner/schedule?day={day}")
    assert page.status_code == 200
    assert "Team meeting" in page.text and "Block all" in page.text and "Add job" in page.text
    assert 'aria-current="page">Schedule' in page.text


def test_the_bench_schedule_is_for_admins_and_lists_what_changed(agent):
    sign_in(agent, "staff")
    assert agent.client.get("/bench/schedule").status_code == 403
    assert agent.client.get("/bench/schedule/changes").status_code == 403
    sign_in(agent, "admin")
    call_id = ready_to_book(agent)
    day, window = min(_offered(agent, call_id))
    _post(agent, "/owner/schedule/block", day=day, window=window, next="/bench/schedule")
    page = agent.client.get(f"/bench/schedule?day={day}")
    assert page.status_code == 200 and "Agent offers" in page.text
    items = agent.client.get("/bench/schedule/changes").json()["items"]
    assert items[0]["by"] == "Admin" and items[0]["where"] == "bench" and items[0]["landed_local"]


def test_the_bench_finds_a_customer_by_phone_without_the_number_in_the_url(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    sign_in(agent, "admin")
    found = _post(agent, "/bench/schedule/find", day="2026-11-03", phone=maria["phone"])
    location = found.headers["location"]
    assert f"customer={maria['customerId']}" in location
    assert phone_key(maria["phone"]) not in location
    page = agent.client.get(location)
    assert maria["phone"][-4:] in page.text
    missing = _post(agent, "/bench/schedule/find", day="2026-11-03", phone="(212) 555-0000")
    assert "missing=1" in missing.headers["location"]


def test_the_job_page_from_the_bench_needs_an_admin(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    sign_in(agent, "staff")
    assert agent.client.get(f"/owner/jobs/{maria['jobId']}").status_code == 200
    assert agent.client.get(
        f"/owner/jobs/{maria['jobId']}?back=/bench/schedule"
    ).status_code == 403


def test_an_edit_never_sends_the_browser_off_the_site(agent):
    sign_in(agent, "staff")
    sent = _post(agent, "/owner/schedule/block", day="2026-11-03", window="morning",
                 next="https://example.com/")
    assert sent.headers["location"].startswith("/owner?edit=")


def test_signed_out_edits_change_nothing(agent):
    sent = _post(agent, "/owner/schedule/block", day="2026-11-03", window="morning")
    assert sent.status_code == 401
    assert _edits(agent) == []


def test_every_write_clears_the_slot_cache(agent):
    services = agent.app.state.services
    cache = services.caches["summit-air"]
    sign_in(agent, "staff")
    call_id = ready_to_book(agent)
    day, window = min(_offered(agent, call_id))
    assert len(cache) > 0
    _post(agent, "/owner/schedule/block", day=day, window=window)
    assert len(cache) == 0


def test_a_whole_shop_block_takes_only_the_technicians_free_then(agent, mock):
    sign_in(agent, "staff")
    services = agent.app.state.services
    tenant = services.tenants.default
    on = date(2026, 11, 3)
    before = schedule.day(services.fsm["summit-air"], tenant, services.caches["summit-air"], on,
                          services.clock())
    col = [w["id"] for w in before["windows"]].index("morning")
    free = sum(r["cells"][col]["state"] == "free" for r in before["rows"])
    _post(agent, "/owner/schedule/block", day=on.isoformat(), window="morning")
    conn = _mock_db(mock)
    try:
        assert conn.execute("SELECT count(*) FROM non_job_appointments").fetchone()[0] == free
    finally:
        conn.close()
