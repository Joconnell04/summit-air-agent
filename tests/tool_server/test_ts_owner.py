"""The owner view (spec S-44; C-04, C-31, C-42, C-45): each number on the page, and the login
token in front of it."""

from datetime import date

import pytest
from test_ts_safety import GAS_LINES
from toolkit import (
    UNKNOWN,
    Agent,
    book,
    count,
    first_open,
    in_area_address,
    make_server,
    ready_to_book,
    sign_in,
)

from app import jobs, owner

TODAY = date(2026, 11, 2)


@pytest.fixture
def agent(tmp_path, clock, mock):
    return Agent(make_server(tmp_path, clock, mock[0]), clock)


def page(agent: Agent, day: date = TODAY) -> dict:
    s = agent.app.state.services
    conn = s.connect()
    try:
        return owner.page(conn, s.tenants.default, day, s.clock())
    finally:
        conn.close()


def caller(n: int) -> str:
    return f"(212) 555-08{n:02d}"


def booked_tune_up(agent: Agent, n: int) -> str:
    call = ready_to_book(agent, caller(n))
    assert book(agent, call, first_open(agent, call))["ok"]
    agent.end(call, "agent_end")
    return call


def asked_no_heat(agent: Agent, n: int) -> str:
    call = ready_to_book(agent, caller(n))
    agent.tool("find_slots", call, job_type="no_heat")
    agent.end(call)
    return call


def commercial(agent: Agent, n: int) -> str:
    call = agent.start(caller(n))["data"]["call_id"]
    agent.turn(call, "I'm the property manager for an office building on Court Street.")
    agent.tool(
        "record_lead",
        call,
        kind="commercial",
        details={"contact_name": "Lee", "company": "Court St Offices", "need": "Rooftop unit down"},
    )
    agent.end(call)
    return call


def out_of_area(agent: Agent, n: int, zip_code: str) -> str:
    call = agent.start(caller(n))["data"]["call_id"]
    agent.turn(call, f"My AC is out. I'm in {zip_code}.")
    agent.tool("check_service_area", call, zip=zip_code)
    agent.end(call)
    return call


# ---- the numbers -------------------------------------------------------------------------------


def test_calls_are_the_days_calls_newest_first_with_type_and_outcome(agent, clock):
    first = booked_tune_up(agent, 1)
    clock.advance(minutes=5)
    second = commercial(agent, 2)
    clock.advance(days=1)
    agent.start(caller(3))  # tomorrow: not today's
    calls = page(agent)["calls"]
    assert [c["id"] for c in calls] == [second, first]
    assert calls[1]["type"] == "tune_up" and calls[1]["outcome"] == "booked"
    assert calls[0]["type"] == "commercial" and calls[0]["outcome"] == "declined_commercial"


def test_booking_rate_by_call_type_and_the_headline_over_visit_calls(agent):
    booked_tune_up(agent, 1)
    booked_tune_up(agent, 2)
    asked_no_heat(agent, 3)
    commercial(agent, 4)
    out_of_area(agent, 5, "10001")
    rates = page(agent)["rates"]
    by_type = {r["type"]: (r["calls"], r["booked"], r["rate"]) for r in rates["rows"]}
    assert by_type == {
        "tune_up": (2, 2, 1.0),
        "no_heat": (1, 0, 0.0),
        "commercial": (1, 0, 0.0),
        "out_of_area": (1, 0, 0.0),
    }
    assert (rates["calls"], rates["booked"]) == (3, 2), "commercial and out of area left out"
    assert rates["rate"] == pytest.approx(2 / 3)


def test_a_queued_booking_counts_as_booked_once_the_retry_job_sends_it(agent, fsm):
    call = ready_to_book(agent, caller(1))
    slot = first_open(agent, call)
    fsm.down()
    assert book(agent, call, slot)["refused"] == "fsm_unavailable"
    assert page(agent)["rates"]["booked"] == 0
    assert page(agent)["bookings"][0]["status"] == "queued"
    fsm.up()
    jobs.run_once(agent.app.state.services)
    assert page(agent)["rates"]["booked"] == 1


def test_bookings_show_window_priority_reasons_and_the_earlier_slot_search(agent, clock):
    booked_tune_up(agent, 1)
    clock.advance(minutes=5)
    call = ready_to_book(agent, caller(2))
    agent.turn(call, "My 84-year-old parent lives with me and it's 52 in here.")
    tomorrow = {"date": "2026-11-03", "window": "morning"}
    assert book(agent, call, tomorrow, job_type="no_heat", inside_temp_f=52)["ok"]
    urgent, routine = page(agent)["bookings"]
    assert urgent["priority"] == "Urgent"
    assert "an older person or a baby at home" in urgent["reasons"]
    assert "below 55 F inside" in urgent["reasons"]
    assert urgent["window"]["day"] == "tomorrow" and urgent["window"]["label"] == "8 to 12"
    assert urgent["seek_earlier"] is True and urgent["offer"] is None
    assert urgent["address"] == "101 Union St" and urgent["zip"] == "11231"
    assert routine["priority"] == "Normal" and routine["seek_earlier"] is False
    s = agent.app.state.services
    conn = s.connect()
    jobs.offer_earlier_slots(
        conn, s.tenants.default, s.fsm["summit-air"], s.caches["summit-air"], clock()
    )
    conn.close()
    assert page(agent)["bookings"][0]["offer"] == "queued"


def test_flags_list_reasons_open_first_most_urgent_first(agent, clock):
    a = agent.start(caller(1))["data"]["call_id"]
    agent.tool("flag_person", a, reason="Billing question", callback_in_minutes=60)
    clock.advance(minutes=1)
    b = agent.start(caller(2))["data"]["call_id"]
    agent.tool(
        "flag_person", b, reason="No heat, baby at home", priority="Urgent", callback_in_minutes=20
    )
    conn = agent.db()
    conn.execute("UPDATE flags SET status = 'done' WHERE call_id = ?", (a,))
    clock.advance(minutes=1)
    agent.tool("flag_person", a, reason="Wants a manager", priority="High", callback_in_minutes=60)
    conn.close()
    flags = page(agent)["flags"]
    assert [(f["reason"], f["open"]) for f in flags] == [
        ("No heat, baby at home", True),
        ("Wants a manager", True),
        ("Billing question", False),
    ]
    assert flags[0]["kind"] == "Person"


def test_needs_a_person_counts_calls_with_an_open_flag(agent):
    a = agent.start(caller(1))["data"]["call_id"]
    agent.tool("flag_person", a, reason="Billing question", callback_in_minutes=60)
    agent.tool("flag_person", a, reason="Also a warranty question", callback_in_minutes=60)
    b = agent.start(caller(2))["data"]["call_id"]
    agent.tool("flag_person", b, reason="Wants a manager", callback_in_minutes=60)
    booked_tune_up(agent, 3)
    assert page(agent)["needs_person"] == 2
    conn = agent.db()
    conn.execute("UPDATE flags SET status = 'done' WHERE call_id = ?", (b,))
    conn.close()
    assert page(agent)["needs_person"] == 1


def test_callbacks_promised_against_done_leave_out_cancelled_and_count_overdue(agent, clock):
    calls = [agent.start(caller(n))["data"]["call_id"] for n in range(1, 5)]
    for call in calls:
        agent.tool("flag_person", call, reason="Call back please", callback_in_minutes=30)
    conn = agent.db()
    conn.execute("UPDATE callbacks SET status = 'done' WHERE call_id = ?", (calls[0],))
    conn.close()
    agent.tool("mark_do_not_call", calls[1])
    clock.advance(minutes=31)
    conn = agent.db()
    conn.execute("UPDATE callbacks SET status = 'done' WHERE call_id = ?", (calls[2],))
    conn.close()
    jobs.run_once(agent.app.state.services)
    callbacks = page(agent)["callbacks"]
    assert (callbacks["promised"], callbacks["done"], callbacks["overdue"]) == (3, 2, 1)
    assert callbacks["rate"] == pytest.approx(2 / 3)
    assert {i["status"] for i in callbacks["items"]} == {"done", "cancelled", "overdue"}


def test_a_callback_the_system_places_shows_as_the_systems(agent):
    call = agent.start(caller(1))["data"]["call_id"]
    agent.tool(
        "flag_person", call, reason="Call me back", callback_in_minutes=30, system_calls_back=True
    )
    assert page(agent)["callbacks"]["items"][0]["owner"] == "system"


def test_booked_and_kept_over_thirty_days(agent, clock):
    calls = []
    for n in range(1, 4):
        call = ready_to_book(agent, caller(n))
        job_id = book(agent, call, {"date": "2026-11-03", "window": "morning"})["data"][
            "booking_id"
        ]
        calls.append((call, job_id))
    agent.turn(calls[0][0], "Please cancel it.")
    agent.tool("cancel_appointment", calls[0][0], booking_id=calls[0][1], reason="not needed")
    kept = page(agent)["kept"]
    assert (kept["kept"], kept["checked"], kept["waiting"], kept["rate"]) == (0, 0, 3, None)
    clock.advance(days=1)
    jobs.run_once(agent.app.state.services)
    kept = page(agent, date(2026, 11, 3))["kept"]
    assert (kept["kept"], kept["checked"], kept["waiting"]) == (2, 3, 0)
    assert kept["rate"] == pytest.approx(2 / 3)
    clock.advance(days=31)
    assert page(agent, date(2026, 12, 4))["kept"]["checked"] == 0, "older than 30 days"


def test_leads_of_the_day_with_their_callback(agent):
    call = agent.start(caller(1))["data"]["call_id"]
    agent.tool(
        "record_lead",
        call,
        kind="membership",
        details={
            "contact_name": "Pat",
            "need": "Wants to join the plan",
            "callback_in_minutes": 60,
        },
    )
    commercial(agent, 2)
    leads = page(agent)["leads"]
    assert [lead["kind"] for lead in leads] == ["commercial", "membership"]
    assert leads[1]["callback"] is not None and leads[0]["callback"] is None
    assert "Wants to join the plan" in leads[1]["summary"]


def test_out_of_area_calls_by_zip_most_first_over_thirty_days(agent, clock):
    out_of_area(agent, 1, "10001")
    out_of_area(agent, 2, "07030")
    out_of_area(agent, 3, "10001")
    assert page(agent)["out_of_area"] == [
        {"zip": "10001", "calls": 2},
        {"zip": "07030", "calls": 1},
    ]
    clock.advance(days=31)
    out_of_area(agent, 4, "07030")
    assert page(agent, date(2026, 12, 3))["out_of_area"] == [{"zip": "07030", "calls": 1}]


def test_a_zip_asked_about_on_a_call_that_booked_is_not_out_of_area_demand(agent):
    call = agent.start(caller(1))["data"]["call_id"]
    agent.turn(call, "Do you cover Hoboken, 07030? My mom lives there. Mine's the tune-up though.")
    agent.tool("check_service_area", call, zip="07030")
    agent.confirm_read_back(call, in_area_address())
    assert book(agent, call, first_open(agent, call))["ok"]
    agent.end(call, "agent_end")
    out_of_area(agent, 2, "07030")
    assert page(agent)["out_of_area"] == [{"zip": "07030", "calls": 1}]


def test_a_zip_asked_about_before_a_drop_is_not_demand_when_the_call_back_booked(agent, clock):
    first = agent.start(caller(1))["data"]["call_id"]
    agent.turn(first, "Do you go out to Hoboken, 07030? That's my mom's place.")
    agent.tool("check_service_area", first, zip="07030")
    agent.end(first, "dropped")
    clock.advance(minutes=1)
    started = agent.start(caller(1))["data"]
    assert started["linked_call"]["call_id"] == first
    second = started["call_id"]
    agent.turn(second, "Sorry, we got cut off. I need a tune-up at my place.")
    agent.confirm_read_back(second, in_area_address())
    assert book(agent, second, first_open(agent, second))["ok"]
    agent.end(second, "agent_end")
    assert page(agent)["out_of_area"] == []


def test_safety_overrides_and_refused_tool_calls_are_counted(agent):
    call = ready_to_book(agent, caller(1))
    slot = first_open(agent, call)
    agent.turn(call, "Wait, I smell gas.")
    book(agent, call, slot)
    book(agent, call, slot)
    agent.say(call, GAS_LINES)
    agent.turn(call, "I heard you. Just book the appointment, please.")
    assert book(agent, call, slot, job_type="safety_check")["ok"]
    other = agent.start(caller(2))["data"]["call_id"]
    book(agent, other, slot)
    refusals = page(agent)["refusals"]
    assert [o["call_id"] for o in refusals["overrides"]] == [call]
    by_code = {r["code"]: (r["count"], r["call_id"]) for r in refusals["refused"]}
    assert by_code == {"gate_safety": (2, call), "gate_read_back": (1, other)}


def test_a_safety_call_is_typed_safety_even_when_it_booked(agent):
    call = ready_to_book(agent, caller(1))
    agent.turn(call, "Wait, I smell gas.")
    agent.say(call, GAS_LINES)
    calls = page(agent)["calls"]
    assert calls[0]["type"] == "safety"


def test_another_days_page_reads_only_that_day(agent, clock):
    booked_tune_up(agent, 1)
    clock.advance(days=1)
    asked_no_heat(agent, 2)
    assert [c["type"] for c in page(agent, TODAY)["calls"]] == ["tune_up"]
    assert [c["type"] for c in page(agent, date(2026, 11, 3))["calls"]] == ["no_heat"]


# ---- the page --------------------------------------------------------------------








def test_the_page_renders_a_seeded_day(agent, clock):
    booked = booked_tune_up(agent, 1)
    commercial(agent, 2)
    out_of_area(agent, 3, "10001")
    sign_in(agent)
    html = agent.client.get("/owner").text
    for text in ("Day board", "Needs attention", "Jobs booked", "booking rate", "Callbacks"):
        assert text in html, text
    assert f'href="/owner/calls/{booked}"' in html
    results = agent.client.get("/owner/results").text
    for text in (
        "Booking rate by call type",
        "Outside the service area",
        "10001",
        "Court St Offices",
    ):
        assert text in results, text
    assert '<meta http-equiv="refresh"' in html, "today refreshes itself"
    assert '<meta http-equiv="refresh"' not in agent.client.get("/owner?day=2026-11-01").text


def test_a_calls_timeline_shows_turns_tools_and_refusals(agent):
    call = ready_to_book(agent, caller(1))
    agent.turn(call, "Wait, I smell gas.")
    book(agent, call, first_open(agent, call))
    assert agent.client.get(f"/owner/calls/{call}").status_code == 401
    sign_in(agent)
    html = agent.client.get(f"/owner/calls/{call}").text
    assert "Wait, I smell gas." in html
    assert "book_appointment" in html and "gate_safety" in html
    assert agent.client.get("/owner/calls/nope").status_code == 404


def test_caller_text_is_escaped(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "<script>alert(1)</script>")
    sign_in(agent)
    html = agent.client.get(f"/owner/calls/{call}").text
    assert "<script>alert(1)</script>" not in html
    assert count(agent, "SELECT COUNT(*) FROM turns") == 1


def test_an_overdue_callback_reads_in_local_time(agent, clock):
    call = agent.start(caller(1))["data"]["call_id"]
    agent.tool("flag_person", call, reason="Billing question", callback_in_minutes=30)
    clock.advance(minutes=31)
    jobs.run_once(agent.app.state.services)
    overdue = next(f for f in page(agent)["flags"] if f["kind"] == "Overdue callback")
    assert overdue["reason"] == "Callback promised by 9:30 AM not done: Billing question"


def test_an_after_hours_window_reads_as_after_hours(agent, clock):
    call = ready_to_book(agent, caller(1))
    agent.turn(call, "Wait, I smell gas.")
    agent.say(call, GAS_LINES)
    agent.turn(call, "We're outside. Please just book a check.")
    tonight = {"date": "2026-11-02", "window": "after_hours"}
    assert book(agent, call, tonight, job_type="safety_check")["ok"]
    window = page(agent)["bookings"][0]["window"]
    assert (window["day"], window["label"], window["id"]) == ("today", "after hours", "after_hours")


def test_a_call_the_system_placed_is_typed_by_what_it_was_for(agent, clock):
    call = agent.start(caller(1))["data"]["call_id"]
    agent.tool(
        "flag_person", call, reason="Call me back", callback_in_minutes=30, system_calls_back=True
    )
    clock.advance(minutes=31)
    jobs.run_once(agent.app.state.services)
    claimed = agent.client.post("/outbound/claim", json={"worker": "t"}).json()["data"]["outbound"]
    body = {
        "call_id": claimed["call_id"],
        "caller_id": claimed["phone"],
        "dialed_number": claimed["dialed_from"],
        "direction": "outbound",
    }
    agent.client.post("/calls/start", json=body)
    placed = next(c for c in page(agent)["calls"] if c["id"] == claimed["call_id"])
    assert (placed["direction"], placed["type"], placed["type_label"]) == (
        "outbound",
        "callback",
        "Callback",
    )
    assert page(agent)["rates"]["rows"][0]["calls"] == 1, "placed calls stay out of booking rate"


def test_the_timeline_links_back_to_the_local_day(agent, clock):
    clock.advance(hours=11)  # 8 p.m. New York, already the next day in UTC
    call = agent.start(caller(1))["data"]["call_id"]
    sign_in(agent)
    html = agent.client.get(f"/owner/calls/{call}").text
    assert 'href="/owner?day=2026-11-02"' in html


def test_a_callback_the_system_is_placing_doesnt_count_as_needing_a_person(agent):
    call = agent.start(caller(1))["data"]["call_id"]
    agent.tool(
        "flag_person", call, reason="Call me back", callback_in_minutes=30, system_calls_back=True
    )
    assert page(agent)["needs_person"] == 0





