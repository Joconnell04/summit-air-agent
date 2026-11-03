"""Booking gates: read-back, idempotency and the race, commercial, out of area, intake fields,
caller ID shorthand, reschedule and cancel ownership (spec S-05, S-13, S-15, S-25 to S-28, S-31)."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
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

AGENT = load_agent(TENANT_DIR)[0]

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
        "Let me read that back: your home at 101 Union St, Brooklyn, 11231, and the best number "
        "is 212, 555, 0999. Is that right?"
    )


def test_a_caller_who_answers_the_read_back_with_a_business_cannot_book(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The heat's out.")
    agent.tool("read_back", call, address=in_area_address(), phone="caller_id")
    agent.turn(call, "Well, it's a business, it's our office upstairs.")
    agent.tool("read_back", call, address=in_area_address(), phone="caller_id", confirmed=True)
    assert book(agent, call, first_open(agent, call))["refused"] == "customer_type"


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
        assert "works on homes and apartments" in refused["say"]
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
        slot={"date": "2026-11-06", "window": "morning"},
        reason="again",
    )
    assert again["refused"] == "limit"


def test_a_cancel_needs_a_reason_and_offers_two_windows(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    no_reason = agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="")
    assert no_reason["refused"] == "invalid_arguments"
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
TOMORROW_MORNING = {"date": "2026-11-03", "window": "morning"}


def test_after_hours_a_cold_house_alerts_the_on_call_technician_before_booking(agent, clock):
    """Eval round 1, after-hours-no-heat-cold: 48 F inside at night was booked for the morning
    with no on-call alert."""
    clock.advance(**AFTER_HOURS)
    call = ready_to_book(agent)
    agent.turn(call, "It's 48 in here and dropping. Just two healthy adults at home.")
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 48})
    assert found["data"]["after_hours_outcome"] == "tonight_if_on_call_agrees"
    refused = book(agent, call, TOMORROW_MORNING, job_type="no_heat", inside_temp_f=48)
    assert refused["refused"] == "on_call_first"
    # No time to ask for: the on-call technician calls back within the tenant's set minutes.
    flagged = agent.tool("flag_person", call, reason="On-call: 48 F inside", priority="Urgent")
    assert flagged["ok"], flagged
    assert flagged["data"]["callback_at"] == iso(clock() + timedelta(minutes=20))
    assert "on-call" in flagged["say"]
    assert book(agent, call, TOMORROW_MORNING, job_type="no_heat", inside_temp_f=48)["ok"]


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
    refused = book(agent, call, TOMORROW_MORNING, job_type="no_cooling", inside_temp_f=88)
    assert refused["refused"] == "on_call_first"
    agent.tool("flag_person", call, reason="On-call: 88 F inside, no AC", priority="Urgent")
    assert book(agent, call, TOMORROW_MORNING, job_type="no_cooling", inside_temp_f=88)["ok"]


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


def test_an_open_visit_at_another_address_does_not_block_a_booking(agent, scenarios):
    call = agent.start(scenarios["repeatCallerOpenJob"]["phone"])["data"]["call_id"]
    agent.turn(call, "Hi, the heat's out at my other place.")
    agent.confirm_read_back(call, in_area_address(41))
    assert book(agent, call, first_open(agent, call))["ok"]


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
    assert refused["refused"] == "not_owner"
    # Confirming the visit's address in the read-back is what a call-taker would ask for.
    agent.turn(other, "It's for my mother's place.")
    agent.confirm_read_back(other, in_area_address(30))
    assert agent.tool("cancel_appointment", other, booking_id=job_id, reason="not needed")["ok"]


def test_a_limit_flags_a_person_once_however_often_it_is_hit(agent):
    call = ready_to_book(agent)
    assert book(agent, call, first_open(agent, call))["ok"]
    for _ in range(3):
        assert book(agent, call, ONE_LEFT)["refused"] == "limit"
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
