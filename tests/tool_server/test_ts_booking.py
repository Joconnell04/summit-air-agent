"""Booking gates: read-back, idempotency and the race, commercial, out of area, intake fields,
caller ID shorthand, reschedule and cancel ownership (spec S-05, S-13, S-15, S-25 to S-28, S-31)."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from toolkit import (
    DETAILS,
    TENANT_DIR,
    UNKNOWN,
    book,
    count,
    first_open,
    in_area_address,
    ready_to_book,
)

from app import lifecycle
from app.db import iso
from app.lifecycle import sounds_commercial
from app.tenant import load_agent
from app.text import phone_said, street_said

AGENT = load_agent(TENANT_DIR)[0]

FULL_DAY = {"date": "2026-11-04", "window": "0800-1200"}  # nearly-full day: no normal slots
ONE_LEFT = {"date": "2026-11-05", "window": "1200-1600"}  # exactly one normal slot open


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
    agent.turn(call, "It's 101 Union St, 11231.")
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
    assert corrected["ok"] and "one oh seven Union St" in corrected["say"]
    assert book(agent, call, first_open(agent, call))["refused"] == "gate_read_back"


def test_the_read_back_is_shorthand_with_the_number_in_groups(agent):
    """Eval round 3: one sentence, no "let me read that back" lead-in, no internal words. Jackson:
    "just shorthand like 'the 199 e 4th street and unit 2'": no ZIP, city or "your home"."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 101 Union St, 11231.")
    asked = agent.tool("read_back", call, address=in_area_address(), phone="2125550142")
    # The sentence after the short connector Jackson asked for ("Perfect."), shorthand otherwise.
    assert asked["data"]["sentence"] == (
        "So that's one oh one Union St, and two one two, five five five, zero one four two. "
        "That's all correct, right?"
    )
    assert "read" not in asked["say"].lower()
    calling = agent.tool("read_back", call, address=in_area_address(), phone="caller_id")
    assert calling["data"]["sentence"] == (
        "So that's one oh one Union St, and the number you're calling from, ending in zero nine nine "
        "nine. That's all correct, right?"
    )


@pytest.mark.parametrize(
    "phone",
    ["the number I'm calling from", "this number", "this one", "the one I'm calling from"],
)
def test_the_number_calling_said_as_a_phrase_is_the_caller_id(agent, phone):
    """Retell call call_9826ff9f0dfa8bc835a3601368b: read_back got "the number I'm calling from"
    and the caller heard "all ten digits" again."""
    call = agent.start("+1 (347) 555-0101")["data"]["call_id"]
    agent.turn(call, "It's 101 Union St, 11231.")
    asked = agent.tool("read_back", call, address=in_area_address(), phone=phone)
    assert asked["ok"], asked
    assert "the number you're calling from" in asked["say"]


@pytest.mark.parametrize("phone", ["555 0142", "212 555 014", "212 555 01422"])
def test_a_number_that_isnt_ten_digits_is_asked_for_again_in_full(agent, phone):
    """Retell call call_cda6d9abd395cbb8859e0d09b14 review: a number short or long by a digit is
    never read back, and the caller is asked for all ten digits."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, f"It's 101 Union St, 11231, and my number is {phone}.")
    asked = agent.tool("read_back", call, address=in_area_address(), phone=phone)
    assert asked["refused"] == "invalid_phone"
    assert asked["say"] == "Could I get that number again, all ten digits?"


def test_a_caller_who_answers_the_read_back_with_a_business_cannot_book(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The heat's out at 101 Union St, 11231.")
    assert agent.tool("read_back", call, address=in_area_address(), phone="caller_id")["ok"]
    agent.turn(call, "Well, it's a business, it's our office upstairs.")
    agent.tool("read_back", call, address=in_area_address(), phone="caller_id", confirmed=True)
    assert book(agent, call, first_open(agent, call))["refused"] == "customer_type"


def test_the_number_im_calling_from_is_the_real_caller_id(agent):
    call = agent.start("+1 (347) 555-0101")["data"]["call_id"]
    agent.turn(call, "It's 101 Union St, 11231.")
    asked = agent.tool("read_back", call, address=in_area_address(), phone="caller_id")
    assert "the number you're calling from" in asked["say"]


def test_the_one_ending_in_picks_the_matching_number_on_file(agent, scenarios):
    park = scenarios["activeMemberWithAdultChild"]  # two numbers on file
    call = agent.start(park["phone"])["data"]["call_id"]
    agent.turn(call, "It's for 101 Union St, 11231.")
    ending = park["adultChildPhone"][-4:]
    asked = agent.tool("read_back", call, address=in_area_address(), phone=ending)
    digits = "".join(c for c in park["adultChildPhone"] if c.isdigit())
    assert phone_said(digits) in asked["say"]
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


def test_the_same_visit_twice_on_one_call_is_refused(agent):
    call = ready_to_book(agent)
    assert book(agent, call, first_open(agent, call))["ok"]
    assert book(agent, call, ONE_LEFT)["refused"] == "already_booked"


def test_a_third_booking_on_one_call_is_refused_and_flags_a_person(agent):
    """Two jobs per call at most (DECISIONS.md 085), then a person takes over."""
    call = ready_to_book(agent)
    assert book(agent, call, first_open(agent, call))["ok"]
    second = book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat",
                  separate_problem=True)
    assert second["ok"], second
    third = book(agent, call, ONE_LEFT, job_type="estimate", separate_problem=True)
    assert third["refused"] == "limit"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'limit'") == 1


def test_a_commercial_account_cannot_book_whatever_the_agent_asks(agent, mock, scenarios):
    before = fsm_jobs(mock)
    for account in scenarios["commercialAccounts"]:
        call = ready_to_book(agent, account["phone"])
        refused = book(agent, call, first_open(agent, call), job_type="no_heat")
        assert refused["refused"] == "customer_type", account["name"]
        assert "we only service homes" in refused["say"]
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


def test_known_caller_with_every_location_out_of_area_is_recorded_at_start(agent, scenarios):
    resident = scenarios["outOfAreaResidents"][0]
    call = agent.start(resident["phone"])["data"]["call_id"]
    recorded = count(
        agent,
        "SELECT COUNT(*) FROM out_of_area WHERE call_id = ? AND zip = ?",
        call,
        resident["zip"],
    )
    assert recorded == 1


def test_a_known_caller_with_several_locations_waits_for_the_calls_zip(agent):
    away = [{"zip": "07030", "in_service_area": False}, {"zip": "10001", "in_service_area": False}]
    conn = agent.db()
    try:
        lifecycle._record_known_out_of_area(conn, "multi", [{"locations": away}], agent.clock())
    finally:
        conn.close()
    assert count(agent, "SELECT COUNT(*) FROM out_of_area WHERE call_id = 'multi'") == 0


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
    agent.turn(call, "I'm in 11201.")
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
        book(agent, call, {"date": "2027-01-01", "window": "0800-1200"})["refused"] == "invalid_slot"
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
    tomorrow = {"date": "2026-11-03", "window": "0800-1200"}
    # After hours with an older person at home, the on-call technician is alerted first.
    assert agent.tool("flag_person", call, reason="On-call: no heat, 84 at home", priority="Urgent")[
        "ok"
    ]
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
        slot={"date": "2026-11-06", "window": "0800-1200"},
        reason="again",
    )
    assert again["refused"] == "limit"


def test_a_cancel_offers_two_windows(agent, scenarios):
    # The reason is optional since issue #149 (test_ts_caller_changes.py, test_a_cancel_needs_no_reason).
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    # Eval round 1, cancel-offers-windows: her afternoon visit isn't dispatched yet, so it cancels.
    cancelled = agent.tool(
        "cancel_appointment", call, booking_id=maria["jobId"], reason="cousin will look at it"
    )
    assert cancelled["ok"], cancelled

    other = ready_to_book(agent, "(212) 555-0901")
    job_id = book(agent, other, first_open(agent, other))["data"]["booking_id"]
    agent.turn(other, "Actually, cancel that.")
    cancelled = agent.tool("cancel_appointment", other, booking_id=job_id, reason="fixed itself")
    assert cancelled["ok"], cancelled
    assert len(cancelled["data"]["alternatives"]) == 2
    # Eval round 4: the voice layer speaks this as it is, so the offer is in the sentence.
    assert cancelled["say"].startswith("That visit is cancelled. If you'd like another time, I have ")
    second = agent.tool("cancel_appointment", other, booking_id=job_id, reason="again please")
    assert second["refused"] == "limit"


def test_a_dispatched_visit_is_handed_to_a_person_not_cancelled(agent, tmp_path):
    call = ready_to_book(agent)
    job_id = book(agent, call, first_open(agent, call))["data"]["booking_id"]
    conn = sqlite3.connect(tmp_path / "world.db")
    with conn:
        conn.execute("UPDATE jobs SET status = 'Dispatched' WHERE id = ?", (job_id,))
    conn.close()
    agent.turn(call, "Actually, cancel that.")
    refused = agent.tool("cancel_appointment", call, booking_id=job_id, reason="fixed itself")
    assert refused["refused"] == "invalid_state"


AFTER_HOURS = {"hours": 12, "minutes": 30}  # 9:30 p.m. New York
TOMORROW_MORNING = {"date": "2026-11-03", "window": "0800-1200"}


def test_after_hours_a_cold_house_alerts_the_on_call_technician_before_booking(agent, clock):
    """Eval round 1, after-hours-no-heat-cold: 48 F inside at night was booked for the morning
    with no on-call alert."""
    clock.advance(**AFTER_HOURS)
    call = ready_to_book(agent)
    agent.turn(call, "It's 48 in here and dropping. Just two healthy adults at home.")
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 48})
    assert found["data"]["after_hours_outcome"] == "tonight_if_on_call_agrees"
    # The server alerts the on-call technician itself and books in the same call: refused with
    # on_call_first, the side-by-side's models stalled and never booked or flagged.
    booked = book(agent, call, TOMORROW_MORNING, job_type="no_heat", inside_temp_f=48)
    assert booked["ok"], booked
    assert booked["say"].startswith(
        "I've let our on-call team know this call is top priority, and someone will call you"
    )
    assert "Okay, I've booked it" in booked["say"]
    assert booked["say"].count("top priority") == 1
    flag = agent.db().execute(
        "SELECT kind, priority FROM flags WHERE kind = 'person'"
    ).fetchone()
    assert tuple(flag) == ("person", "Urgent")
    # No time to ask for: the on-call technician calls back within the tenant's set minutes.
    due = agent.db().execute("SELECT due_at FROM callbacks").fetchone()[0]
    assert due == iso(clock() + timedelta(minutes=20))


def test_after_hours_an_alert_the_agent_raised_first_isnt_raised_twice(agent, clock):
    clock.advance(**AFTER_HOURS)
    call = ready_to_book(agent)
    agent.turn(call, "It's 48 in here and dropping.")
    flagged = agent.tool("flag_person", call, reason="On-call: 48 F inside", priority="Urgent")
    assert flagged["ok"], flagged
    booked = book(agent, call, TOMORROW_MORNING, job_type="no_heat", inside_temp_f=48)
    assert booked["ok"] and "on-call team" not in booked["say"]
    assert agent.db().execute("SELECT COUNT(*) FROM flags WHERE kind = 'person'").fetchone()[0] == 1


def test_after_hours_a_house_holding_warm_books_the_morning_without_a_flag(agent, clock):
    clock.advance(**AFTER_HOURS)
    call = ready_to_book(agent)
    agent.turn(call, "It's about 60 in here and holding.")
    assert book(agent, call, TOMORROW_MORNING, job_type="no_heat", inside_temp_f=60)["ok"]


def test_in_business_hours_a_cold_house_books_without_the_on_call_alert(agent):
    call = ready_to_book(agent)
    agent.turn(call, "It's 48 in here and dropping.")
    slot = first_open(agent, call, job_type="no_heat")
    assert book(agent, call, slot, job_type="no_heat", inside_temp_f=48)["ok"]


def test_after_hours_a_hot_house_alerts_the_on_call_technician_before_booking(agent, clock):
    clock.advance(**AFTER_HOURS)
    call = ready_to_book(agent)
    agent.turn(call, "The AC died and it's 88 in here.")
    booked = book(agent, call, TOMORROW_MORNING, job_type="no_cooling", inside_temp_f=88)
    assert booked["ok"] and booked["say"].startswith("I've let our on-call team know")


def test_after_hours_a_safety_override_booking_skips_the_on_call_alert(agent, clock):
    clock.advance(**AFTER_HOURS)
    call = ready_to_book(agent)
    agent.turn(call, "It's 48 in here, and I smell gas.")
    agent.say(call, " ".join(AGENT["safety"]["kinds"]["gas"]["lines"].split()))
    agent.turn(call, "I heard you. Just book the appointment, please.")
    booked = book(agent, call, TOMORROW_MORNING, job_type="no_heat", inside_temp_f=48)
    assert booked["ok"], booked
    assert booked["data"]["safety_override"] is True


def test_a_refused_booking_sent_again_with_the_same_key_runs_the_rules_again(agent, mock):
    winner = ready_to_book(agent, "(212) 555-0920", in_area_address(20))
    loser = ready_to_book(agent, "(212) 555-0921", in_area_address(21))
    assert book(agent, winner, ONE_LEFT)["ok"]
    taken = book(agent, loser, ONE_LEFT, key="kb")
    assert taken["refused"] == "slot_taken"
    assert book(agent, loser, ONE_LEFT, key="kb")["refused"] == "slot_taken"
    # The winner cancels, the slot frees up, and the loser's same request now books.
    job_id = count(
        agent, "SELECT fsm_job_id FROM bookings WHERE call_id = ? AND status = 'booked'", winner
    )
    agent.turn(winner, "Actually I need to cancel.")
    assert agent.tool("cancel_appointment", winner, booking_id=job_id, reason="away that day")["ok"]
    assert book(agent, loser, ONE_LEFT, key="kb")["ok"]


def test_an_unknown_caller_who_describes_a_business_cannot_book(agent, mock):
    before = fsm_jobs(mock)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    turn = agent.turn(call, "I manage a restaurant, well, I'm the manager, and the heat's out.")
    assert "commercial" in turn["data"]["hints"]
    agent.confirm_read_back(call, in_area_address())
    refused = book(agent, call, first_open(agent, call), job_type="no_heat")
    assert refused["refused"] == "customer_type"
    assert fsm_jobs(mock) == before


def test_a_landlord_of_a_two_family_house_is_not_marked_commercial(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    turn = agent.turn(call, "I own a two-family house and my tenants upstairs have no heat.")
    assert "commercial" not in turn["data"]["hints"]


def _maria_ready(agent, maria) -> str:
    started = agent.start(maria["phone"])["data"]
    loc = started["customers"][0]["locations"][0]
    call = started["call_id"]
    agent.turn(call, "I need to move my appointment, something came up at work")
    agent.confirm_read_back(call, {k: loc[k] for k in ("street", "unit", "city", "zip")})
    return call


def test_a_caller_with_an_open_visit_is_sent_to_reschedule_not_a_second_booking(
    agent, mock, scenarios
):
    """Eval round 1, reschedule-own-booking: book_appointment made a second visit."""
    maria = scenarios["repeatCallerOpenJob"]
    call = _maria_ready(agent, maria)
    before = fsm_jobs(mock)
    refused = book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat")
    assert refused["refused"] == "open_job"
    assert refused["data"]["job_id"] == maria["jobId"]
    assert "reschedule_appointment" in refused["data"]["use"]
    assert fsm_jobs(mock) == before


def test_a_confirmed_separate_problem_can_still_be_booked(agent, scenarios):
    call = _maria_ready(agent, scenarios["repeatCallerOpenJob"])
    booked = agent.tool(
        "book_appointment",
        call,
        job_type="tune_up",
        slot=first_open(agent, call),
        details=DETAILS,
        separate_problem=True,
    )
    assert booked["ok"], booked


def _mock_customer_of_job(mock, job_id: int) -> dict:
    conn = sqlite3.connect(mock[0].state.db_path)
    conn.row_factory = sqlite3.Row
    try:
        customer = conn.execute(
            "SELECT c.* FROM jobs j JOIN customers c ON c.id = j.customer_id WHERE j.id = ?",
            (job_id,),
        ).fetchone()
        contacts = conn.execute(
            "SELECT * FROM contacts WHERE customer_id = ?", (customer["id"],)
        ).fetchall()
    finally:
        conn.close()
    return {**dict(customer), "contacts": [dict(c) for c in contacts]}


def test_an_open_visit_at_another_address_does_not_block_a_booking(agent, scenarios):
    call = agent.start(scenarios["repeatCallerOpenJob"]["phone"])["data"]["call_id"]
    agent.turn(call, "Hi, the heat's out at my other place.")
    agent.confirm_read_back(call, in_area_address(41))
    asked = book(agent, call, first_open(agent, call))
    assert asked["refused"] == "account_choice"
    assert agent.tool(
        "book_appointment",
        call,
        job_type="tune_up",
        slot=first_open(agent, call),
        details=DETAILS,
        new_address_account="caller",
    )["ok"]


def test_a_parents_house_goes_on_its_own_account_with_the_caller_as_contact(agent, mock, scenarios):
    """Eval round 2, seed 5: a caller with an open job at home books her mother's house."""
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, "This call is for my mother's house in Bensonhurst, the yearly tune-up.")
    mothers = {"street": "1857 77th St", "unit": "", "city": "Brooklyn", "zip": "11204"}
    agent.confirm_read_back(call, mothers)
    refused = book(agent, call, first_open(agent, call))
    assert refused["refused"] == "account_choice"
    assert refused["say"] == "Should I put this under your account?"
    no_name = agent.tool(
        "book_appointment",
        call,
        job_type="tune_up",
        slot=first_open(agent, call),
        details={**DETAILS, "name": ""},
        new_address_account="own",
    )
    assert no_name["refused"] == "invalid_arguments"
    booked = agent.tool(
        "book_appointment",
        call,
        job_type="tune_up",
        slot=first_open(agent, call),
        details={**DETAILS, "name": "Rosa Delgado"},
        new_address_account="own",
    )
    assert booked["ok"], booked
    customer = _mock_customer_of_job(mock, booked["data"]["booking_id"])
    assert customer["id"] != maria["customerId"]
    assert customer["name"] == "Rosa Delgado"
    assert [c["memo"] for c in customer["contacts"]] == ["Maria Delgado"]


def test_a_known_caller_at_an_address_on_file_is_not_asked_about_accounts(agent, scenarios):
    thomas = scenarios["jobDoneLastWeek"]
    call = agent.start(thomas["phone"])["data"]["call_id"]
    agent.turn(call, "The heat's out again.")
    agent.confirm_read_back(
        call, {"street": "89-15 Springfield Blvd", "unit": "", "city": "Queens", "zip": "11427"}
    )
    assert book(agent, call, first_open(agent, call))["ok"]


# ---- arrival hours and spelled streets (eval round 2, seeds 7 and spelling) -----------------


def test_arrival_hours_read_8_to_2_as_daytime_and_offer_only_windows_inside(agent):
    call = ready_to_book(agent)
    found = agent.tool(
        "find_slots", call, job_type="tune_up", arrive_after="8", arrive_before="2"
    )
    assert found["ok"], found
    assert found["data"]["arrival"] == {"after": "08:00", "before": "14:00", "fits": True}
    windows = [w for d in found["data"]["days"] for w in d["windows"]]
    assert windows and all("0800" <= w[:4] and w[5:] <= "1400" for w in windows)


def test_arrival_hours_narrower_than_a_window_get_a_shorter_one_that_fits(agent):
    call = ready_to_book(agent)
    longer = agent.tool("find_slots", call, job_type="tune_up", arrive_before="9", window_minutes=120)
    assert longer["ok"], longer
    assert longer["data"]["arrival"]["fits"] is True
    assert {label for d in longer["data"]["days"] for label in d["labels"]} == {"8 to 9"}


def test_windows_are_30_minutes_unless_the_caller_asks(agent):
    """Jackson's gas test call call_0246b91f77e01a4b18583e6ca8b heard "5 to 7": "by default, it
    should give a 30 minute way, not a 2 hour window"."""
    call = ready_to_book(agent)
    found = agent.tool("find_slots", call, job_type="tune_up", arrive_before="9")
    assert found["ok"], found
    assert {label for d in found["data"]["days"] for label in d["labels"]} == {
        "8 to 8:30", "8:30 to 9"
    }
    offered = agent.tool("find_slots", call, job_type="tune_up")["data"]["offer"]
    start, end = offered["window"].split("-")
    minutes = (int(end[:2]) * 60 + int(end[2:])) - (int(start[:2]) * 60 + int(start[2:]))
    assert offered["after_hours"] or minutes == 30


def test_an_arrival_time_no_window_fits_is_said_plainly_without_a_promise(agent):
    call = ready_to_book(agent)
    found = agent.tool("find_slots", call, job_type="tune_up", arrive_before="8:15")
    # No 30-minute window ends by 8:15.
    assert found["ok"], found
    assert found["data"]["arrival"]["fits"] is False
    assert found["say"].startswith(
        "Our arrival windows are 30 minutes, and the technician can come any time in the window, "
        "so I can't promise an exact time."
    )


@pytest.mark.parametrize(
    "address,street",
    [
        ({"street": "81 Huston St", "spelled": "H, O, U, S, T, O, N"}, "81 Houston St"),
        ({"street": "81 H O U S T O N Street"}, "81 Houston Street"),
        ({"street": "412 Kosciuszko St", "spelled": "k o s c i u s z k o"}, "412 Kosciuszko St"),
        ({"street": "12 Elm Street", "spelled": "E as in Edward, L, M"}, "12 Elm Street"),
    ],
)
def test_a_spelled_street_is_joined_into_the_name_for_the_read_back(agent, address, street):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's on Houston, 10302, let me spell it.")
    agent.turn(call, address.get("spelled") or "H O U S T O N")
    asked = agent.tool(
        "read_back", call, address={"zip": "10302", **address}, phone="caller_id"
    )
    assert asked["ok"], asked
    assert asked["data"]["sentence"].startswith(f"So that's {street_said(street)}, and ")


def test_an_owner_who_lives_downstairs_with_a_tenant_upstairs_can_book(agent):
    """Eval round 1, tune-up-known-caller: "I own the building" with one tenant is a home."""
    call = ready_to_book(agent)
    for line in (
        "I'm in the downstairs unit, and the boiler's in the basement. My tenant lives upstairs.",
        "Yes, that's right. And yes, I own the building and I approve the work.",
    ):
        assert "commercial" not in agent.turn(call, line)["data"]["hints"]
    assert book(agent, call, first_open(agent, call))["ok"]


@pytest.mark.parametrize(
    "line",
    [
        "We own the building, it's a three-family",
        "I own an apartment building in Flatbush",
        "I manage a six-unit walk-up in Crown Heights and my tenants have no heat",
        "I'm the super, the boiler for the whole apartment building is out",
        "I'm the property manager for a few apartment buildings",
        "We manage the building, it's not my home, it's twelve units",
        "I live in a co-op and the board has to approve any work",
    ],
)
def test_apartment_buildings_and_their_managers_are_residential(line):
    """Eval round 2: residential means residential-grade equipment, so an apartment building, its
    super and its manager are residential, whatever the count of units."""
    assert not sounds_commercial(line, AGENT["commercial"]), line


def test_a_building_manager_can_book_for_an_apartment_building(agent):
    """Eval round 2, property-manager-books-building (seed 1)."""
    call = ready_to_book(agent)
    for line in (
        "I run a six-unit walk-up in Crown Heights, my tenants have no heat.",
        "Yes, I manage the building and I approve the work.",
    ):
        assert "commercial" not in agent.turn(call, line)["data"]["hints"]
    assert book(agent, call, first_open(agent, call))["ok"]


def test_the_commercial_note_and_refusal_give_the_model_nothing_to_parrot():
    """The model once read out "the caller describes this as a business". The note it gets says
    what to do, never why, and the refusal it gets is the caller-facing line itself."""
    from prompts.assemble import SERVER_NOTES

    note = SERVER_NOTES["commercial"]
    assert "caller describ" not in note.lower() and "building you manage" not in note
    assert "as written" in note
    line = " ".join(AGENT["lines"]["commercial"].split())
    assert line.startswith("Thanks for thinking of us.")
    for internal in ("caller", "system", "describ", "treat", "manage", "flag"):
        assert internal not in line.lower(), internal


def test_the_server_refusal_for_a_commercial_call_is_the_journey_line(agent):
    call = ready_to_book(agent)
    agent.turn(call, "I run a restaurant and the heat is out")
    refused = book(agent, call, first_open(agent, call))
    assert refused["refused"] == "customer_type"
    assert refused["say"] == " ".join(AGENT["lines"]["commercial"].split())


def test_a_number_the_caller_only_states_is_not_enough_to_change_a_visit(agent):
    owner = ready_to_book(agent, "(212) 555-0930", in_area_address(30))
    job_id = book(agent, owner, first_open(agent, owner))["data"]["booking_id"]
    agent.end(owner)
    other = agent.start("(212) 555-0931")["data"]["call_id"]
    found = agent.tool("find_customer", other, phone="(212) 555-0930")
    assert found["data"]["matches"]
    refused = agent.tool("cancel_appointment", other, booking_id=job_id, reason="not needed")
    # From a number not on the account, the caller says the name and address first (issue #128).
    assert refused["refused"] == "identity_check"
    agent.say(other, refused["say"])
    agent.turn(other, "It's Pat Caller, 130 Union Street.")
    assert agent.tool("cancel_appointment", other, booking_id=job_id, reason="not needed")["ok"]


def test_a_limit_flags_a_person_once_however_often_it_is_hit(agent):
    call = ready_to_book(agent)
    assert book(agent, call, first_open(agent, call))["ok"]
    assert book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat",
                separate_problem=True)["ok"]
    for _ in range(3):
        assert book(agent, call, ONE_LEFT, job_type="estimate",
                    separate_problem=True)["refused"] == "limit"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'limit'") == 1


@pytest.mark.parametrize(
    "line",
    [
        "I manage a restaurant and our heat is out",
        "I run a restaurant and the heat is out",
        "It's for my bakery on Church Ave",
        "It's an office building on Court Street",
        "The rooftop unit over our kitchen died",
        "It's a storefront, the heat in the shop is out",
        # PR #74 review: callers who say it's a business, not a home, stay commercial.
        "It's not a home, it's a deli",
        "It's an office, not my home",
        "It's not my home, it's a church",
        "I'm a property manager at a shopping center",
        "We manage the property, it's retail",
        "I run the property it is a hotel",
        "I manage a building of commercial tenants",
        "The units in our strip mall are out",
        "It's the plaza on Atlantic Avenue",
        "It's for my dental practice upstairs",
    ],
)
def test_business_callers_are_recognized_from_their_words(line):
    assert sounds_commercial(line, AGENT["commercial"]), line


@pytest.mark.parametrize(
    "line",
    [
        "My business partner is home with the kids",
        "I own a two-family house and my tenants have no heat",
        "We live in a co-op and our building's super is away",
        "My kid's school called, I'm home now",
        "I work from my home office and it's freezing",
        "My company is sending me on a trip so mornings are bad",
    ],
)
def test_home_callers_are_not_marked_commercial(line):
    assert not sounds_commercial(line, AGENT["commercial"]), line


# ---- eval round 2: who can approve the work ------------------------------------------------------


def _approval_callbacks(agent, call):
    conn = agent.db()
    try:
        return conn.execute(
            "SELECT due_at, reason FROM callbacks WHERE call_id = ? AND reason LIKE 'Confirm who%'",
            (call,),
        ).fetchall()
    finally:
        conn.close()


def test_a_co_op_board_that_must_approve_is_residential_and_gets_the_approval_note(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    turn = agent.turn(call, "I own my unit in a co-op, but the co-op board has to approve any work.")
    assert "approval_unclear" in turn["data"]["hints"]
    assert "commercial" not in turn["data"]["hints"]


def test_a_booking_with_approval_unclear_gets_a_call_before_dispatch(agent, clock):
    """Eval round 2, co-op-owner-mentions-board: booked only because the caller asked, so a person
    calls before the technician heads out, inside calling hours and before the window."""
    call = ready_to_book(agent)
    agent.turn(call, "The co-op board has to approve any work, but please just book it.")
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    callbacks = _approval_callbacks(agent, call)
    assert len(callbacks) == 1
    due = datetime.fromisoformat(callbacks[0]["due_at"])
    assert booked["data"]["approval_callback_at"] == callbacks[0]["due_at"]
    conn = agent.db()
    try:
        row = conn.execute("SELECT window_start FROM bookings WHERE call_id = ?", (call,)).fetchone()
    finally:
        conn.close()
    window_start = datetime.fromisoformat(row["window_start"])
    local = due.astimezone(ZoneInfo("America/New_York"))
    assert clock.now <= due < window_start
    assert time(8) <= local.time() <= time(21)
    assert "to confirm who can approve the work" in booked["say"]
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'approval_check'") == 1


def test_an_approver_the_agent_records_as_unsure_also_gets_the_call(agent):
    call = ready_to_book(agent)
    booked = book(agent, call, first_open(agent, call), approver="Not sure, maybe the board")
    assert booked["ok"], booked
    assert len(_approval_callbacks(agent, call)) == 1


def test_a_caller_who_approves_gets_no_approval_call(agent):
    call = ready_to_book(agent)
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    assert booked["data"]["approval_callback_at"] is None
    assert "approve" not in booked["say"]
    assert _approval_callbacks(agent, call) == []


def test_a_property_manager_account_typed_residential_can_book(agent, scenarios):
    """Eval round 2: the mock's property manager and co-op board look after apartment buildings,
    so they are Residential accounts and book like any home."""
    linden = next(
        a for a in scenarios["residentialBuildingAccounts"] if a["kind"] == "property manager"
    )
    call = ready_to_book(agent, linden["phone"])
    assert agent.tool("find_slots", call, job_type="tune_up")["ok"]
    # A manager booking a building they look after keeps it on their own account (seed 5).
    assert book(agent, call, first_open(agent, call), account="caller")["ok"]


NY = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    "now,window,expected,late",
    [
        # An afternoon booking for tomorrow at 8: the evening before, inside calling hours.
        ((2026, 11, 2, 14, 0), (2026, 11, 3, 8, 0), (2026, 11, 2, 20, 30), False),
        # A midday window: two hours before it.
        ((2026, 11, 2, 9, 0), (2026, 11, 3, 12, 0), (2026, 11, 3, 10, 0), False),
        # PR #74 review: booked at 10:30 PM for tomorrow at 8. The evening has passed, so the next
        # opening of calling hours, which is when the window opens: the job is held.
        ((2026, 11, 2, 22, 30), (2026, 11, 3, 8, 0), (2026, 11, 3, 8, 0), True),
        # Booked at 6:30 AM for a noon window: calling hours open at 8, still before the window.
        ((2026, 11, 3, 6, 30), (2026, 11, 3, 12, 0), (2026, 11, 3, 10, 0), False),
        # Booked at 6:30 AM for an 8 AM window: no calling time before it.
        ((2026, 11, 3, 6, 30), (2026, 11, 3, 8, 0), (2026, 11, 3, 8, 0), True),
    ],
)
def test_the_approval_call_is_always_inside_calling_hours(now, window, expected, late):
    from app import records, tools
    from app.tenant import Tenant

    tenant = Tenant.load(TENANT_DIR)
    now, window = datetime(*now, tzinfo=NY), datetime(*window, tzinfo=NY)
    due = tools.approval_call_time(tenant, window, now)
    assert due == datetime(*expected, tzinfo=NY)
    assert records.in_outbound_hours(tenant, due)
    assert due >= now
    assert (due >= window) is late


def test_a_late_night_booking_with_approval_unclear_holds_the_job_and_says_so(agent, clock):
    clock.now = datetime(2026, 11, 3, 3, 30, tzinfo=UTC)  # 10:30 PM in New York
    call = ready_to_book(agent)
    agent.turn(call, "The co-op board has to approve any work, but please book it anyway.")
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    due = datetime.fromisoformat(booked["data"]["approval_callback_at"])
    assert time(8) <= due.astimezone(NY).time() < time(21)
    conn = agent.db()
    try:
        start = conn.execute("SELECT window_start FROM bookings WHERE call_id = ?", (call,))
        window_start = datetime.fromisoformat(start.fetchone()["window_start"])
        reason = conn.execute(
            "SELECT reason FROM flags WHERE kind = 'approval_check'"
        ).fetchone()["reason"]
    finally:
        conn.close()
    if due >= window_start:
        assert "hold the technician" in reason
        assert "won't start until that's settled" in booked["say"]
    else:
        assert "Before the technician heads out" in booked["say"]


@pytest.mark.parametrize(
    "line,unclear",
    [
        ("The co-op board has to approve any work", True),
        ("I'll have to ask my landlord first", True),
        ("I'm not sure who approves work in the building", True),
        # PR #74 review: negated or settled lines say approval is clear.
        ("I don't need permission, it's my unit", False),
        ("no I dont need to ask my landlord", False),
        ("My landlord already approved it", False),
        ("We don't have to check with the board, it's my place", False),
    ],
)
def test_approval_is_unclear_only_when_the_caller_says_someone_else_must_approve(line, unclear):
    assert lifecycle.approval_unclear(line, AGENT["approval"]) is unclear
def test_the_confirmation_names_the_day_and_the_date(agent):
    """Eval round 3: "make sure the agent restates the day/date with the time"."""
    call = ready_to_book(agent)
    found = agent.tool("find_slots", call, job_type="tune_up")
    today = next(d for d in found["data"]["days"] if d["day"] == "today")
    booked = book(agent, call, {"date": today["date"], "window": today["windows"][0]})
    assert booked["ok"], booked
    assert booked["say"].startswith("Okay, I've booked it: the technician will arrive today between")
    # Another caller, so the new address isn't a second home for the first one.
    later = ready_to_book(agent, caller_id="(212) 555-0998", address=in_area_address(5))
    thursday = agent.tool("find_slots", later, job_type="tune_up", day="2026-11-05")
    window = thursday["data"]["windows"][0]["window"]
    on_thursday = book(agent, later, {"date": "2026-11-05", "window": window})
    assert on_thursday["ok"], on_thursday
    assert on_thursday["say"].startswith(
        "Okay, I've booked it: the technician will arrive Thursday, November 5, between"
    )


def test_the_same_problem_back_after_a_recent_visit_flags_a_person_to_review_it(agent, scenarios):
    """Eval round 2, seed 10: a return visit is booked and a person reviews priority and cost."""
    thomas = scenarios["jobDoneLastWeek"]
    call = agent.start(thomas["phone"])["data"]["call_id"]
    agent.turn(call, "You were here last week for no heat and it's out again.")
    agent.confirm_read_back(
        call, {"street": "89-15 Springfield Blvd", "unit": "", "city": "Queens", "zip": "11427"}
    )
    booked = book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat")
    assert booked["ok"], booked
    assert booked["data"]["return_visit_review"] is True
    assert "a manager will look over this visit" in booked["say"]
    assert count(
        agent,
        "SELECT COUNT(*) FROM flags WHERE call_id = ? AND kind = 'person' AND reason LIKE ?",
        call,
        "Return visit:%",
    ) == 1


def test_a_first_visit_flags_no_one(agent):
    call = ready_to_book(agent)
    booked = book(agent, call, first_open(agent, call))
    assert booked["data"]["return_visit_review"] is False
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE call_id = ?", call) == 0


def test_an_unrelated_booking_after_a_recent_visit_is_not_a_return_visit(agent, scenarios):
    """PR #75 review: a recent job of another type, and no word that it's the same problem."""
    thomas = scenarios["jobDoneLastWeek"]  # an AC repair finished last week
    call = agent.start(thomas["phone"])["data"]["call_id"]
    agent.turn(call, "Hi, I'd like to book my yearly heating tune-up.")
    agent.confirm_read_back(
        call, {"street": "89-15 Springfield Blvd", "unit": "", "city": "Queens", "zip": "11427"}
    )
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    assert booked["data"]["return_visit_review"] is False
    assert "manager" not in booked["say"]


@pytest.mark.parametrize(
    "line",
    [
        "It's 540 Church Ave.",
        "I live on Church Avenue in Flatbush.",
        "five forty church avenue",
        "It's 540 Church, Brooklyn.",
        "I'm right by Grand Army Plaza.",
        "It's on Hotel Street.",
        "We're on Office Road, the house at the end.",
    ],
)
def test_a_business_word_in_a_street_or_place_name_is_not_a_business(line):
    assert not sounds_commercial(line, AGENT["commercial"]), line


@pytest.mark.parametrize(
    "line",
    [
        "It's a church.",
        "I run a hotel.",
        "It's in a plaza on Atlantic.",
        "We run a restaurant on Church Ave.",
    ],
)
def test_a_business_said_as_one_still_reads_commercial_beside_a_street_name(line):
    assert sounds_commercial(line, AGENT["commercial"]), line


def test_a_caller_on_church_avenue_can_book(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat's out at 540 Church Ave, 11218.")
    address = {"street": "540 Church Ave", "unit": "", "city": "Brooklyn", "zip": "11218"}
    agent.confirm_read_back(call, address)
    assert book(agent, call, first_open(agent, call))["ok"]


@pytest.mark.parametrize(
    "line",
    [
        "I run a clinic. Dr. Patel handles the billing.",
        "It's my store. Street parking only.",
        "It's for my restaurant, St Marks and Third.",
    ],
)
def test_a_street_word_after_a_break_does_not_hide_the_business_before_it(line):
    assert sounds_commercial(line, AGENT["commercial"]), line


def test_a_street_name_still_hides_its_business_word_inside_one_sentence():
    assert not sounds_commercial("It's 540 Church St. in Brooklyn.", AGENT["commercial"])
def test_the_read_back_says_the_zip_and_number_digit_by_digit():
    """A live test call read 646 624 0418 back as "six hundred and forty six"."""
    assert phone_said("(646) 624-0418") == "six four six, six two four, zero four one eight"


def test_confirmed_details_are_not_read_back_again(agent):
    """Eval round 3 run 20261104-031113, reschedule-own-booking."""
    call = ready_to_book(agent)
    again = agent.tool("read_back", call, address=in_area_address(), phone="caller_id")
    assert again["refused"] == "read_back_confirmed" and again["say"] == ""
    assert "already confirmed" in again["data"]["note"]
    # Still confirmed, so the booking goes ahead.
    assert book(agent, call, first_open(agent, call))["ok"]
    later = ready_to_book(agent)
    changed = agent.tool("read_back", later, address=in_area_address(3), phone="caller_id")
    assert changed["data"]["sentence"].startswith("So that's one oh three Union St")


def test_a_renter_with_an_unknown_approver_books_with_a_call_before_dispatch(agent):
    """Eval round 3 run 20261104-031113, baby-no-heat-standard: the landlord's name is never
    needed to book (DECISIONS.md 045, eval round 1 verdict on renter-landlord-unknown)."""
    call = ready_to_book(agent)
    agent.turn(call, "I rent, I don't know the landlord's name, the management company handles it.")
    booked = book(agent, call, first_open(agent, call), approver="unknown")
    assert booked["ok"], booked
    assert booked["data"]["approval_callback_at"]
    assert "to confirm who can approve the work" in booked["say"]


def test_the_booking_confirmation_is_the_whole_turn_and_asks_anything_else(agent):
    """Eval round 4: the voice layer speaks it with no model step after, so it asks itself."""
    call = ready_to_book(agent)
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    assert booked["say"].startswith("Okay, I've booked it: ")
    assert booked["say"].endswith(" Anything else?")


@pytest.mark.parametrize(
    "advance,day",
    [
        # 11:50 PM Monday: Tuesday's visit is tomorrow.
        ({"hours": 14, "minutes": 50}, "tomorrow"),
        # 12:20 AM Tuesday: the same visit is today (a live call heard "tomorrow").
        ({"hours": 15, "minutes": 20}, "today"),
    ],
)
def test_today_and_tomorrow_come_from_the_visit_date_against_the_local_date(
    agent, clock, advance, day
):
    clock.advance(**advance)
    call = ready_to_book(agent)
    found = agent.tool("find_slots", call, job_type="tune_up", day="2026-11-03")
    window = found["data"]["windows"][0]["window"]
    booked = book(agent, call, {"date": "2026-11-03", "window": window})
    assert booked["ok"], booked
    assert booked["say"].startswith(f"Okay, I've booked it: the technician will arrive {day} between")


def test_whose_account_is_asked_with_the_confirmed_read_back_not_by_a_refused_booking(
    agent, scenarios
):
    """Eval run 20261104-183233, tune-up-known-caller: the booking's short line was said on the
    turn the booking was refused for the account choice, so the confirmation came alone. The
    question now comes as the confirmed read-back's own sentence, and the next booking goes
    through in one reply."""
    call = agent.start(scenarios["repeatCallerOpenJob"]["phone"])["data"]["call_id"]
    agent.turn(call, "Hi, the heat's out at my other place.")
    confirmed = agent.confirm_read_back(call, in_area_address(41))
    assert confirmed["data"]["account_choice"] is True
    assert confirmed["say"] == "Should I put this under your account?"
    agent.say(call, confirmed["say"])
    agent.turn(call, "Put it under my account.")
    booked = agent.tool(
        "book_appointment",
        call,
        job_type="tune_up",
        slot=first_open(agent, call),
        details=DETAILS,
        new_address_account="caller",
    )
    assert booked["ok"], booked


def test_an_address_already_on_file_confirms_with_no_account_question(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, "It's about the tune-up.")
    home = {"street": "620 Bedford Ave", "unit": "", "city": "Brooklyn", "zip": "11249"}
    confirmed = agent.confirm_read_back(call, home)
    assert "account_choice" not in confirmed["data"] and not confirmed["say"]


@pytest.mark.parametrize(
    "line",
    [
        "it's eight four one oh ridge boulevard brooklyn one one two oh nine right off the corner by the church if that helps",
        "we're across from the school on 4th Ave",
        "it's the house next to the restaurant",
        "around the corner from the church",
        "near the old hospital, the brick one",
    ],
)
def test_a_landmark_the_address_is_placed_by_is_not_a_business(line):
    # Eval run 20261104-183233, no-heat-gas-stove: "by the church" declined a home as commercial.
    assert not sounds_commercial(line, AGENT["commercial"]), line


@pytest.mark.parametrize(
    "line",
    ["It's a church", "I run the church on the corner", "we're a restaurant near the park"],
)
def test_a_business_said_as_the_callers_own_still_declines(line):
    assert sounds_commercial(line, AGENT["commercial"]), line


# ---- one simple choice (issue #149, live call 2) ------------------------------------------------


def test_slots_offer_one_choice_the_soonest_when_no_day_was_named(agent):
    """Jackson: "too many double booking questions... two sets of multi-options"."""
    call = ready_to_book(agent)
    found = agent.tool("find_slots", call, job_type="tune_up")
    assert found["ok"], found
    assert found["say"].startswith("The soonest I have is ")
    assert found["say"].endswith("Does that work?")
    assert found["say"].count(" to ") == 1, "one window, not a list"
    dates = [d["date"] for d in found["data"]["days"]]
    assert dates == sorted(dates), "days in date order"


def test_slots_offer_the_callers_day_first(agent):
    """Jackson: "why didn't it check friday first?"."""
    call = ready_to_book(agent)
    found = agent.tool("find_slots", call, job_type="tune_up", preferred_days=["Friday"])
    assert found["say"].startswith("Friday I have "), found["say"]
    assert found["data"]["offer"]["weekday"] == "Friday"


@pytest.mark.parametrize("day, date", [("today", "2026-11-02"), ("tomorrow", "2026-11-03"),
                                       ("Friday", "2026-11-06"), ("monday", "2026-11-02")])
def test_find_slots_takes_today_tomorrow_and_weekday_names_as_its_day(agent, day, date):
    """Retell call call_3f65a9aea68c561c5c7de616f9f: day "today" was refused as no date."""
    call = ready_to_book(agent)
    found = agent.tool("find_slots", call, job_type="tune_up", day=day)
    assert found["ok"], found
    assert {w["date"] for w in found["data"]["windows"]} <= {date}


def test_a_day_find_slots_cannot_read_is_asked_for(agent):
    call = ready_to_book(agent)
    found = agent.tool("find_slots", call, job_type="tune_up", day="someday")
    assert found["refused"] == "invalid_arguments"
    assert found["say"] == "Which day works for you?"


def test_preferred_today_offers_today_and_never_says_today_as_a_name(agent):
    """Retell call call_3f65a9aea68c561c5c7de616f9f: "I don't have Today open in the next two
    weeks. The soonest I have is today, 4 to 8."."""
    call = ready_to_book(agent)
    found = agent.tool("find_slots", call, job_type="tune_up", preferred_days=["today"])
    assert found["ok"], found
    assert "Today" not in found["say"][1:] and "don't have" not in found["say"]
    assert found["data"]["offer"]["date"] == "2026-11-02"


def test_live_call_2_tomorrow_at_two_is_the_window_that_holds_two(agent):
    """Live call 2: "Tomorrow at two PM." passed as arrive 1 PM to 5 PM, which no window fits,
    and the caller heard there were no openings."""
    call = ready_to_book(agent)
    agent.turn(call, "Yeah. Tomorrow at two PM.")
    found = agent.tool(
        "find_slots", call, job_type="tune_up", day="2026-11-03", arrive_after="1 PM",
        arrive_before="5 PM",
    )
    assert found["data"]["arrival"] == {"time": "14:00", "fits": True}
    assert [w["label"] for w in found["data"]["windows"]] == ["2 to 2:30"]
    assert found["say"] == "Tomorrow I have 2 to 2:30."


def test_a_named_day_full_this_week_says_so_and_offers_next_weeks_with_its_date():
    """Live call 2: "Friday the 13th, Saturday the 7th, or Monday the 9th", out of order, when this
    Friday was full."""
    from types import SimpleNamespace

    from app import tools
    from app.tenant import Tenant

    tenant = Tenant.load(TENANT_DIR)
    ctx = SimpleNamespace(tenant=tenant, now=datetime(2026, 11, 4, 15, 0, tzinfo=UTC))

    def slot(day: str, weekday: str, window: str, label: str) -> dict:
        return {"date": day, "window": window, "weekday": weekday, "day": weekday,
                "day_and_date": f"{weekday}, November {int(day[-2:])}", "label": label}

    by_day = {
        "2026-11-07": [slot("2026-11-07", "Saturday", "morning", "8 to 12")],
        "2026-11-13": [slot("2026-11-13", "Friday", "afternoon", "12 to 4")],
    }
    offer, say = tools._one_offer(ctx, by_day, sorted(by_day), {"friday"}, None)
    assert offer["date"] == "2026-11-13"
    assert say == "This Friday is full. Friday, November 13, I have 12 to 4. Does that work?"


def test_book_for_tomorrow_at_two_in_a_longer_line_is_the_window_that_holds_it(agent):
    """PR #155 check: "I want to book for tomorrow at 2" got fits False and every window."""
    call = ready_to_book(agent)
    agent.turn(call, "I want to book for tomorrow at 2")
    found = agent.tool(
        "find_slots", call, job_type="tune_up", arrive_after="1 PM", arrive_before="3 PM"
    )
    assert found["data"]["arrival"] == {"time": "14:00", "fits": True}
    assert [w["label"] for w in found["data"]["windows"]] == ["2 to 2:30"]
    assert found["say"] == "Tomorrow I have 2 to 2:30.", "the day comes from the caller's words"


def test_a_named_time_outside_visit_hours_says_the_hours_and_offers_the_nearest(agent):
    call = ready_to_book(agent)
    agent.turn(call, "Tomorrow at 8 PM works.")
    found = agent.tool("find_slots", call, job_type="tune_up", arrive_after="8 PM")
    assert found["data"]["arrival"]["fits"] is False
    assert found["say"] == "Our visits run from 8 AM to 8 PM. Tomorrow I have 7:30 to 8."


# ---- all five boroughs (Jackson's call on the Retell number) ------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        {"street": "199 E 4th St", "unit": "2", "city": "New York", "zip": "10009"},
        {"street": "2500 Grand Concourse", "unit": "", "city": "Bronx", "zip": "10458"},
    ],
)
def test_manhattan_and_the_bronx_are_in_the_area_and_book(agent, address):
    """199 E 4th St, 10009 was refused out_of_area: the zones listed only some Brooklyn, Queens and
    Staten Island ZIPs. Jackson: "any of 5 boroughs zip code should be in zone"."""
    call = ready_to_book(agent, address=address)
    assert agent.tool("check_service_area", call, zip=address["zip"])["ok"]
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked


def test_every_nyc_residential_zip_is_in_the_area():
    from app.tenant import Tenant

    area = Tenant.load(TENANT_DIR).service_area
    # MODZCTA's 214, and 11249, a USPS ZIP no census ZIP area has (Retell eval suite round 1).
    assert len(area) == 215
    for zip_code in ("10009", "10458", "11201", "11373", "10314", "10463", "11691", "11249"):
        assert zip_code in area, zip_code
    assert "07302" not in area and "11501" not in area


def test_a_new_caller_is_asked_their_name_right_after_the_read_back(agent):
    """A scripted call on #159 (call-_+16466240418_bUwDUCZFrKN8): "Perfect, booking that now.",
    then a refused booking asked "Can I get a name for the booking?"."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I'd like to book my furnace's yearly tune-up, please.")
    confirmed = agent.confirm_read_back(call, in_area_address())
    assert confirmed["say"] == "Thanks. And what name should I put the visit under?"
    agent.say(call, confirmed["say"])
    agent.turn(call, "Pat Rivera.")
    booked = book(agent, call, first_open(agent, call), name="Pat Rivera")
    assert booked["ok"], booked


def test_a_booking_with_no_name_asks_with_the_booking_name_line(agent):
    """Jackson's gas test call (call_0246b91f77e01a4b18583e6ca8b): the agent booked without asking
    the name, and the refusal asked "Can I get a name for the booking?"."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I'd like to book my furnace's yearly tune-up, please.")
    agent.confirm_read_back(call, in_area_address())
    refused = book(agent, call, first_open(agent, call), name="")
    assert refused["refused"] == "invalid_arguments"
    assert refused["say"] == "Thanks. And what name should I put the visit under?"


def test_a_name_passed_to_read_back_is_not_asked_again_and_goes_on_the_visit(agent, mock):
    """Jackson's Retell agent asks the name at the start of the call; the confirmed read-back
    still asked "what name should I put the visit under?", because the name only reached the
    server in book_appointment."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I'd like to book my furnace's yearly tune-up, please.")
    address = in_area_address()
    agent.turn(call, f"It's {address['street']}, {address['zip']}.")

    def read_back(**extra):
        body = {"call_id": call, "address": address, "phone": "caller_id", "name": "Jordan Lee"}
        return agent.client.post("/tools/read_back", json={**body, **extra}).json()

    asked = read_back()
    agent.say(call, asked["say"])
    agent.turn(call, "Yes, that's right.")
    confirmed = read_back(confirmed=True)
    assert confirmed["ok"] and confirmed["say"] == ""
    booked = book(agent, call, first_open(agent, call), name="")
    assert booked["ok"], booked


def test_a_new_caller_who_already_gave_a_name_is_not_asked_again(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Hi, this is Pat Rivera, I need a tune-up.")
    assert agent.confirm_read_back(call, in_area_address())["say"] == ""


def test_a_unit_and_the_zip_are_said_apart_in_the_read_back(agent):
    """Retell call call_1840576e622e3d729b760085142: "unit two one zero zero zero nine"."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 199 East 4th Street, unit 2, 10009.")
    address = {"street": "199 E 4th St", "unit": "2", "city": "New York", "zip": "10009"}
    asked = agent.tool("read_back", call, address=address, phone="caller_id")
    assert asked["data"]["sentence"].startswith("So that's one ninety-nine E Fourth St, unit two, and ")


def test_a_unit_heard_with_the_zip_on_the_end_keeps_only_the_unit(agent):
    """Retell call call_1840576e622e3d729b760085142: "Unit 2, 10009" was heard as "Unit 210009"."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 199 East 4th Street, Unit 210009.")
    agent.turn(call, "Yes, 10009.")
    address = {"street": "199 E 4th St", "unit": "210009", "city": "New York", "zip": "10009"}
    asked = agent.tool("read_back", call, address=address, phone="caller_id")
    assert asked["data"]["sentence"].startswith("So that's one ninety-nine E Fourth St, unit two, and ")


def test_a_caller_who_says_they_are_someone_else_is_booked_as_a_new_customer(
    agent, mock, scenarios
):
    """Retell call call_1840576e622e3d729b760085142: the number was on file under another name, the
    caller said "No, I'm Jordan Lee. I'm new.", was asked whose account a new address goes on, and
    was booked with the other customer as the contact. A caller who says they're someone else gets
    no account question, and the visit goes on a new account in their name alone."""
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, "No, I'm Jordan Lee. I'm new. Those aren't mine.")
    confirmed = agent.confirm_read_back(call, in_area_address(41))
    assert "account_choice" not in confirmed["data"]
    booked = agent.tool(
        "book_appointment",
        call,
        job_type="tune_up",
        slot=first_open(agent, call),
        details={**DETAILS, "name": "Jordan Lee"},
    )
    assert booked["ok"], booked
    customer = _mock_customer_of_job(mock, booked["data"]["booking_id"])
    assert customer["id"] != maria["customerId"]
    assert customer["name"] == "Jordan Lee"
    assert [c["memo"] for c in customer["contacts"]] == ["Jordan Lee"]


def test_a_named_half_hour_gets_a_window_from_then_cut_at_closing(agent):
    """Jackson on Retell call call_9826ff9f0dfa8bc835a3601368b asked for 7:30 and heard "4 to 8";
    a named time now starts the window at the half hour on or before it."""
    call = ready_to_book(agent)
    agent.turn(call, "Tomorrow at 7:30 PM works.")
    found = agent.tool("find_slots", call, job_type="tune_up")
    assert found["data"]["arrival"] == {"time": "19:30", "fits": True}
    assert found["say"] == "Tomorrow I have 7:30 to 8."


def test_a_caller_can_ask_for_a_shorter_window(agent):
    call = ready_to_book(agent)
    found = agent.tool("find_slots", call, job_type="tune_up", day="tomorrow", window_minutes=60)
    assert found["ok"], found
    assert all(
        int(w["window"][5:7]) * 60 + int(w["window"][7:]) - int(w["window"][:2]) * 60
        - int(w["window"][2:4]) == 60
        for w in found["data"]["windows"]
    )


def test_the_default_offer_is_the_earliest_open_30_minute_window(agent):
    call = ready_to_book(agent)
    found = agent.tool("find_slots", call, job_type="tune_up")
    window = found["data"]["offer"]["window"]
    start, end = int(window[:2]) * 60 + int(window[2:4]), int(window[5:7]) * 60 + int(window[7:])
    assert end - start == 30


def test_a_spelling_the_caller_never_said_leaves_the_street_alone(agent):
    """Slate call 4 (Retell call_f8a3ad3177fa2c62fc9db00dcfa): the model passed spelled "E, A, S,
    T, 4, T, H, S, T, R, E, E, T" for "199 East 4th Street", which the caller never spelled, and
    the read-back and the booking said "199 Eastthstreet Street"."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 199 East 4th Street, unit 2, 10009.")
    address = {"street": "199 East 4th Street", "unit": "2", "city": "New York", "zip": "10009",
               "spelled": "E, A, S, T, 4, T, H, S, T, R, E, E, T"}
    asked = agent.tool("read_back", call, address=address, phone="caller_id")
    assert asked["ok"], asked
    assert "one ninety-nine East Fourth Street, unit two" in asked["say"]
    assert "Eastth" not in asked["say"]


@pytest.mark.parametrize("caller", ["", "anonymous"])
@pytest.mark.parametrize("phone", ["caller_id", "the number I'm calling from"])
def test_a_call_with_no_number_asks_for_it_by_name(agent, caller, phone):
    """Retell's builder agent: a web call or a blocked caller ID has no number to read back."""
    call = agent.start(caller)["data"]["call_id"]
    agent.turn(call, "It's 101 Union St, 11231.")
    asked = agent.tool("read_back", call, address=in_area_address(), phone=phone)
    assert asked["refused"] == "no_caller_number"
    assert asked["say"] == "I can't see the number you're calling from. Could you read it out for me?"


def test_find_slots_takes_since_in_its_facts(agent):
    """Retell eval suite round 1: 16 find_slots calls refused facts.since as an unknown field."""
    call = ready_to_book(agent)
    found = agent.tool("find_slots", call, job_type="tune_up",
                       facts={"issue": "yearly tune-up", "since": "last week"})
    assert found["ok"], found
