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
    # The caller opened with the address, so only the name is asked for.
    assert asked["say"].endswith("could you tell me your name?")
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


# ---- the heads-up to the number on file ---------------------------------------------------------


def cancel_from_unknown_number(agent, scenarios) -> tuple[str, dict]:
    call, maria = unknown_caller_finds_maria(agent, scenarios)
    agent.turn(call, "It's Maria Delgado.")
    assert agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="fixed")["ok"]
    return call, maria


def heads_ups(agent) -> list:
    conn = agent.db()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM heads_ups ORDER BY id")]
    finally:
        conn.close()


def test_the_allowlist_parses_ten_digit_numbers_only():
    from app.heads_up import allowlist, may_dial

    allowed = allowlist({"HEADSUP_ALLOWLIST": "(212) 555-0100, 555-0101,,not a number"})
    assert allowed == frozenset({"2125550100"})
    assert may_dial("212.555.0100", allowed) and not may_dial("2125550199", allowed)
    assert allowlist({}) == frozenset()


def test_a_change_from_an_unknown_number_off_the_allowlist_is_never_dialed(agent, mock, scenarios):
    call, maria = cancel_from_unknown_number(agent, scenarios)
    [row] = heads_ups(agent)
    assert (row["status"], row["result"]) == ("would_call", "not on the allowlist")
    assert row["phone"] == "".join(c for c in maria["phone"] if c.isdigit())
    assert "was cancelled by a caller from another number" in row["line"]
    agent.end(call)
    assert agent.client.post("/outbound/claim", json={"worker": "w"}).json()["data"]["outbound"] is None
    notes = [r["text"] for r in st(mock, "SELECT text FROM notes WHERE job_id = ?", maria["jobId"])]
    assert any(n.startswith("Heads-up would have been sent to the number on file") for n in notes)
    html = signed_in(agent).client.get(f"/owner/calls/{call}").text
    assert "Heads-up would have been sent to the number on file at" in html


def test_a_known_number_gets_no_heads_up(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, "Cancel my visit please.")
    assert agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="fixed")["ok"]
    assert heads_ups(agent) == []


def test_a_failed_check_gets_no_heads_up(agent, scenarios):
    call, maria = unknown_caller_finds_maria(agent, scenarios)
    for _ in range(3):
        refused = agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="fixed")
        agent.say(call, refused["say"])
        agent.turn(call, "Joanne Price.")
    assert refused["refused"] == "identity_mismatch"
    assert heads_ups(agent) == []


def test_an_allowed_heads_up_is_dialed_after_the_call_once_and_noted(tmp_path, clock, mock,
                                                                    scenarios):
    from toolkit import Agent, make_server

    maria = scenarios["repeatCallerOpenJob"]
    allowed = frozenset({"".join(c for c in maria["phone"] if c.isdigit())})
    agent = Agent(make_server(tmp_path, clock, mock[0], headsup_allowlist=allowed), clock)
    call, _ = cancel_from_unknown_number(agent, scenarios)
    assert heads_ups(agent)[0]["status"] == "queued"
    # Not during the call.
    assert agent.client.post("/outbound/claim", json={"worker": "w"}).json()["data"]["outbound"] is None
    agent.end(call)
    claim = agent.client.post("/outbound/claim", json={"worker": "w"}).json()["data"]["outbound"]
    assert claim["kind"] == "heads_up" and claim["opening_line"].startswith("This is Summit Air.")
    assert agent.client.post("/outbound/claim", json={"worker": "w"}).json()["data"]["outbound"] is None
    done = agent.client.post(f"/outbound/heads-up/{claim['outbound_id']}/done",
                             json={"answered": False, "voicemail": True}).json()
    assert done["ok"] and done["data"]["result"] == "voicemail"
    again = agent.client.post(f"/outbound/heads-up/{claim['outbound_id']}/done",
                              json={"answered": True}).json()
    assert again["refused"] == "invalid_state", "one attempt"
    notes = [r["text"] for r in st(mock, "SELECT text FROM notes WHERE job_id = ?", maria["jobId"])]
    assert any(n.startswith("Heads-up sent to the number on file") and "voicemail" in n
               for n in notes)


def test_a_number_taken_off_the_allowlist_before_the_claim_is_not_dialed(tmp_path, clock, mock,
                                                                        scenarios):
    from toolkit import Agent, make_server

    maria = scenarios["repeatCallerOpenJob"]
    allowed = frozenset({"".join(c for c in maria["phone"] if c.isdigit())})
    app = make_server(tmp_path, clock, mock[0], headsup_allowlist=allowed)
    agent = Agent(app, clock)
    call, _ = cancel_from_unknown_number(agent, scenarios)
    agent.end(call)
    app.state.services.settings.headsup_allowlist = frozenset()
    assert agent.client.post("/outbound/claim", json={"worker": "w"}).json()["data"]["outbound"] is None
    assert heads_ups(agent)[0]["status"] == "would_call"


def test_a_customer_found_by_address_comes_back_without_name_or_numbers(agent, scenarios):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I need to cancel the visit at 620 Bedford Avenue, 11235.")
    found = agent.tool("find_customer", call,
                       address={"street": "620 Bedford Ave", "unit": "", "zip": "11235"})
    match = found["data"]["matches"][0]
    assert match["name"] == "" and match["phones"] == [] and match["withheld"]
    assert match["open_jobs"] and match["open_jobs"][0]["summary"] == ""
    agent.turn(call, "It's Maria Delgado.")
    again = agent.tool("find_customer", call,
                       address={"street": "620 Bedford Ave", "unit": "", "zip": "11235"})
    assert again["data"]["matches"][0]["name"] == "Maria Delgado"


def test_a_spouse_named_later_does_not_reopen_which_account(agent, mock, scenarios):
    """Eval run 20261104-200156, shared-phone-names-self: "my wife, Marisol" after "this is
    Hector" asked whose name the visit goes under."""
    call, address = shared_call(agent, scenarios, "Hi, this is Hector Ruiz, a tune-up please.")
    agent.confirm_read_back(call, address)
    agent.turn(call, "It's just me and my wife, Marisol, at home.")
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    owner = st(mock, "SELECT c.name FROM jobs j JOIN customers c ON c.id = j.customer_id "
                     "WHERE j.id = ?", booked["data"]["booking_id"])[0]
    assert owner["name"] == "Hector Ruiz"


def test_a_name_mentioned_in_passing_does_not_pick_the_account(agent, mock, scenarios):
    """Eval run 20261104-200759, shared-phone-asks-which: "I'm Marisol", then "Hector might have
    messed with the thermostat", booked under Hector."""
    call, address = shared_call(agent, scenarios, "Could we put it under my name though, I'm Marisol.")
    agent.confirm_read_back(call, address)
    agent.turn(call, "My husband Hector handles that. Hector might have messed with the thermostat.")
    booked = book(agent, call, first_open(agent, call))
    owner = st(mock, "SELECT c.name FROM jobs j JOIN customers c ON c.id = j.customer_id "
                     "WHERE j.id = ?", booked["data"]["booking_id"])[0]
    assert owner["name"] == "Marisol Ruiz"


def test_a_movers_cancel_goes_through_record_move_first(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, "We moved to Hoboken last week, please cancel the visit.")
    first = agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="moved")
    assert first["refused"] == "move_first"
    # Asked once: a caller who won't give the new address can still cancel.
    assert agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="moved")["ok"]


def test_a_callback_after_an_unmatched_answer_goes_to_the_number_on_file(agent, scenarios):
    call, maria = unknown_caller_finds_maria(agent, scenarios)
    asked = agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="fixed")
    assert asked["refused"] == "identity_check"
    agent.say(call, asked["say"])
    agent.turn(call, "I'm Joanne Price, I don't know whose name it's under.")
    flagged = agent.tool("flag_person", call, key="f1", reason="Wants the visit cancelled",
                         priority="Normal", callback_in_minutes=30)
    assert "number we have on file" in flagged["say"]
    assert count(agent, "SELECT COUNT(*) FROM identity_checks WHERE status = 'mismatch'") == 1


def test_a_number_that_matches_nobody_falls_back_to_the_address(agent, scenarios):
    call = agent.start("(212) 555-0151")["data"]["call_id"]
    agent.turn(call, "It's 620 Bedford Avenue, 11235.")
    found = agent.tool("find_customer", call, phone="(212) 555-0151",
                       address={"street": "620 Bedford Ave", "unit": "", "zip": "11235"})
    assert [m["customer_id"] for m in found["data"]["matches"]] == [
        scenarios["repeatCallerOpenJob"]["customerId"]
    ]


# ---- the name and address check, on half the information (PR #134 review) ----------------------


def rename_maria(mock, scenarios, name: str, unit: str = "") -> None:
    maria = scenarios["repeatCallerOpenJob"]
    conn = sqlite3.connect(mock[0].state.db_path)
    with conn:
        conn.execute("UPDATE customers SET name = ? WHERE id = ?", (name, maria["customerId"]))
        conn.execute("UPDATE locations SET unit = ?, unit_key = ? WHERE id = ?",
                     (unit, unit.lower(), maria["locationId"]))
    conn.close()


def unknown_caller_cancels(agent, scenarios, line: str, unit: str = "") -> dict:
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, line)
    agent.tool("find_customer", call,
               address={"street": "620 Bedford Ave", "unit": unit, "zip": "11235"})
    return agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="not needed")


def test_a_surname_that_is_the_street_name_is_not_a_name(agent, mock, scenarios):
    rename_maria(mock, scenarios, "Maria Bedford")
    refused = unknown_caller_cancels(
        agent, scenarios, "Cancel the visit at 620 Bedford Avenue, 11235."
    )
    assert refused["refused"] == "identity_check"
    assert refused["say"].endswith("could you tell me your name?")


def test_a_surname_said_in_passing_is_not_a_name(agent, mock, scenarios):
    rename_maria(mock, scenarios, "Maria Price")
    refused = unknown_caller_cancels(
        agent, scenarios, "What's the price to cancel the visit at 620 Bedford Avenue, 11235?"
    )
    assert refused["refused"] == "identity_check"


def test_a_street_name_surname_counts_when_given_as_a_name(agent, mock, scenarios):
    rename_maria(mock, scenarios, "Maria Washington")
    assert unknown_caller_cancels(
        agent, scenarios,
        "This is Maria Washington, cancel the visit at 620 Bedford Avenue, 11235.",
    )["ok"]


def test_a_hudson_living_on_hudson_counts_only_by_name(agent, mock, scenarios):
    from app.tools import _name_given

    assert not _name_given("I live at 71 Hudson Street", "Ana Hudson")
    assert not _name_given("it's on Hudson Avenue", "Ana Hudson")
    assert _name_given("my name is Hudson", "Ana Hudson")
    assert _name_given("Ana Hudson.", "Ana Hudson")
    assert _name_given("um, it's under Hudson", "Ana Hudson")
    assert _name_given("Del Gado", "Maria Delgado")


def test_the_unit_on_file_must_be_the_one_the_caller_said(agent, mock, scenarios):
    rename_maria(mock, scenarios, "Maria Delgado", unit="3B")
    wrong = unknown_caller_cancels(
        agent, scenarios, "Maria Delgado, 620 Bedford apartment 9Z, 11235, cancel my visit.",
        unit="3B",
    )
    assert wrong["refused"] == "identity_check"
    assert wrong["say"].endswith("could you tell me the address?")


def test_the_unit_said_in_words_counts(agent, mock, scenarios):
    rename_maria(mock, scenarios, "Maria Delgado", unit="3B")
    assert unknown_caller_cancels(
        agent, scenarios,
        "Maria Delgado, 620 Bedford Avenue apartment three B, 11235, cancel my visit.", unit="3B",
    )["ok"]


def test_a_malformed_allowlist_entry_never_becomes_another_number():
    from app.heads_up import allowlist

    allowed = allowlist({"HEADSUP_ALLOWLIST": "718-555-0142 ext 5, +44 20 7946 0958, "
                                              "1 (646) 555-0199, 646555019"})
    assert allowed == frozenset({"6465550199"})


def test_the_heads_up_never_dials_the_number_the_change_came_from(agent, mock, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    conn = sqlite3.connect(mock[0].state.db_path)
    with conn:
        conn.execute("UPDATE contacts SET value = ?, phone_key = ? WHERE customer_id = ?",
                     ("(212) 555-0999", "2125550999", maria["customerId"]))
    conn.close()
    # The caller's number is now the one on file, but the call matched by address: no heads-up.
    from app import heads_up

    services = agent.app.state.services
    call = agent.start("(212) 555-0999")["data"]["call_id"]
    db = agent.db()
    row = db.execute("SELECT * FROM calls WHERE id = ?", (call,)).fetchone()
    tenant = services.tenants.by_slug["summit-air"]
    assert heads_up.queue(db, tenant, services.fsm["summit-air"], row, maria["customerId"], None,
                          "(212) 555-0999", "x", frozenset({"2125550999"}), services.clock()) is None
    db.close()


def test_notes_and_job_details_wait_for_the_full_check(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "This is Maria Delgado, I need to cancel a visit.")
    found = agent.tool("find_customer", call,
                       address={"street": "620 Bedford Ave", "unit": "", "zip": "11235"})
    match = found["data"]["matches"][0]
    # The name she gave shows; the gate code note and the visit's summary don't, yet.
    assert match["name"] == "Maria Delgado"
    assert match["notes"] == [] and match["open_jobs"][0]["summary"] == ""
    assert match["open_jobs"][0]["window"] is None, "the visit's day and time wait too"
    agent.turn(call, "It's 620 Bedford Avenue, 11235.")
    assert agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="not needed")["ok"]
    again = agent.tool("find_customer", call,
                       address={"street": "620 Bedford Ave", "unit": "", "zip": "11235"})
    assert again["data"]["matches"][0]["notes"]


# ---- second review (PR #134) --------------------------------------------------------------------


def test_history_shows_nothing_until_the_check_passes(agent, scenarios):
    call, maria = unknown_caller_finds_maria(agent, scenarios)
    for what in ("jobs", "equipment"):
        history = agent.tool("get_customer_history", call, customer_id=maria["customerId"],
                             what=what)
        assert history["ok"] and history["data"]["items"] == [] and history["data"]["withheld"]
    agent.turn(call, "It's Maria Delgado.")
    assert agent.tool("cancel_appointment", call, booking_id=maria["jobId"], reason="fixed")["ok"]
    after = agent.tool("get_customer_history", call, customer_id=maria["customerId"], what="jobs")
    assert after["data"]["items"]


def test_a_name_at_the_start_of_the_line_counts():
    from app.tools import _name_given

    assert _name_given("Bedford, 620 Bedford Ave apartment 3B", "Maria Bedford")
    assert _name_given("Bedford, 620 Bedford Ave", "Maria Bedford")
    assert _name_given("Delgado 620 Bedford", "Maria Delgado")
    assert _name_given("um, Delgado, it's 620 Bedford", "Maria Delgado")
    # Still no name: the street alone, and a word in passing.
    assert not _name_given("Cancel the visit at 620 Bedford Avenue", "Maria Bedford")
    assert not _name_given("Bedford Avenue, 620", "Maria Bedford")
    assert not _name_given("What's the price to cancel?", "Maria Price")


def test_a_surname_that_is_an_everyday_word_needs_the_first_name():
    from app.tools import _name_given

    assert not _name_given("This is price gouging", "Maria Price")
    assert not _name_given("It's long overdue", "Ana Long")
    assert not _name_given("Price is too high", "Maria Price")
    assert _name_given("This is Maria Price", "Maria Price")
    assert _name_given("Price.", "Maria Price")


def test_a_unit_letter_needs_a_unit_cue():
    from app.tools import _unit_said

    def rows(text):
        return [{"call_id": "c", "seq": 1, "text": text}]

    assert not _unit_said(rows("I need a visit at 620 Bedford"), "A")
    assert _unit_said(rows("620 Bedford, apartment A"), "A")
    assert _unit_said(rows("unit 12, 620 Bedford"), "12")
    assert not _unit_said(rows("I've had it 12 days, 620 Bedford"), "12")
    assert _unit_said(rows("620 Bedford 3B"), "3B")


def test_may_dial_compares_the_whole_number():
    from app.heads_up import may_dial

    allowed = frozenset({"2079460958"})
    assert not may_dial("+44 20 7946 0958", allowed)
    assert may_dial("(207) 946-0958", allowed) and may_dial("1 207 946 0958", allowed)
    assert not may_dial("207-946-0958 ext 5", allowed)
