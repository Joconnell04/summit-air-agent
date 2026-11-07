"""The safety menu (DECISIONS.md 131): the hazard lines end with three choices. Someone sent right
away is dispatched at the read-back as an Urgent safety check; an appointment is booked at High;
a person now is the handoff; a no carries on, and neither the lines nor the menu come back."""

from test_ts_safety import CFG, CO_LINES, GAS_LINES
from test_ts_transfer import EVENING
from toolkit import UNKNOWN, book, first_open, in_area_address

NOTE = " ".join(CFG["booking_note"].split())
MENU = ("Once everyone's safe, I can connect you with someone on our team right now, or book an "
        "appointment with one of our technicians to check it out. And if it's really urgent, we "
        "can send someone out right away to help.")


def gas_call(agent) -> str:
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I think I smell gas in the basement.")
    agent.say(call, GAS_LINES)
    return call


def read_back(agent, call, job_type="safety_check"):
    address = in_area_address()
    agent.turn(call, f"It's {address['street']}, {address['zip']}, by the furnace.")
    return agent.tool("read_back", call, address=address, phone="caller_id", job_type=job_type)


def label(agent, call):
    with agent.db() as db:
        return db.execute("SELECT label FROM call_labels WHERE call_id = ?", (call,)).fetchone()[0]


def test_every_hazard_line_ends_with_the_menu():
    for lines in (GAS_LINES, CO_LINES, " ".join(CFG["kinds"]["hazard"]["lines"].split())):
        assert lines.endswith(MENU)


def test_someone_right_away_is_an_urgent_emergency_dispatch(agent):
    call = gas_call(agent)
    agent.turn(call, "We're outside. Please send someone out right away.")
    sent = read_back(agent, call)
    assert sent["ok"] and sent["data"]["dispatched"], sent
    say = sent["say"]
    assert NOTE in say and say.index(NOTE) < say.index("We'll get a technician out")
    assert say.endswith("Is that the right address?")
    agent.end(call)
    assert label(agent, call) == "Emergency dispatch"


def test_after_hours_someone_right_away_goes_to_the_on_call_technician(agent, clock):
    clock.now = EVENING
    call = gas_call(agent)
    agent.turn(call, "Send someone right away please.")
    sent = read_back(agent, call)
    assert sent["data"]["dispatched"] and "on-call technician will call you" in sent["say"], sent


def test_an_appointment_is_booked_at_high_not_dispatched(agent):
    call = gas_call(agent)
    agent.turn(call, "Let's book an appointment to check it out.")
    sent = read_back(agent, call)
    assert not sent["data"].get("dispatched"), sent
    agent.say(call, sent["say"])
    agent.turn(call, "Yes, that's right.")
    address = in_area_address()
    agent.tool("read_back", call, address=address, phone="caller_id", job_type="safety_check",
               confirmed=True)
    booked = book(agent, call, first_open(agent, call, "safety_check"), job_type="safety_check")
    assert booked["ok"] and booked["data"]["priority"]["level"] == "High", booked


def test_a_no_carries_on_without_the_lines_or_the_menu_again(agent):
    call = gas_call(agent)
    no = agent.turn(call, "No thanks, the gas company is handling it. I still smell a little gas.")
    assert no["data"]["safety"] is None and no["say"] == ""
    later = agent.turn(call, "Actually, can you tell me your hours?")
    assert later["data"]["safety"] is None and later["say"] == ""


def test_no_dispatch_before_the_lines_are_said(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas, send someone right away.")
    early = read_back(agent, call)
    assert not (early.get("data") or {}).get("dispatched"), early
    with agent.db() as db:
        assert db.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0] == 0


def test_other_job_types_on_a_safety_call_are_never_dispatched_as_safety(agent):
    call = gas_call(agent)
    agent.turn(call, "Also the heat's out.")
    sent = read_back(agent, call, job_type="tune_up")
    assert not sent["data"].get("dispatched"), sent


def test_after_a_dispatch_getting_in_isnt_asked_when_the_caller_already_said(agent):
    """Phone check t2 gas-now, 6 November: "Someone will be home, and there's no gate code" came
    with the address, and after the dispatch Brian asked "Will someone be home?" again."""
    call = gas_call(agent)
    agent.turn(call, "We're outside. Please send someone out right away.")
    agent.turn(call, "Someone will be home, and there's no gate code.")
    sent = read_back(agent, call)
    assert sent["data"]["dispatched"], sent
    assert "don't ask" in sent["data"]["next"] and "Will someone be home" not in sent["data"]["next"]


def test_after_a_dispatch_getting_in_is_asked_when_it_wasnt_said(agent):
    call = gas_call(agent)
    agent.turn(call, "We're outside. Please send someone out right away.")
    sent = read_back(agent, call)
    assert sent["data"]["dispatched"], sent
    assert agent.start(UNKNOWN)["data"]["tenant"]["arrival_question"] in sent["data"]["next"]
    assert "also when the caller asks what the technician needs" in sent["data"]["next"]
