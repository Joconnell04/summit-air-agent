"""Booking gates: read-back, idempotency and the race, commercial, out of area, intake fields,
caller ID shorthand, reschedule and cancel ownership (spec S-05, S-13, S-15, S-25 to S-28, S-31)."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from toolkit import DETAILS, UNKNOWN, book, count, first_open, in_area_address, ready_to_book

FULL_DAY = {"date": "2026-11-04", "window": "morning"}  # nearly-full day: no normal slots
ONE_LEFT = {"date": "2026-11-05", "window": "afternoon"}  # exactly one normal slot open


def fsm_jobs(mock) -> int:
    conn = sqlite3.connect(mock[0].state.db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    finally:
        conn.close()


def test_a_happy_booking_creates_one_job_with_the_intake_fields(agent, mock):
    before = fsm_jobs(mock)
    call = ready_to_book(agent)
    booked = book(agent, call, first_open(agent, call), arrival_notes="Side door, small dog")
    assert booked["ok"], booked
    assert fsm_jobs(mock) == before + 1
    summary = (
        sqlite3.connect(mock[0].state.db_path)
        .execute("SELECT summary FROM jobs WHERE id = ?", (booked["data"]["booking_id"],))
        .fetchone()[0]
    )
    for label in (
        "Issue:",
        "Since:",
        "Address: 101 Union St, 11231",
        "Contact: (212) 555-0999",
        "System: gas boiler",
        "Tried:",
        "Home:",
        "Approves:",
        "On arrival: Side door, small dog",
        "Priority:",
    ):
        assert label in summary, label


def test_booking_is_refused_until_the_read_back_was_returned_and_answered(agent, mock):
    before = fsm_jobs(mock)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    slot = first_open(agent, call)
    refused = book(agent, call, slot)
    assert refused["refused"] == "gate_read_back"

    # confirmed: true before the sentence was ever returned does nothing
    skipped = agent.tool(
        "read_back", call, address=in_area_address(), phone="caller_id", confirmed=True
    )
    assert skipped["refused"] == "gate_read_back"
    # and right after it was returned, with no caller line in between, does nothing either
    asked = agent.tool("read_back", call, address=in_area_address(), phone="caller_id")
    too_soon = agent.tool(
        "read_back", call, address=in_area_address(), phone="caller_id", confirmed=True
    )
    assert too_soon["refused"] == "gate_read_back"
    assert book(agent, call, slot)["refused"] == "gate_read_back"
    assert fsm_jobs(mock) == before

    agent.say(call, asked["say"])
    agent.turn(call, "Yep.")
    assert agent.tool(
        "read_back", call, address=in_area_address(), phone="caller_id", confirmed=True
    )["ok"]
    assert book(agent, call, slot)["ok"]


def test_a_correction_after_the_read_back_needs_a_new_read_back(agent):
    call = ready_to_book(agent)
    corrected = agent.tool("read_back", call, address=in_area_address(7), phone="caller_id")
    assert corrected["ok"] and "107 Union St" in corrected["say"]
    assert book(agent, call, first_open(agent, call))["refused"] == "gate_read_back"


def test_the_read_back_sentence_reads_the_number_in_groups(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    asked = agent.tool("read_back", call, address=in_area_address(), phone="caller_id")
    assert asked["say"] == (
        "Let me read that back: 101 Union St, Brooklyn, 11231, and the best number is "
        "212, 555, 0999. Is that right?"
    )


def test_the_number_im_calling_from_is_the_real_caller_id(agent):
    call = agent.start("+1 (347) 555-0101")["data"]["call_id"]
    asked = agent.tool("read_back", call, address=in_area_address(), phone="caller_id")
    assert "347, 555, 0101" in asked["say"]


def test_the_one_ending_in_picks_the_matching_number_on_file(agent, scenarios):
    park = scenarios["activeMemberWithAdultChild"]  # two numbers on file
    call = agent.start(park["phone"])["data"]["call_id"]
    ending = park["adultChildPhone"][-4:]
    asked = agent.tool("read_back", call, address=in_area_address(), phone=ending)
    digits = "".join(c for c in park["adultChildPhone"] if c.isdigit())
    assert f"{digits[:3]}, {digits[3:6]}, {digits[6:]}" in asked["say"]
    unknown = agent.tool("read_back", call, address=in_area_address(), phone="9999")
    assert unknown["refused"] == "invalid_phone"


def test_a_retried_booking_returns_the_first_result(agent, mock):
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    first = book(agent, call, slot, key="k-1")
    again = book(agent, call, slot, key="k-1")
    assert first == again
    assert first["ok"]
    assert count(agent, "SELECT COUNT(*) FROM bookings WHERE kind = 'book'") == 1


def test_eight_parallel_retries_of_one_booking_book_once(agent, mock):
    before = fsm_jobs(mock)
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: book(agent, call, slot, key="same-key"), range(8)))
    assert all(r == results[0] for r in results)
    assert results[0]["ok"]
    assert fsm_jobs(mock) == before + 1


def test_eight_callers_racing_for_the_last_slot_get_it_once(agent, mock):
    calls = [ready_to_book(agent, f"(212) 555-09{10 + i}", in_area_address(i)) for i in range(8)]
    before = fsm_jobs(mock)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda c: book(agent, c, ONE_LEFT), calls))
    wins = [r for r in results if r["ok"]]
    assert len(wins) == 1
    assert {r["refused"] for r in results if not r["ok"]} == {"slot_taken"}
    assert fsm_jobs(mock) == before + 1
    loser = next(r for r in results if not r["ok"])
    assert loser["data"]["alternatives"], "a taken slot comes back with windows to offer"


def test_a_second_booking_on_one_call_is_refused_and_flags_a_person(agent):
    call = ready_to_book(agent)
    assert book(agent, call, first_open(agent, call))["ok"]
    second = book(agent, call, ONE_LEFT)
    assert second["refused"] == "limit"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'limit'") == 1


def test_a_commercial_account_cannot_book_whatever_the_agent_asks(agent, mock, scenarios):
    before = fsm_jobs(mock)
    for account in scenarios["commercialAccounts"]:
        call = ready_to_book(agent, account["phone"])
        refused = book(agent, call, first_open(agent, call), job_type="no_heat")
        assert refused["refused"] == "customer_type", account["name"]
        assert "only works on homes" in refused["say"]
    assert fsm_jobs(mock) == before


def test_a_caller_who_said_they_are_a_business_cannot_book(agent, mock):
    call = ready_to_book(agent)
    lead = agent.tool(
        "record_lead",
        call,
        kind="commercial",
        details={"contact_name": "Sam", "company": "Sam's Deli", "need": "Walk-in cooler is warm"},
    )
    assert lead["ok"]
    assert book(agent, call, first_open(agent, call))["refused"] == "customer_type"


def test_a_commercial_customer_found_by_address_is_refused_by_the_fsm_check(agent, mock, scenarios):
    school = next(a for a in scenarios["commercialAccounts"] if a["kind"] == "school")
    conn = sqlite3.connect(mock[0].state.db_path)
    street, zip_code = conn.execute(
        "SELECT street, zip FROM locations WHERE id = ?", (school["locationId"],)
    ).fetchone()
    before = fsm_jobs(mock)
    call = ready_to_book(
        agent, UNKNOWN, {"street": street, "unit": "", "city": "", "zip": zip_code}
    )
    refused = book(agent, call, first_open(agent, call))
    assert refused["refused"] == "customer_type"
    assert fsm_jobs(mock) == before


def test_out_of_area_is_refused_and_recorded_with_its_zip(agent, mock, scenarios):
    before = fsm_jobs(mock)
    for resident in scenarios["outOfAreaResidents"]:
        call = agent.start(resident["phone"])["data"]["call_id"]
        checked = agent.tool("check_service_area", call, zip=resident["zip"])
        assert checked["refused"] == "out_of_area"
        assert "outside the area we cover" in checked["say"]
        agent.confirm_read_back(
            call, {"street": "1 Main St", "unit": "", "city": "", "zip": resident["zip"]}
        )
        assert book(agent, call, ONE_LEFT)["refused"] == "out_of_area"
    zips = count(agent, "SELECT COUNT(DISTINCT zip) FROM out_of_area")
    assert zips == len({r["zip"] for r in scenarios["outOfAreaResidents"]})
    # No supervisor flag for an out-of-area call: turning them away is normal practice.
    assert count(agent, "SELECT COUNT(*) FROM flags") == 0
    assert fsm_jobs(mock) == before


def test_an_in_area_zip_passes(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    checked = agent.tool("check_service_area", call, zip="11201")
    assert checked["ok"] and checked["data"]["in_area"]


def test_every_intake_field_is_required_except_system_and_arrival_notes(agent):
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    for field in ("issue", "since", "tried", "who_home", "approver"):
        details = {k: v for k, v in DETAILS.items() if k != field}
        response = agent.tool(
            "book_appointment", call, job_type="tune_up", slot=slot, details=details
        )
        assert response["refused"] == "invalid_arguments", field
        assert any(field in key for key in response["data"]["errors"]), field
    optional = {k: v for k, v in DETAILS.items() if k not in ("system_type", "arrival_notes")}
    assert agent.tool("book_appointment", call, job_type="tune_up", slot=slot, details=optional)[
        "ok"
    ]


def test_a_slot_that_is_not_offered_is_refused(agent):
    call = ready_to_book(agent)
    assert (
        book(agent, call, {"date": "2026-11-03", "window": "brunch"})["refused"] == "invalid_slot"
    )
    assert (
        book(agent, call, {"date": "2027-01-01", "window": "morning"})["refused"] == "invalid_slot"
    )
    # Normal jobs never get the after-hours window or the held urgent slots.
    assert (
        book(agent, call, {"date": "2026-11-03", "window": "after_hours"})["refused"]
        == "invalid_slot"
    )
    assert book(agent, call, FULL_DAY)["refused"] == "slot_taken"


def test_an_urgent_job_with_no_window_today_books_tomorrow_and_is_flagged(agent, clock):
    clock.advance(hours=11)  # 8 p.m. New York, after the last regular window has started
    call = ready_to_book(agent)
    agent.turn(call, "My 84-year-old mother lives with me and it's 52 in here.")
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 52})
    assert found["data"]["priority"]["level"] == "Urgent"
    first = found["data"]["first_urgent"]
    if first["day"] == "today":  # the on-call after-hours window is urgent-only, offered tonight
        assert first["window"] == "after_hours"
    tomorrow = {"date": "2026-11-03", "window": "morning"}
    booked = book(agent, call, tomorrow, job_type="no_heat", inside_temp_f=52)
    assert booked["ok"], booked
    assert booked["data"]["seek_earlier"] is True
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'seek_earlier'") == 1


def test_reschedule_and_cancel_only_act_on_the_callers_own_booking(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    brennan = scenarios["jobDoneLastWeek"]
    call = agent.start(brennan["phone"])["data"]["call_id"]
    moved = agent.tool(
        "reschedule_appointment", call, booking_id=maria["jobId"], slot=ONE_LEFT, reason="x"
    )
    assert moved["refused"] == "not_owner"
    cancelled = agent.tool(
        "cancel_appointment", call, booking_id=maria["jobId"], reason="no longer needed"
    )
    assert cancelled["refused"] == "not_owner"
    unknown = agent.tool("cancel_appointment", call, booking_id=999999, reason="no longer needed")
    assert unknown["refused"] == "not_owner"


def test_a_caller_can_move_their_own_booking_once(agent):
    call = ready_to_book(agent)
    booked = book(agent, call, first_open(agent, call))
    job_id = booked["data"]["booking_id"]
    moved = agent.tool(
        "reschedule_appointment", call, booking_id=job_id, slot=ONE_LEFT, reason="works better"
    )
    assert moved["ok"], moved
    again = agent.tool(
        "reschedule_appointment",
        call,
        booking_id=job_id,
        slot={"date": "2026-11-06", "window": "morning"},
        reason="again",
    )
    assert again["refused"] == "limit"


def test_a_cancel_needs_a_reason_and_offers_two_windows(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    no_reason = agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="")
    assert no_reason["refused"] == "invalid_arguments"
    # Maria's job is already dispatched, so it can't be cancelled from the phone.
    dispatched = agent.tool(
        "cancel_appointment", call, booking_id=maria["jobId"], reason="fixed itself"
    )
    assert dispatched["refused"] == "invalid_state"

    other = ready_to_book(agent, "(212) 555-0901")
    job_id = book(agent, other, first_open(agent, other))["data"]["booking_id"]
    agent.turn(other, "Actually, cancel that.")
    cancelled = agent.tool("cancel_appointment", other, booking_id=job_id, reason="fixed itself")
    assert cancelled["ok"], cancelled
    assert len(cancelled["data"]["alternatives"]) == 2
    second = agent.tool("cancel_appointment", other, booking_id=job_id, reason="again please")
    assert second["refused"] == "limit"
