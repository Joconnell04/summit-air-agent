"""The booking flow paths from issue #129: a correction after a confirmed read-back, a visit whose
window has ended, commercial phrasings, work the company doesn't do, two jobs on one call and a
request for a technician by name (DECISIONS.md 077 to 079)."""

import json

import pytest
from toolkit import TENANT_DIR, UNKNOWN, book, first_open, in_area_address, ready_to_book

from app import asks, readback
from app.jobs import retry_queue
from app.lifecycle import sounds_commercial
from app.tenant import load_agent
from prompts.assemble import SpokenReply, call_notes, server_notes

READ_BACK = {"street": "142 Main St", "unit": "", "city": "Brooklyn", "zip": "11231",
             "phone": "2125550999"}


# ---- a correction after a confirmed read-back ---------------------------------------------


@pytest.mark.parametrize(
    "line, field",
    [
        ("Yes, but it's 4B.", "unit"),
        ("Yes, apartment 4B.", "unit"),
        ("Yeah, but it's 144.", "house number"),
        ("No, it's 144 Main Street.", "house number"),
        ("Yes, it's Union Street actually.", "street"),
        ("Yes, the ZIP is 11232.", "ZIP"),
        ("Yes, but my cell is 718 555 0142.", "phone"),
    ],
)
def test_a_line_that_changes_a_read_back_detail_is_a_change(line, field):
    assert readback.change(line, READ_BACK) == field


@pytest.mark.parametrize(
    "line",
    [
        "Yes, that's right.",
        "Yep, 142 Main.",
        "Yes, thanks.",
        "Yes, 142 Main Street, 11231.",
        "Yes, it's been out 3 days.",
        "Yes, I'm on the 4th floor.",
        "Yes, it's off Flatbush Avenue.",
        "Yes, the number is 212 555 0999.",
    ],
)
def test_a_plain_yes_or_the_same_value_again_is_no_change(line):
    assert readback.change(line, READ_BACK) is None


def test_a_queens_house_number_and_its_numbered_street_are_not_read_as_changes():
    queens = {**READ_BACK, "street": "188-10 64th Ave", "unit": "2A", "zip": "11366"}
    assert readback.change("Yes, 188-10 64th Avenue, 2A.", queens) is None
    assert readback.change("Yes, but it's 65th Avenue.", queens) == "street"
    assert readback.change("Yes, but it's 188-12.", queens) == "house number"


def test_a_unit_given_after_the_confirmation_resets_it_and_the_booking_waits(agent):
    call = ready_to_book(agent)
    turned = agent.turn(call, "Oh wait, it's apartment 4B.")
    assert "readback_changed" in turned["data"]["hints"]
    refused = book(agent, call, first_open(agent, call))
    assert refused["refused"] == "gate_read_back"
    # The new read-back with the unit books, and the job carries the unit.
    address = {**in_area_address(), "unit": "4B"}
    asked = agent.tool("read_back", call, address=address, phone="caller_id")
    assert asked["ok"] and "unit 4B" in asked["say"]
    agent.say(call, asked["say"])
    agent.turn(call, "Yes.")
    assert agent.tool("read_back", call, address=address, phone="caller_id", confirmed=True)["ok"]
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    request = json.loads(_one(agent, "SELECT request_json FROM bookings WHERE kind = 'book'"))
    assert request["address"]["unit"] == "4B"


def test_a_correction_in_the_same_breath_as_the_yes_cannot_be_confirmed(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 101 Union St, 11231.")
    address = in_area_address()
    asked = agent.tool("read_back", call, address=address, phone="caller_id")
    agent.say(call, asked["say"])
    agent.turn(call, "Yes, but it's 4B.")
    refused = agent.tool("read_back", call, address=address, phone="caller_id", confirmed=True)
    assert refused["refused"] == "gate_read_back"
    assert refused["data"]["changed"] == "unit"
    assert book(agent, call, first_open(agent, call))["refused"] == "gate_read_back"


@pytest.mark.parametrize("line", ["Yes, that's right.", "Yep, 101 Union.", "Yes, thanks."])
def test_a_plain_yes_after_the_confirmation_keeps_it(agent, line):
    call = ready_to_book(agent)
    turned = agent.turn(call, line)
    assert "readback_changed" not in turned["data"]["hints"]
    assert book(agent, call, first_open(agent, call))["ok"]


def test_a_plain_yes_still_confirms_the_read_back(agent):
    """What the change check might break: the two-step read-back with an ordinary yes."""
    call = ready_to_book(agent)
    assert _one(agent, "SELECT readback_confirmed_at FROM calls WHERE id = ?", call)


def _one(agent, sql, *params):
    conn = agent.db()
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()




# ---- a visit whose window has ended -------------------------------------------------------

# Maria Delgado's open no-heat visit is today, 12 to 4 (the mock's seed). The clock starts at 9 AM.
BEFORE, DURING, AFTER = 0, 4, 8  # hours past 9 AM: 9 AM, 1 PM, 5 PM


def _delgado_visit(agent, clock, scenarios, hours):
    clock.advance(hours=hours)
    started = agent.start(scenarios["repeatCallerOpenJob"]["phone"])
    return started, started["data"]["customers"][0]["open_jobs"][0]


@pytest.mark.parametrize("hours, passed", [(BEFORE, False), (DURING, False), (AFTER, True)])
def test_the_lookup_before_hello_marks_a_visit_whose_window_ended(agent, clock, scenarios, hours,
                                                                  passed):
    _, visit = _delgado_visit(agent, clock, scenarios, hours)
    assert visit["window"]["label"] == "12 to 4"
    assert visit["window"]["passed"] is passed


@pytest.mark.parametrize("hours, passed", [(BEFORE, False), (DURING, False), (AFTER, True)])
def test_the_call_note_never_calls_an_ended_visit_upcoming(agent, clock, scenarios, hours, passed):
    started, _ = _delgado_visit(agent, clock, scenarios, hours)
    notes = " ".join(call_notes(started["data"]))
    assert ("already have a visit booked" in notes) is not passed
    assert ("has already ended" in notes) is passed


def test_find_customer_marks_an_ended_window_too(agent, clock, scenarios):
    clock.advance(hours=AFTER)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    found = agent.tool("find_customer", call, phone=scenarios["repeatCallerOpenJob"]["phone"])
    assert found["data"]["matches"][0]["open_jobs"][0]["window"]["passed"] is True


@pytest.mark.parametrize("hours, code", [(DURING, "open_job"), (AFTER, "open_job_passed")])
def test_booking_over_an_open_visit_asks_about_one_whose_window_ended(agent, clock, scenarios,
                                                                      hours, code):
    started, _ = _delgado_visit(agent, clock, scenarios, hours)
    call = started["data"]["call_id"]
    location = started["data"]["customers"][0]["locations"][0]
    address = {k: location[k] for k in ("street", "unit", "city", "zip")}
    agent.turn(call, "The heat is still out.")
    agent.confirm_read_back(call, address)
    refused = book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat")
    assert refused["refused"] == code
    if code == "open_job_passed":
        assert refused["say"].startswith("I see a visit there that was due today between 12 and 4")
        assert "all set" not in refused["say"]


# ---- commercial phrasings ------------------------------------------------------------------

COMMERCIAL = load_agent(TENANT_DIR)[0]["commercial"]


@pytest.mark.parametrize(
    "line",
    [
        "My office is by the church.",
        "The bakery across from me is mine.",
        "I own the place, it's a deli near the park.",
        "The AC in our office quit.",
    ],
)
def test_an_office_or_a_business_the_caller_owns_is_commercial(line):
    assert sounds_commercial(line, COMMERCIAL), line


@pytest.mark.parametrize(
    "line",
    [
        "It's the house next to the restaurant.",
        "I'm calling from my office, the heat at home is out.",
        "I'm at my office right now.",
        "I own the place, it's a two-family next to a deli.",
        "My house is next to the bakery, and it's mine.",
        "I own it, it's a brownstone.",
        "My doctor's office called.",
        "I work from my home office and it's freezing.",
    ],
)
def test_a_home_placed_by_a_business_or_a_caller_at_work_stays_residential(line):
    assert not sounds_commercial(line, COMMERCIAL), line


# ---- work the company doesn't do -----------------------------------------------------------

SERVICES = load_agent(TENANT_DIR)[0]["services"]


@pytest.mark.parametrize(
    "line, kinds",
    [
        ("My kitchen sink is leaking.", ["plumbing"]),
        ("Can you fix a fridge that stopped cooling?", ["appliances"]),
        ("Our dishwasher won't drain.", ["appliances"]),
        ("It's an electric water heater, no hot water.", ["electric_water_heater"]),
        ("Do you guys do duct cleaning?", ["duct_cleaning"]),
        ("My heat is out and my sink leaks.", ["plumbing"]),
    ],
)
def test_work_the_company_doesnt_do_is_recognized(line, kinds):
    assert asks.not_offered(line, SERVICES) == kinds


@pytest.mark.parametrize(
    "line",
    [
        "My heat is out and the radiators are cold.",
        "I'm worried the pipes will freeze.",
        "We've been using the oven to keep warm.",
        "The boiler is leaking water on the floor.",
        "No hot water and no heat, it's a gas boiler.",
        "It's freezing in here.",
        "No, it's not the sink, it's the heat.",
    ],
)
def test_heating_and_cooling_lines_are_never_declined(line):
    assert asks.not_offered(line, SERVICES) == []


def test_a_request_for_plumbing_gets_the_server_line_once_and_a_lead(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    hints = agent.turn(call, "My heat is out and my sink leaks.")["data"]["hints"]
    line = "I'm sorry, we don't do plumbing, so a plumber would be the right call for that."
    assert f"not_offered:{line}" in hints
    notes = " ".join(server_notes(hints))
    assert line in notes and "carry on" in notes
    # Said once per kind per call.
    assert not any(h.startswith("not_offered:") for h in agent.turn(call, "The sink is bad.")["data"]["hints"])
    lead = _row(agent, "SELECT kind, request_json FROM leads WHERE call_id = ?", call)
    assert lead["kind"] == "not_offered"
    assert json.loads(lead["request_json"])["callReasonId"] == 706
    # The heat still books.
    agent.confirm_read_back(call, in_area_address())
    assert book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat")["ok"]


def test_the_queued_lead_reaches_servicetitan_on_the_next_jobs_run(agent, mock):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Do you do duct cleaning?")
    services = agent.app.state.services
    conn = services.connect()
    try:
        tenant = next(iter(services.tenants))
        slug = tenant.slug
        retry_queue(conn, tenant, services.fsm[slug], services.caches[slug], services.clock())
        status = conn.execute("SELECT status FROM leads WHERE call_id = ?", (call,)).fetchone()[0]
    finally:
        conn.close()
    assert status == "stored"


def test_no_decline_on_a_safety_call_or_a_commercial_one(agent):
    gas = agent.start(UNKNOWN)["data"]["call_id"]
    hints = agent.turn(gas, "I smell gas and my sink is leaking.")["data"]["hints"]
    assert not any(h.startswith("not_offered:") for h in hints)
    shop = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(shop, "I run a restaurant.")
    assert not any(h.startswith("not_offered:") for h in agent.turn(shop, "The sink leaks.")["data"]["hints"])


def _row(agent, sql, *params):
    conn = agent.db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


# ---- a technician asked for by name --------------------------------------------------------


@pytest.mark.parametrize(
    "line, asked",
    [
        ("Can I get Mike again?", "Mike"),
        ("Could you send Rosa back out, she knows the system?", "Rosa"),
        ("Is Mike available Thursday?", "Mike"),
        ("I'd like the same technician as last time.", "the same technician as last time"),
        ("My heat is out.", None),
        ("Can I get someone out today?", None),
        ("Can you send someone again?", None),
        ("It's Mike, I called earlier.", None),
    ],
)
def test_a_technician_asked_for_is_read_from_the_callers_words(line, asked):
    assert asks.technician_asked(line) == asked


def test_a_request_before_booking_goes_into_the_job_and_is_never_promised(agent, mock):
    call = ready_to_book(agent)
    hints = agent.turn(call, "Can I get Mike again?")["data"]["hints"]
    assert "technician_requested" in hints
    assert "never promise" in " ".join(server_notes(hints))
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    summary = _mock_job(mock, booked["data"]["booking_id"])["summary"]
    assert "Caller asked for Mike. Not promised; dispatcher to decide." in summary
    # The server never picks the technician by name: nothing in the request names one.
    request = json.loads(_one(agent, "SELECT request_json FROM bookings WHERE kind = 'book'"))
    assert "Mike" not in json.dumps({k: v for k, v in request.items() if k != "notes"})


def test_a_request_after_booking_is_added_to_the_booked_job(agent):
    call = ready_to_book(agent)
    booked = book(agent, call, first_open(agent, call))
    agent.turn(call, "Oh, and can I get Mike again?")
    adapter = agent.app.state.services.fsm["summit-air"]
    notes = [n["text"] for n in adapter.job_notes(booked["data"]["booking_id"])]
    assert "Caller asked for Mike. Not promised; dispatcher to decide." in notes
    # Asked twice, noted once.
    agent.turn(call, "Can I get Mike again?")
    assert len(adapter.job_notes(booked["data"]["booking_id"])) == len(notes)


def _mock_job(mock, job_id):
    import sqlite3

    conn = sqlite3.connect(mock[0].state.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
    finally:
        conn.close()



# ---- two jobs on one call -------------------------------------------------------------------


def test_two_different_jobs_at_one_address_book_with_their_own_priority(agent):
    call = ready_to_book(agent)
    agent.turn(call, "My heat's out, it's 55 in here, and can you also tune up the AC?")
    heat = book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat",
                inside_temp_f=55)
    assert heat["ok"], heat
    tune = book(agent, call, first_open(agent, call), separate_problem=True)
    assert tune["ok"], tune
    assert heat["data"]["booking_id"] != tune["data"]["booking_id"]
    levels = dict(_rows(agent, "SELECT job_type, priority FROM bookings WHERE kind = 'book'"))
    assert levels["tune_up"] == "Normal"
    assert levels["no_heat"] in ("High", "Urgent")


def test_a_second_job_at_the_same_address_needs_separate_problem(agent):
    call = ready_to_book(agent)
    assert book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat")["ok"]
    assert book(agent, call, first_open(agent, call))["refused"] == "second_job"


def test_a_second_unit_needs_its_own_read_back_and_books_there(agent):
    """A super with units 2A and 5C: naming the second unit clears the confirmed read-back, so the
    second job can't land on the first unit."""
    first = {**in_area_address(), "unit": "2A"}
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I'm the super, no heat in 2A.")
    agent.confirm_read_back(call, first)
    assert book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat")["ok"]
    hints = agent.turn(call, "And unit 5C has no heat either.")["data"]["hints"]
    assert "readback_changed" in hints
    slot = first_open(agent, call, "no_heat")
    assert book(agent, call, slot, job_type="no_heat")["refused"] == "gate_read_back"
    second = {**first, "unit": "5C"}
    asked = agent.tool("read_back", call, address=second, phone="caller_id")
    agent.say(call, asked["say"])
    agent.turn(call, "Yes.")
    assert agent.tool("read_back", call, address=second, phone="caller_id", confirmed=True)["ok"]
    booked = book(agent, call, slot, job_type="no_heat")
    assert booked["ok"], booked
    units = [json.loads(r[0])["address"]["unit"] for r in
             _rows(agent, "SELECT request_json FROM bookings WHERE kind = 'book' ORDER BY id")]
    assert units == ["2A", "5C"]


def test_no_opening_for_the_second_job_asks_for_a_named_callback(agent, monkeypatch):
    call = ready_to_book(agent)
    assert book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat")["ok"]
    monkeypatch.setattr("app.tools._open_windows", lambda ctx, job_type, level: [])
    found = agent.tool("find_slots", call, job_type="tune_up")
    assert "flag_person" in found["data"]["need"]


def test_no_opening_on_a_first_job_has_no_second_job_note(agent, monkeypatch):
    call = ready_to_book(agent)
    monkeypatch.setattr("app.tools._open_windows", lambda ctx, job_type, level: [])
    assert "need" not in agent.tool("find_slots", call, job_type="tune_up")["data"]


def _rows(agent, sql, *params):
    conn = agent.db()
    try:
        return [tuple(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


@pytest.mark.parametrize(
    "sentence, dropped",
    [
        ("Mike will be the one coming out.", True),
        ("I'll make sure it's Mike.", True),
        ("I've assigned Mike to your visit.", True),
        ("I'll pass that along to the team.", False),
        ("I can't promise who comes, but I'll pass it on.", False),
        ("Someone will be there between 8 and 12.", False),
        ("I've booked you for tomorrow.", False),
    ],
)
def test_a_promise_of_a_named_technician_is_never_said(sentence, dropped):
    assert (SpokenReply().sentence(sentence) == "") is dropped


@pytest.mark.parametrize(
    "line, asked",
    [
        ("can i get mike again he was great last time", "Mike"),
        ("oh could you send rosa back out", "Rosa"),
        ("can you get someone out today", None),
        ("can you get it working again", None),
        ("is anyone available tomorrow", None),
        ("can you send a tech out", None),
    ],
)
def test_a_name_written_in_lower_case_still_counts(line, asked):
    """Eval run 20261104-200546: the transcript came through in lower case."""
    assert asks.technician_asked(line) == asked


def test_two_units_in_one_read_back_are_refused(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 101 Union St, 11231, units 2A and 5C.")
    refused = agent.tool("read_back", call, address={**in_area_address(), "unit": "2A and 5C"},
                         phone="caller_id")
    assert refused["refused"] == "invalid_arguments"
    assert agent.tool("read_back", call, address={**in_area_address(), "unit": "2A"},
                      phone="caller_id")["ok"]


def test_the_ended_visit_note_names_the_job_to_move(agent, clock, scenarios):
    started, visit = _delgado_visit(agent, clock, scenarios, AFTER)
    notes = " ".join(call_notes(started["data"]))
    assert f"job {visit['job_id']}" in notes and "never book_appointment" in notes


def test_a_yes_naming_both_units_is_no_change_to_the_one_read_back():
    second = {**READ_BACK, "unit": "5C"}
    assert readback.change("Yes, 2A and 5C, that's right.", second) is None
    assert readback.change("Yes, 2A.", second) == "unit"


def test_two_units_named_together_get_a_note_and_the_first_booking_moves_on_to_the_second(agent):
    first = {**in_area_address(), "unit": "2A"}
    call = agent.start(UNKNOWN)["data"]["call_id"]
    hints = agent.turn(call, "I'm the super, no heat in 2A and 5C.")["data"]["hints"]
    assert "units:2A,5C" in hints
    assert "Each unit is its own job" in " ".join(server_notes(hints))
    agent.confirm_read_back(call, first)
    booked = book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat")
    assert booked["say"].endswith("Now let's set up apartment 5C.")
    second = {**first, "unit": "5C"}
    agent.turn(call, "Okay, 5C now.")
    asked = agent.tool("read_back", call, address=second, phone="caller_id")
    agent.say(call, asked["say"])
    agent.turn(call, "Yes.")
    assert agent.tool("read_back", call, address=second, phone="caller_id", confirmed=True)["ok"]
    done = book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat")
    assert done["say"].endswith("Anything else?")


def test_one_unit_booking_still_ends_with_anything_else(agent):
    call = ready_to_book(agent)
    assert book(agent, call, first_open(agent, call))["say"].endswith("Anything else?")
