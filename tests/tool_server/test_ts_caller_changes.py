"""Caller changes (issue #128): a caller who moved, the name and address check before a change from
a number not on the account, another number for the visit, and a number on two accounts."""

import json
import sqlite3

from test_ts_owner_view import signed_in
from toolkit import UNKNOWN, book, count, first_open, in_area_address, ready_to_book

from app import booking, summary

NEW_HOME = {"street": "380 Washington Ave", "unit": "", "city": "Brooklyn", "zip": "11238"}
HOBOKEN = {"street": "71 Hudson St", "unit": "", "city": "Hoboken", "zip": "07030"}
ONE_LEFT = {"date": "2026-11-05", "window": "morning"}


def st(mock, sql: str, *params) -> list[sqlite3.Row]:
    conn = sqlite3.connect(mock[0].state.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def maria_moves(agent, scenarios, line: str, address: dict) -> str:
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, line)
    agent.confirm_read_back(call, address)
    return call


# ---- a caller who moved -------------------------------------------------------------------------


def test_a_move_in_the_area_adds_a_location_retires_the_old_one_and_moves_the_visit(
    agent, mock, scenarios
):
    maria = scenarios["repeatCallerOpenJob"]
    call = maria_moves(agent, scenarios, "Hi, we just moved, so the visit should go to the new place.",
                       NEW_HOME)
    moved = agent.tool("record_move", call, key="m1")
    assert moved["ok"], moved
    assert moved["data"]["location"] == "added"
    assert moved["data"]["jobs_moved"] == [maria["jobId"]]
    assert moved["say"].startswith("Thanks for letting us know. I've updated your address to")
    assert "now set for the new address" in moved["say"]

    old = st(mock, "SELECT * FROM locations WHERE id = ?", maria["locationId"])[0]
    assert old["active"] == 0, "the old location is kept, inactive, with its history"
    new = st(mock, "SELECT * FROM locations WHERE id = ?", moved["data"]["new_location_id"])[0]
    assert (new["street"], new["zip"], new["active"]) == ("380 Washington Ave", "11238", 1)
    job = st(mock, "SELECT * FROM jobs WHERE id = ?", maria["jobId"])[0]
    assert job["location_id"] == new["id"]
    notes = [r["text"] for r in st(mock, "SELECT text FROM notes WHERE job_id = ?", job["id"])]
    assert any("Go to the new address" in n for n in notes)
    # The customer's address now reads as the new one.
    again = agent.start(maria["phone"])["data"]["customers"][0]
    assert [loc["street"] for loc in again["locations"]] == ["380 Washington Ave"]
    assert again["open_jobs"][0]["street"] == "380 Washington Ave"


def test_the_confirmed_read_back_of_a_mover_asks_for_record_move_not_whose_account(
    agent, scenarios
):
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, "I moved to a new apartment last week.")
    confirmed = agent.confirm_read_back(call, NEW_HOME)
    assert "account_choice" not in confirmed["data"]
    assert "record_move" in confirmed["data"]["need"]
    assert book(agent, call, first_open(agent, call))["refused"] == "move_first"


def test_a_move_out_of_the_area_cancels_the_old_visit_and_changes_nothing_else(
    agent, mock, scenarios
):
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, "We moved to Hoboken, 07030, so I don't need the visit at the old place.")
    # The decline waits for record_move, which says it with the cancellation.
    area = agent.tool("check_service_area", call, zip="07030")
    assert area["refused"] == "out_of_area" and area["say"] == ""
    agent.confirm_read_back(call, HOBOKEN)
    moved = agent.tool("record_move", call, key="m1")
    assert moved["ok"], moved
    assert moved["data"]["in_area"] is False
    assert moved["data"]["jobs_cancelled"] == [maria["jobId"]]
    assert "outside the area we cover" in moved["say"]
    assert "cancelled the visit you had at the old address" in moved["say"]
    job = st(mock, "SELECT status FROM jobs WHERE id = ?", maria["jobId"])[0]
    assert job["status"] == "Canceled"
    locations = st(mock, "SELECT * FROM locations WHERE customer_id = ?", maria["customerId"])
    assert [(loc["id"], loc["active"]) for loc in locations] == [(maria["locationId"], 1)]
    notes = [r["text"] for r in st(mock, "SELECT text FROM notes WHERE customer_id = ? AND job_id "
                                         "IS NULL", maria["customerId"])]
    assert any("outside the service area" in n for n in notes)
    assert count(agent, "SELECT COUNT(*) FROM out_of_area WHERE zip = '07030'") == 1


def test_record_move_needs_the_caller_to_have_said_they_moved(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, "I moved the thermostat and now the heat won't come on.")
    agent.confirm_read_back(call, NEW_HOME)
    assert agent.tool("record_move", call)["refused"] == "move_not_said"


def test_a_move_needs_the_new_address_read_back_first(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, "We just moved to 380 Washington Avenue.")
    assert agent.tool("record_move", call)["refused"] == "gate_read_back"


def test_nothing_is_booked_or_moved_to_the_old_address_after_a_move(agent, mock, scenarios, clock):
    maria = scenarios["repeatCallerOpenJob"]
    call = maria_moves(agent, scenarios, "We just moved.", NEW_HOME)
    assert agent.tool("record_move", call, key="m1")["ok"]
    agent.end(call)
    # A later call books the old address: refused, not a new location there.
    later = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(later, "The heat's out at 620 Bedford Ave.")
    old = st(mock, "SELECT * FROM locations WHERE id = ?", maria["locationId"])[0]
    agent.confirm_read_back(later, {"street": old["street"], "unit": "", "city": "Brooklyn",
                                    "zip": old["zip"]})
    refused = book(agent, later, first_open(agent, later), account="caller")
    assert refused["refused"] == "location_superseded", refused
    assert len(st(mock, "SELECT * FROM locations WHERE customer_id = ?", maria["customerId"])) == 2


def test_a_job_left_at_a_retired_location_is_never_rescheduled(agent, mock, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    conn = sqlite3.connect(mock[0].state.db_path)
    with conn:
        conn.execute("UPDATE locations SET active = 0 WHERE id = ?", (maria["locationId"],))
    conn.close()
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, "Can I move my visit to Thursday morning?")
    refused = agent.tool(
        "reschedule_appointment", call, booking_id=maria["jobId"], slot=ONE_LEFT, reason="work"
    )
    assert refused["refused"] == "location_superseded"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'person'") == 1


def test_a_queued_booking_for_the_old_address_is_refused_when_sent(agent, mock, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    conn = sqlite3.connect(mock[0].state.db_path)
    with conn:
        conn.execute("UPDATE locations SET active = 0 WHERE id = ?", (maria["locationId"],))
        old = conn.execute("SELECT street, zip FROM locations WHERE id = ?",
                           (maria["locationId"],)).fetchone()
    conn.close()
    fsm = agent.app.state.services.fsm["summit-air"]
    request = {"address": {"street": old[0], "unit": "", "city": "", "zip": old[1]},
               "scoped_customer_ids": [maria["customerId"]]}
    try:
        booking._customer_and_location(fsm, None, request, "k")
    except booking.FsmRejected as exc:
        assert exc.code == "location_superseded"
    else:
        raise AssertionError("a booking at the old address went through")


# ---- the name and address check -----------------------------------------------------------------


def unknown_caller_finds_maria(agent, scenarios) -> tuple[str, dict]:
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Hi, I need to cancel the heating visit at 620 Bedford Avenue, 11235.")
    found = agent.tool("find_customer", call,
                       address={"street": "620 Bedford Ave", "unit": "", "zip": "11235"})
    assert [m["customer_id"] for m in found["data"]["matches"]] == [maria["customerId"]]
    return call, maria


def test_an_unknown_number_cancels_after_saying_the_name_and_address(agent, mock, scenarios):
    call, maria = unknown_caller_finds_maria(agent, scenarios)
    asked = agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="fixed")
    assert asked["refused"] == "identity_check"
    assert "confirm you're the person for the visit" in asked["say"]
    assert "Delgado" not in asked["say"] and "Bedford" not in asked["say"]
    agent.say(call, asked["say"])
    agent.turn(call, "Sure, it's Maria Delgado.")
    cancelled = agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="fixed")
    assert cancelled["ok"], cancelled
    notes = [r["text"] for r in st(mock, "SELECT text FROM notes WHERE job_id = ?", maria["jobId"])]
    assert any(n.startswith("Changed from an unknown number after a name and address check") for n in notes)


def test_a_wrong_name_changes_nothing_and_the_callback_goes_to_the_number_on_file(
    agent, mock, scenarios
):
    call, maria = unknown_caller_finds_maria(agent, scenarios)
    asked = agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="fixed")
    agent.say(call, asked["say"])
    agent.turn(call, "It's Joanne Price.")
    again = agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="fixed")
    # The address was said; only the name is asked for again.
    assert again["refused"] == "identity_check" and again["say"] == "Thanks. And what's the name on the visit?"
    agent.say(call, again["say"])
    agent.turn(call, "Joanne Price, like I said.")
    refused = agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="fixed")
    assert refused["refused"] == "identity_mismatch"
    assert "number we have on file" in refused["say"]
    # Refused for the rest of the call, whatever is said next.
    agent.turn(call, "Oh sorry, I mean Maria Delgado.")
    assert agent.tool("cancel_appointment", call, booking_id=maria["jobId"],
                      reason="fixed")["refused"] == "identity_mismatch"
    assert st(mock, "SELECT status FROM jobs WHERE id = ?", maria["jobId"])[0]["status"] == "Scheduled"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'identity_mismatch'") == 1
    agent.turn(call, "Yes, have them call.")
    flagged = agent.tool("flag_person", call, key="f1", reason="Asked to cancel a visit",
                         priority="Normal", callback_in_minutes=30)
    assert flagged["ok"], flagged
    assert "number we have on file" in flagged["say"]
    conn = agent.db()
    phone = conn.execute("SELECT phone_key FROM callbacks WHERE call_id = ?", (call,)).fetchone()
    conn.close()
    assert phone[0] == "".join(c for c in maria["phone"] if c.isdigit())


def test_details_the_agent_said_first_do_not_count(agent, scenarios):
    call, maria = unknown_caller_finds_maria(agent, scenarios)
    agent.say(call, "Is this Maria Delgado?")
    agent.turn(call, "Yes, Maria Delgado.")
    asked = agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="fixed")
    assert asked["refused"] == "identity_check"


def test_a_known_number_skips_the_check(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, "I need to cancel my visit.")
    assert agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="fixed")["ok"]
    assert count(agent, "SELECT COUNT(*) FROM identity_checks") == 0


def test_a_move_from_an_unknown_number_needs_the_check_too(agent, scenarios):
    call, _ = unknown_caller_finds_maria(agent, scenarios)
    agent.turn(call, "Actually we just moved, so update my address instead.")
    agent.confirm_read_back(call, NEW_HOME)
    assert agent.tool("record_move", call)["refused"] == "identity_check"


# ---- another number for the visit ---------------------------------------------------------------


def test_a_daughters_number_is_the_jobs_contact_and_the_account_keeps_its_own(
    agent, mock, scenarios
):
    brennan = scenarios["jobDoneLastWeek"]
    call = agent.start(brennan["phone"])["data"]["call_id"]
    agent.turn(call, "Book a tune-up, and call my daughter Ana at 718 555 0142 instead.")
    home = agent.start(brennan["phone"])["data"]["customers"][0]["locations"][0]
    address = {"street": home["street"], "unit": home["unit"], "city": home["city"],
               "zip": home["zip"]}
    agent.turn(call, f"It's {address['street']}, {address['zip']}.")
    asked = agent.tool("read_back", call, address=address, phone="7185550142",
                       phone_for="my daughter Ana")
    assert "the number to reach my daughter Ana is seven one eight" in asked["say"]
    agent.say(call, asked["say"])
    agent.turn(call, "Yes.")
    assert agent.tool("read_back", call, address=address, phone="7185550142",
                      phone_for="my daughter Ana", confirmed=True)["ok"]
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    job = st(mock, "SELECT * FROM jobs WHERE id = ?", booked["data"]["booking_id"])[0]
    assert "Contact: my daughter Ana, (718) 555-0142" in job["summary"]
    assert "Account phone: " in job["summary"]
    keys = [r["phone_key"] for r in st(mock, "SELECT phone_key FROM contacts WHERE customer_id = ?",
                                       brennan["customerId"])]
    assert "7185550142" not in keys and keys


def test_a_new_caller_keeps_their_own_number_with_someone_elses_on_the_visit(agent, mock):
    call = agent.start("(212) 555-0944")["data"]["call_id"]
    agent.turn(call, "The heat's out. Please call my son Leo at 917 555 0123, he'll be home.")
    address = in_area_address(77)
    agent.turn(call, f"It's {address['street']}, {address['zip']}.")
    asked = agent.tool("read_back", call, address=address, phone="9175550123", phone_for="my son Leo")
    agent.say(call, asked["say"])
    agent.turn(call, "Yes.")
    agent.tool("read_back", call, address=address, phone="9175550123", phone_for="my son Leo",
               confirmed=True)
    booked = book(agent, call, first_open(agent, call), name="Rita Moss")
    assert booked["ok"], booked
    job = st(mock, "SELECT * FROM jobs WHERE id = ?", booked["data"]["booking_id"])[0]
    keys = [r["phone_key"] for r in st(mock, "SELECT phone_key FROM contacts WHERE customer_id = ?",
                                       job["customer_id"])]
    assert keys == ["2125550944"]
    assert "Contact: my son Leo, (917) 555-0123" in job["summary"]


def test_whom_the_number_reaches_comes_from_the_caller(agent):
    call = ready_to_book(agent)
    refused = agent.tool("read_back", call, address=in_area_address(), phone="7185550142",
                         phone_for="my daughter Ana")
    assert refused["refused"] == "invalid_arguments"


# ---- one number, two accounts -------------------------------------------------------------------


def shared_call(agent, scenarios, opening: str) -> tuple[str, dict]:
    shared = scenarios["sharedPhone"]
    call = agent.start(shared["phone"])["data"]["call_id"]
    agent.turn(call, opening)
    address = {"street": shared["street"], "unit": "", "city": "Brooklyn", "zip": shared["zip"]}
    return call, address


def test_a_number_on_two_accounts_asks_whose_name_the_visit_goes_under(agent, mock, scenarios):
    call, address = shared_call(agent, scenarios, "Hi, the heat's not working at the house.")
    confirmed = agent.confirm_read_back(call, address)
    assert confirmed["data"]["account_which"] is True
    assert confirmed["say"] == ("I see more than one account on this number. Whose name should the "
                                "visit go under?")
    assert book(agent, call, first_open(agent, call))["refused"] == "account_which"
    agent.turn(call, "Put it under Marisol.")
    booked = book(agent, call, first_open(agent, call), name="")
    assert booked["ok"], booked
    owner = st(mock, "SELECT c.name FROM jobs j JOIN customers c ON c.id = j.customer_id "
                     "WHERE j.id = ?", booked["data"]["booking_id"])[0]
    assert owner["name"] == "Marisol Ruiz"


def test_a_name_said_earlier_settles_which_account(agent, mock, scenarios):
    call, address = shared_call(agent, scenarios, "Hi, this is Hector, the heat's not working.")
    confirmed = agent.confirm_read_back(call, address)
    assert "account_which" not in confirmed["data"]
    booked = book(agent, call, first_open(agent, call))
    owner = st(mock, "SELECT c.name FROM jobs j JOIN customers c ON c.id = j.customer_id "
                     "WHERE j.id = ?", booked["data"]["booking_id"])[0]
    assert owner["name"] == "Hector Ruiz"


def test_the_moved_phrases_are_about_the_caller_moving_house(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    for line, moved in (
        ("My tenant moved out last month.", False),
        ("Can you move my appointment to Friday?", False),
        ("We've moved to Queens.", True),
        ("I don't live there anymore, I have a new address.", True),
    ):
        call = agent.start(maria["phone"])["data"]["call_id"]
        agent.turn(call, line)
        agent.confirm_read_back(call, NEW_HOME)
        refused = agent.tool("record_move", call)
        assert (refused["refused"] != "move_not_said") is moved, line
        agent.end(call)


def test_a_caller_who_moved_in_years_ago_books_their_own_home_as_before(agent, scenarios):
    """"We just moved in two years ago" is no move when the address is the one on file."""
    brennan = scenarios["jobDoneLastWeek"]
    call = agent.start(brennan["phone"])["data"]["call_id"]
    agent.turn(call, "We just moved in two years ago and the boiler needs a tune-up.")
    home = agent.start(brennan["phone"])["data"]["customers"][0]["locations"][0]
    confirmed = agent.confirm_read_back(
        call, {"street": home["street"], "unit": home["unit"], "city": "", "zip": home["zip"]}
    )
    assert "record_move" not in str(confirmed["data"].get("need"))
    assert book(agent, call, first_open(agent, call))["ok"]


def test_the_owner_view_and_the_technician_summary_show_both_numbers(agent):
    call = agent.start("(212) 555-0944")["data"]["call_id"]
    agent.turn(call, "Please call my son Leo at 917 555 0123 for the visit.")
    address = in_area_address(78)
    agent.turn(call, f"It's {address['street']}, {address['zip']}.")
    asked = agent.tool("read_back", call, address=address, phone="9175550123", phone_for="my son Leo")
    agent.say(call, asked["say"])
    agent.turn(call, "Yes.")
    agent.tool("read_back", call, address=address, phone="9175550123", phone_for="my son Leo",
               confirmed=True)
    assert book(agent, call, first_open(agent, call), name="Rita Moss")["ok"]
    agent.end(call)
    html = signed_in(agent).client.get(f"/owner/calls/{call}").text
    assert "my son Leo, (917) 555-0123" in html and "(212) 555-0944" in html
    conn = agent.db()
    request = conn.execute("SELECT request_json FROM bookings WHERE call_id = ?", (call,)).fetchone()
    conn.close()
    assert summary.contact_lines(json.loads(request[0])) == [
        ["Contact", "my son Leo, (917) 555-0123; account phone (212) 555-0944"]
    ]


def test_a_question_from_a_dropped_call_is_asked_again(agent, scenarios):
    call, maria = unknown_caller_finds_maria(agent, scenarios)
    asked = agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="fixed")
    assert asked["refused"] == "identity_check"
    agent.end(call, "dropped")
    agent.clock.advance(minutes=2)
    again = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(again, "Sorry, we got cut off.")
    refused = agent.tool("cancel_appointment", again, booking_id=maria["jobId"], reason="fixed")
    # The calls are linked, so Maria is in scope, and the question is asked on this call.
    assert refused["refused"] == "identity_check"
    assert count(agent, "SELECT COUNT(*) FROM identity_checks WHERE call_id = ?", again) == 1
    agent.say(again, refused["say"])
    agent.turn(again, "Maria Delgado.")
    assert agent.tool("cancel_appointment", again, booking_id=maria["jobId"], reason="fixed")["ok"]
