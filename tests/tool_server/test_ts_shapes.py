"""The adapter speaks ServiceTitan's real shapes, and the company's rules sit on top of them in the
tool server (docs/explorations/006-servicetitan-field-names.md)."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import httpx
import pytest
from toolkit import (
    TENANT_DIR,
    UNKNOWN,
    book,
    count,
    first_open,
    in_area_address,
    ready_to_book,
)

from app import capacity, jobs
from app.db import iso
from app.fsm import FsmRejected
from app.fsm.servicetitan import ServiceTitanAdapter
from app.tenant import Tenant

TENANT = Tenant.load(TENANT_DIR)
TUESDAY, SATURDAY = date(2026, 11, 3), date(2026, 11, 7)


def frame(day, window_id, technicians, unit=11) -> dict:
    """A capacity time frame with technicians given as (id, status, hasRequiredSkills)."""
    start, end = TENANT.window_bounds(day, TENANT.window(window_id))
    return {
        "startUtc": iso(start),
        "endUtc": iso(end),
        "businessUnitIds": [unit],
        "technicians": [
            {"id": i, "name": f"T{i}", "status": s, "hasRequiredSkills": h}
            for i, s, h in technicians
        ],
    }


FREE = "Available"
BUSY = "Unavailable"


# ---- the company's rules on top of capacity ---------------------------------------------------


def usable(day, window, technicians, level, unit=11):
    f = frame(day, window, technicians, unit)
    return capacity.usable_technicians(TENANT, f, day, TENANT.window(window), level)


def test_an_ordinary_job_leaves_the_held_technicians_free(agent):
    three = [(1, FREE, True), (2, FREE, True), (3, FREE, True)]
    assert TENANT.hold_for(TUESDAY, "0800-1000", 11) == 2
    assert usable(TUESDAY, "0800-1000", three, "Normal") == [1], "three free, two held: one usable"
    assert usable(TUESDAY, "0800-1000", three[:2], "Normal") == []
    assert usable(TUESDAY, "0800-1000", three, "High") == [1]


def test_an_urgent_job_may_take_any_free_technician_with_the_skills(agent):
    mixed = [(1, FREE, True), (2, FREE, False), (3, BUSY, True), (4, FREE, True)]
    assert usable(TUESDAY, "0800-1000", mixed, "Urgent") == [1, 4]


def test_the_hold_counts_free_technicians_whatever_their_skills(agent):
    one_fit = [(1, FREE, True), (2, FREE, False), (3, FREE, False)]
    assert usable(TUESDAY, "0800-1000", one_fit, "Normal") == [1]
    two = [(1, FREE, True), (2, FREE, False)]
    assert usable(TUESDAY, "0800-1000", two, "Normal") == []


def test_booked_technicians_are_not_free_and_not_counted(agent):
    some_busy = [(1, BUSY, True), (2, FREE, True), (3, FREE, True), (4, FREE, True)]
    assert usable(TUESDAY, "0800-1000", some_busy, "Normal") == [2]


def test_the_hold_follows_the_day_and_the_business_unit(agent):
    two = [(1, FREE, True), (2, FREE, True)]
    assert usable(SATURDAY, "0800-1000", two, "Normal") == [1], "a Saturday holds one"
    assert usable(TUESDAY, "0800-1000", two, "Normal", unit=12) == [1, 2], "installs hold none"


def test_the_night_window_is_for_urgent_jobs_only(agent):
    on_call = [(5, FREE, True)]
    assert usable(TUESDAY, "after_hours", on_call, "Normal") == []
    assert usable(TUESDAY, "after_hours", on_call, "High") == []
    assert usable(TUESDAY, "after_hours", on_call, "Urgent") == [5]


def test_open_windows_keeps_only_the_tenants_windows_in_range_and_in_time_order(agent):
    plenty = [(i, FREE, True) for i in range(1, 6)]
    wednesday = date(2026, 11, 4)
    odd = frame(TUESDAY, "0800-1000", plenty)
    odd["startUtc"], odd["endUtc"] = "2026-11-03T14:00:00Z", "2026-11-03T15:00:00Z"
    frames = [
        frame(wednesday, "0800-1000", plenty),
        frame(TUESDAY, "1200-1400", plenty),
        frame(TUESDAY, "0800-1000", plenty),
        frame(date(2026, 11, 20), "0800-1000", plenty),
        odd,
    ]
    found = capacity.open_windows(TENANT, frames, "Normal", TUESDAY, wednesday, minutes=120)
    assert [(w["date"], w["window"]) for w in found] == [
        ("2026-11-03", "0800-1000"),
        ("2026-11-03", "1200-1400"),
        ("2026-11-04", "0800-1000"),
    ]
    assert found[0]["technician_ids"] == [1, 2, 3]
    assert set(found[0]) == {"date", "window", "after_hours", "start", "end", "technician_ids"}
    assert found[0]["start"].endswith("Z") and found[0]["after_hours"] is False


def test_open_windows_depends_on_the_priority(agent):
    two = [(1, FREE, True), (2, FREE, True)]
    frames = [frame(TUESDAY, "0800-1000", two), frame(TUESDAY, "after_hours", [(5, FREE, True)])]
    assert capacity.open_windows(TENANT, frames, "Normal", TUESDAY, TUESDAY, minutes=120) == []
    urgent = capacity.open_windows(TENANT, frames, "Urgent", TUESDAY, TUESDAY, minutes=120)
    assert [(w["window"], w["after_hours"]) for w in urgent] == [
        ("0800-1000", False),
        ("after_hours", True),
    ]


def test_pick_technician_reads_the_exact_window_and_applies_the_rules(agent):
    start, end = TENANT.window_bounds(TUESDAY, TENANT.window("0800-1000"))
    three = [(1, FREE, True), (2, FREE, True), (3, FREE, True)]
    frames = [frame(TUESDAY, "0800-1000", three), frame(TUESDAY, "1200-1400", [(9, FREE, True)])]
    assert capacity.pick_technician(TENANT, frames, start, end, "Normal") == 1, (
        "the others are held"
    )
    picked = {capacity.pick_technician(TENANT, frames, start, end, "Urgent") for _ in range(60)}
    assert picked == {1, 2, 3}, "an urgent job may take any, and the pick is spread over them"
    assert capacity.pick_technician(TENANT, frames[1:], start, end, "Urgent") is None
    taken = [frame(TUESDAY, "0800-1000", [(1, BUSY, True), (2, FREE, True), (3, FREE, True)])]
    assert capacity.pick_technician(TENANT, taken, start, end, "Normal") is None
    assert capacity.pick_technician(TENANT, taken, start, end, "Urgent") in (2, 3)


# ---- the adapter ------------------------------------------------------------------------------


class Recorder:
    """Wraps the adapter's HTTP client to keep every request it makes."""

    def __init__(self, adapter):
        self.adapter, self.seen = adapter, []
        original = adapter.http.request

        def request(method, url, **kwargs):
            self.seen.append((method, url, kwargs))
            return original(method, url, **kwargs)

        adapter.http.request = request

    def to(self, method, suffix):
        return [k for m, u, k in self.seen if m == method and u.endswith(suffix)]


@pytest.fixture
def recorder(agent):
    return Recorder(agent.app.state.services.fsm["summit-air"])


def test_the_adapter_never_sends_an_idempotency_key_or_a_debug_header(agent, recorder):
    call = ready_to_book(agent)
    assert book(agent, call, first_open(agent, call), key="the-agents-key")["ok"]
    assert recorder.seen
    for _, url, kwargs in recorder.seen:
        assert "Idempotency-Key" not in (kwargs.get("headers") or {}), url
        assert "X-Call-Id" not in (kwargs.get("headers") or {}), url
        assert not url.endswith(("/company-profile", "/health")), url


def test_capacity_is_asked_the_real_way_and_the_job_is_created_the_real_way(agent, recorder):
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    assert book(agent, call, slot)["ok"]
    capacity_body = recorder.to("POST", "/capacity")[0]["json"]
    assert set(capacity_body) == {
        "startsOnOrAfter",
        "endsOnOrBefore",
        "businessUnitIds",
        "jobTypeId",
        "skillBasedAvailability",
    }
    assert capacity_body["skillBasedAvailability"] is True
    job = recorder.to("POST", "/jobs")[0]["json"]
    appointment = job["appointments"][0]
    assert {"start", "end", "arrivalWindowStart", "arrivalWindowEnd"} <= set(appointment)
    assert appointment["start"] == appointment["arrivalWindowStart"]
    assert len(appointment["technicianIds"]) == 1, "the technician is named, not left to the mock"
    assert {"priority", "campaignId", "summary"} <= set(job)
    assert "\nRef: " in job["summary"], "a person can find the attempt from the summary"


def test_a_job_made_before_the_answer_was_lost_is_found_again_by_its_reference(agent, fsm, mock):
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    fsm.losing_job_responses()
    assert book(agent, call, slot, key="k1")["refused"] == "fsm_unavailable"
    fsm.up()
    adapter = agent.app.state.services.fsm["summit-air"]
    attempt = agent.db().execute("SELECT fsm_key FROM bookings").fetchone()["fsm_key"]
    made = (
        sqlite3.connect(mock[0].state.db_path)
        .execute("SELECT id, customer_id FROM jobs ORDER BY id DESC LIMIT 1")
        .fetchone()
    )
    found = adapter.find_job(made[1], f"{attempt}:job")
    assert found and found["id"] == made[0]
    assert adapter.find_job(made[1], "some other attempt:job") is None


def test_a_lost_answer_does_not_make_a_second_job_when_the_booking_is_retried(agent, fsm, mock):
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    db = sqlite3.connect(mock[0].state.db_path)
    jobs_before = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    fsm.losing_job_responses()
    assert book(agent, call, slot, key="k2")["refused"] == "fsm_unavailable"
    fsm.up()
    report = jobs.run_once(agent.app.state.services)
    assert report["summit-air"]["queue"]["booked"] == 1
    assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == jobs_before + 1
    assert db.execute("SELECT COUNT(*) FROM jobs WHERE summary LIKE '%Ref: %'").fetchone()[0] == 1


def test_a_conflict_is_slot_taken_a_missing_job_is_not_found_and_anything_else_is_rejected(
    agent, scenarios
):
    adapter = agent.app.state.services.fsm["summit-air"]
    with pytest.raises(FsmRejected) as missing:
        adapter.job(999999)
    assert (missing.value.status, missing.value.code) == (404, "not_found")
    with pytest.raises(FsmRejected) as bad:
        adapter.create_lead("k", {"campaignId": 301, "summary": "x", "callReasonId": 4242})
    assert (bad.value.status, bad.value.code) == (422, "rejected")
    assert bad.value.detail, "the problem title is the detail"
    # The same technician twice in one window is a conflict (409), which the tools read as slot_taken.
    start, end = TENANT.window_bounds(TUESDAY, TENANT.window("1200-1400"))
    frames = adapter.capacity(start, end, 11, 203)
    technician = next(
        t["id"]
        for t in frames[0]["technicians"]
        if t["status"] == "Available" and t["hasRequiredSkills"]
    )
    person = scenarios["jobDoneLastWeek"]
    args = (person["customerId"], person["locationId"], 11, 203, "Normal", 301, "x", start, end)
    adapter.create_job("a", *args, [technician])
    with pytest.raises(FsmRejected) as conflict:
        adapter.create_job("b", *args, [technician])
    assert (conflict.value.status, conflict.value.code) == (409, "slot_taken")


def test_the_job_list_is_filtered_by_status_on_our_side(agent, scenarios):
    adapter = agent.app.state.services.fsm["summit-air"]
    maria = scenarios["repeatCallerOpenJob"]
    assert [j["id"] for j in adapter.jobs(maria["customerId"], ["Scheduled", "Dispatched"])] == [
        maria["jobId"]
    ]
    assert adapter.jobs(maria["customerId"], ["Completed"]) == []
    assert maria["jobId"] in [j["id"] for j in adapter.jobs(maria["customerId"])]


def test_contacts_come_from_their_own_call_and_the_customer_has_none_inline(agent, scenarios):
    adapter = agent.app.state.services.fsm["summit-air"]
    park = scenarios["activeMemberWithAdultChild"]
    assert "contacts" not in adapter.customer(park["customerId"])
    contacts = adapter.contacts(park["customerId"])
    assert park["adultChildPhone"] in [c["value"] for c in contacts]
    assert {"id", "type", "value", "memo"} <= set(contacts[0])


def test_the_adapter_signs_in_to_report_health_and_needs_no_health_route(agent, fsm):
    adapter = agent.app.state.services.fsm["summit-air"]
    assert adapter.health() is True
    fsm.down()
    adapter._token = ""  # a fresh sign-in is needed, and the connection is down
    assert adapter.health() is False


def test_a_lead_goes_out_with_servicetitans_field_names(agent, mock, recorder):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "We're thinking about a heat pump.")
    lead = agent.tool(
        "record_lead",
        call,
        kind="install",
        details={"contact_name": "Sam", "need": "Heat pump", "callback_in_minutes": 60},
    )
    assert lead["ok"]
    sent = recorder.to("POST", "/leads")[0]["json"]
    assert set(sent) == {
        "callReasonId",
        "customerId",
        "leadCustomerName",
        "leadPhone",
        "summary",
        "campaignId",
    }
    assert (sent["callReasonId"], sent["campaignId"], sent["leadCustomerName"]) == (702, 301, "Sam")


# ---- what the model sees ----------------------------------------------------------------------


def test_who_a_number_belongs_to_comes_from_the_contact_memo(agent, scenarios):
    park = scenarios["activeMemberWithAdultChild"]
    call = agent.start(park["adultChildPhone"])["data"]
    match = call["customers"][0]
    mine = next(p for p in match["phones"] if p["number"].endswith(park["adultChildPhone"][-4:]))
    assert mine["memo"] == "Adult child: David Park"
    assert not {"role", "name"} & set(mine)


def test_recent_calls_read_from_the_nested_call_feed(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    started = agent.start(maria["phone"])["data"]
    calls = started["customers"][0]["recent_calls"]
    assert len(calls) == 3
    assert {"at", "reason", "call_type"} == set(calls[0])
    assert {c["reason"] for c in calls} == {"No heat"}
    assert {c["call_type"] for c in calls} <= {"Booked", "Unbooked"}
    assert calls[0]["at"] >= calls[-1]["at"], "newest first"


def test_equipment_reads_the_type_object_and_the_install_date(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    started = agent.start(maria["phone"])["data"]
    system = started["customers"][0]["equipment"][0]
    assert set(system) == {"system", "brand", "install_year"}
    assert isinstance(system["install_year"], int) and system["system"] in {"boiler", "furnace"}


def test_note_authors_are_told_apart_by_user_id(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.tool("add_note", call, key="n", text="Daughter Ana usually answers.")
    other = agent.start(maria["phone"], call_id="second")["data"]["customers"][0]["notes"]
    by = {n["text"]: n["by"] for n in other}
    assert by["Daughter Ana usually answers."] == "agent"
    assert set(by.values()) == {"agent", "staff"}


def test_an_agent_job_note_is_kept_on_the_job_and_has_no_id(agent):
    call = ready_to_book(agent, UNKNOWN, in_area_address())
    assert book(agent, call, first_open(agent, call))["ok"]
    noted = agent.tool("add_note", call, text="Gate code 4417.", attach_to="job")
    assert noted["data"]["stored_in"] == "job"
    assert count(agent, "SELECT fsm_note_id FROM notes WHERE attach_to = 'job'") is None


# ---- bookings at the same moment, windows that have gone, long lists ---------------------------


def test_callers_booking_one_window_together_all_get_it_while_technicians_are_free(
    agent, monkeypatch
):
    """Everyone reaches for the first technician, so they collide, and each one who loses looks
    again and takes the next. Only a window with no one left is a taken slot."""
    monkeypatch.setattr(capacity.random, "choice", lambda technicians: technicians[0])
    adapter = agent.app.state.services.fsm["summit-air"]
    window = TENANT.window("1200-1400")
    slot = None
    for offset in range(3, 12):
        day = date(2026, 11, 2) + timedelta(days=offset)
        start, end = TENANT.window_bounds(day, window)
        frames = adapter.capacity(start, end, 11, 203)
        if (
            frames
            and len(capacity.usable_technicians(TENANT, frames[0], day, window, "Normal")) >= 4
        ):
            slot = {"date": day.isoformat(), "window": window.id}
            break
    assert slot, "the seeded world has a quiet window"
    calls = [ready_to_book(agent, f"(212) 555-09{10 + i}", in_area_address(i)) for i in range(3)]
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda c: book(agent, c, slot), calls))
    assert [r["ok"] for r in results] == [True, True, True], results


def test_a_window_that_has_started_is_offered_back_as_a_taken_slot_with_other_times(agent):
    call = ready_to_book(agent)
    started = book(agent, call, {"date": "2026-11-02", "window": "0800-1000"})  # it is 9 a.m.
    assert started["refused"] == "slot_taken"
    assert started["data"]["alternatives"], "other windows come back"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'booking_failed'") == 0
    assert count(agent, "SELECT COUNT(*) FROM callbacks") == 0, "nobody is promised a callback"
    assert book(agent, call, first_open(agent, call))["ok"], "and the call can still book"


def test_moving_a_visit_into_a_window_that_has_started_offers_other_times(agent):
    call = ready_to_book(agent)
    job_id = book(agent, call, first_open(agent, call))["data"]["booking_id"]
    started = agent.tool(
        "reschedule_appointment",
        call,
        booking_id=job_id,
        slot={"date": "2026-11-02", "window": "0800-1000"},  # it is 9 a.m.
        reason="works better",
    )
    assert started["refused"] == "slot_taken"
    assert started["data"]["alternatives"], "other windows come back"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'reschedule_failed'") == 0
    assert count(agent, "SELECT COUNT(*) FROM callbacks") == 0, "nobody is promised a callback"
    later = {"date": "2026-11-06", "window": "0800-1000"}
    moved = agent.tool(
        "reschedule_appointment", call, booking_id=job_id, slot=later, reason="works better"
    )
    assert moved["ok"], "the refused window didn't use up the one move"


def test_a_job_of_a_type_no_longer_booked_can_still_be_moved(agent, monkeypatch):
    call = ready_to_book(agent)
    job_id = book(agent, call, first_open(agent, call))["data"]["booking_id"]
    tenant = agent.app.state.services.tenants.default
    for code in list(tenant.job_types):
        monkeypatch.delitem(tenant.job_types, code)
    started = agent.tool(
        "reschedule_appointment",
        call,
        booking_id=job_id,
        slot={"date": "2026-11-02", "window": "0800-1000"},  # it is 9 a.m.
        reason="works better",
    )
    assert started["refused"] == "slot_taken" and started["data"]["alternatives"] == []
    later = {"date": "2026-11-06", "window": "0800-1000"}
    moved = agent.tool(
        "reschedule_appointment", call, booking_id=job_id, slot=later, reason="works better"
    )
    assert moved["ok"], moved


def test_a_list_is_read_page_by_page_until_it_runs_out():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/connect/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 900})
        page = int(request.url.params["page"])
        seen.append((page, request.url.params["pageSize"]))
        return httpx.Response(
            200,
            json={
                "data": [{"id": page * 10 + n, "jobStatus": "Scheduled"} for n in range(2)],
                "hasMore": page < 3,
            },
        )

    adapter = ServiceTitanAdapter(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="http://st"),
        1000,
        "id",
        "secret",
        "key",
    )
    assert [job["id"] for job in adapter.jobs(5)] == [10, 11, 20, 21, 30, 31]
    assert seen == [(1, "200"), (2, "200"), (3, "200")]


def blocks(day, start_hour, count, technicians, unit=11) -> list[dict]:
    """Consecutive half-hour block frames from start_hour."""
    from datetime import time

    out = []
    for n in range(count):
        minutes = start_hour * 60 + 30 * n
        s, e = time(minutes // 60, minutes % 60), time((minutes + 30) // 60, (minutes + 30) % 60)
        out.append(frame(day, f"{s:%H%M}-{e:%H%M}", technicians, unit))
    return out


def test_a_window_is_a_run_of_blocks_a_technician_is_free_in_all_of(agent):
    """Windows became any run of 30-minute blocks (Jackson after Retell call
    call_9826ff9f0dfa8bc835a3601368b)."""
    free = [(i, FREE, True) for i in range(1, 5)]
    frames = blocks(TUESDAY, 8, 4, free)
    frames[3]["technicians"][0]["status"] = BUSY  # T1 is busy 9:30 to 10
    found = capacity.open_windows(TENANT, frames, "Urgent", TUESDAY, TUESDAY, minutes=120)
    assert [(w["window"], w["technician_ids"]) for w in found] == [("0800-1000", [2, 3, 4])]
    hour = capacity.open_windows(TENANT, frames, "Urgent", TUESDAY, TUESDAY, minutes=60)
    assert [w["window"] for w in hour] == ["0800-0900", "0830-0930", "0900-1000"]


def test_a_window_from_a_named_time_is_cut_short_at_closing(agent):
    free = [(i, FREE, True) for i in range(1, 5)]
    frames = blocks(TUESDAY, 18, 4, free)
    from datetime import time

    found = capacity.open_windows(TENANT, frames, "Normal", TUESDAY, TUESDAY, start_at=time(19, 30))
    assert [w["window"] for w in found] == ["1930-2000"]
    assert TENANT.window("1930-2000").label() == "7:30 to 8"


def test_the_urgent_hold_applies_in_every_block(agent):
    three = [(1, FREE, True), (2, FREE, True), (3, FREE, True)]
    frames = blocks(TUESDAY, 8, 4, three)
    frames[2]["technicians"][2]["status"] = BUSY  # one fewer free at 9 to 9:30
    assert capacity.open_windows(TENANT, frames, "Normal", TUESDAY, TUESDAY, minutes=120) == []
