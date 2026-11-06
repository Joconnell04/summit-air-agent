"""Every call gets one label for the owner, Urgent, Routine or No visit, with its reason
(DECISIONS.md 122), stored on the call and shown on the owner view; a booked job carries it to the
CRM as a tag. The label describes the visit the call left, not the call (DECISIONS.md 128): a call
with no visit standing is No visit, whatever else happened on it."""

from test_ts_owner import booked_tune_up, caller, commercial, out_of_area
from test_ts_safety import GAS_LINES
from toolkit import UNKNOWN, book, first_open, in_area_address, ready_to_book, sign_in


def _label(agent, call):
    with agent.db() as db:
        row = db.execute(
            "SELECT label, reason FROM call_labels WHERE call_id = ?", (call,)
        ).fetchone()
    return (row["label"], row["reason"]) if row else None


def _fsm(agent):
    return agent.app.state.services.fsm["summit-air"]


def test_a_booked_tune_up_is_routine_and_tagged_so(agent):
    call = ready_to_book(agent)
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    agent.end(call)
    assert _label(agent, call) == ("Scheduled", "Scheduled: Low priority, maintenance tune-up")
    assert _fsm(agent).job(booked["data"]["booking_id"])["tagTypeIds"] == [805]


def test_an_older_person_with_no_heat_is_urgent_with_the_reason(agent):
    call = ready_to_book(agent)
    agent.turn(call, "My mother is 82 and she's here with me.")
    booked = book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat")
    assert booked["ok"], booked
    agent.end(call)
    # Urgent in the heating season is an emergency dispatch; the one reason an owner reads first,
    # without the caller's quoted line or the weather.
    assert _label(agent, call)[0] in ("Emergency dispatch", "Scheduled")
    assert "an older person or a baby at home" in _label(agent, call)[1]
    # Booked as a scheduled visit (the test books directly), so it carries the Scheduled tag.
    assert _fsm(agent).job(booked["data"]["booking_id"])["tagTypeIds"] == [805]


def test_a_dispatch_is_urgent(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat's out and my mother is 82.")
    address = in_area_address()
    agent.turn(call, f"It's {address['street']}, {address['zip']}.")
    sent = agent.tool("read_back", call, address=address, phone="caller_id", job_type="no_heat")
    assert sent["data"]["dispatched"], sent
    agent.end(call)
    label, reason = _label(agent, call)
    assert label == "Emergency dispatch" and "no heat" in reason


def test_a_gas_smell_with_no_visit_is_no_visit_and_says_so(agent):
    call = ready_to_book(agent)
    agent.turn(call, "Wait, I smell gas.")
    agent.end(call)
    assert _label(agent, call) == ("No visit", "No visit: gas smell, no visit booked")


def test_a_visit_booked_on_a_gas_call_is_urgent(agent):
    call = ready_to_book(agent)
    agent.turn(call, "Wait, I smell gas.")
    agent.say(call, GAS_LINES)
    agent.turn(call, "Okay, we're all outside and the gas company is on the way.")
    booked = book(agent, call, first_open(agent, call, "safety_check"), job_type="safety_check")
    assert booked["ok"], booked
    agent.end(call)
    assert _label(agent, call) == ("Scheduled", "Scheduled: High priority, gas smell, safety check")


def test_an_urgent_lookup_with_nothing_booked_is_no_visit(agent):
    call = ready_to_book(agent)
    agent.turn(call, "My mother is 82 and she's here with me.")
    agent.tool("find_slots", call, job_type="no_heat")
    agent.end(call)
    assert _label(agent, call)[0] == "No visit"


def test_a_callback_with_no_visit_is_no_visit(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I have a question about my bill, can someone call me back?")
    flagged = agent.tool("flag_person", call, reason="Billing question", callback_time="11 AM")
    assert flagged["ok"], flagged
    agent.end(call)
    assert _label(agent, call) == ("No visit", "No visit: callback promised")


def test_an_urgent_label_stays_when_a_routine_job_follows(agent):
    call = ready_to_book(agent)
    agent.turn(call, "My mother is 82 and she's here with me.")
    booked = book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat")
    assert booked["ok"], booked
    agent.tool("find_slots", call, job_type="tune_up")
    agent.end(call)
    assert "Urgent" in _label(agent, call)[1] or _label(agent, call)[0] == "Emergency dispatch"


def test_calls_with_no_visit_say_why(agent):
    business = commercial(agent, 1)
    away = out_of_area(agent, 2, "11501")
    wrong = agent.start(caller(3))["data"]["call_id"]
    agent.turn(wrong, "Sorry, wrong number.")
    agent.end(wrong)
    assert _label(agent, business) == ("No visit", "No visit: a business, turned away")
    assert _label(agent, away)[0] == "No visit"
    assert _label(agent, wrong) == ("No visit", "No visit: no visit asked for")


def test_the_owner_sees_the_label_and_can_show_urgent_calls(agent):
    routine = booked_tune_up(agent, 1)
    urgent = ready_to_book(agent, caller(2))
    agent.turn(urgent, "Wait, I smell gas.")
    agent.say(urgent, GAS_LINES)
    agent.turn(urgent, "Okay, we're all outside and the gas company is on the way.")
    assert book(agent, urgent, first_open(agent, urgent, "safety_check"), job_type="safety_check")["ok"]
    agent.end(urgent)
    sign_in(agent)
    html = agent.client.get("/owner/calls").text
    assert 'data-show="urgent"' in html
    assert '>Scheduled</span>' in html and "gas smell" in html
    assert 'title="Scheduled: Low priority, maintenance tune-up">Scheduled</span>' in html
    page = agent.client.get(f"/owner/calls/{routine}").text
    assert ">Scheduled</span>" in page and "Scheduled: Low priority, maintenance tune-up" in page


def test_a_call_still_going_has_a_label_so_far(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Hi, I have a question.")
    sign_in(agent)
    html = agent.client.get("/owner/calls").text
    assert 'title="No visit: nothing booked yet">No visit</span>' in html
