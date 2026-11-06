"""The safety lines' dispatch offer (DECISIONS.md 129): the gas and carbon monoxide lines end by
offering a technician right away; a yes is dispatched at the read-back as an Urgent safety check,
a no carries on, and neither the lines nor the offer come back."""

from test_ts_safety import CFG, CO_LINES, GAS_LINES
from test_ts_transfer import EVENING
from toolkit import UNKNOWN, in_area_address

NOTE = " ".join(CFG["booking_note"].split())


def gas_call(agent) -> str:
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I think I smell gas in the basement.")
    agent.say(call, GAS_LINES)
    return call


def read_back(agent, call, job_type="safety_check"):
    address = in_area_address()
    agent.turn(call, f"It's {address['street']}, {address['zip']}.")
    return agent.tool("read_back", call, address=address, phone="caller_id", job_type=job_type)


def label(agent, call):
    with agent.db() as db:
        return db.execute("SELECT label FROM call_labels WHERE call_id = ?", (call,)).fetchone()[0]


def test_both_hazard_lines_end_by_offering_a_technician_right_away():
    for lines in (GAS_LINES, CO_LINES):
        assert lines.endswith("would you like us to send a technician out right away?")


def test_a_yes_is_dispatched_at_the_read_back_as_urgent(agent):
    call = gas_call(agent)
    agent.turn(call, "Yes, we're outside now, please send someone.")
    sent = read_back(agent, call)
    assert sent["ok"] and sent["data"]["dispatched"], sent
    # The visit waits until it's checked and safe, said before the sentence's closing question.
    say = sent["say"]
    assert NOTE in say and say.index(NOTE) < say.index("We'll get a technician out")
    assert say.endswith("Is that the right address?")
    agent.end(call)
    assert label(agent, call) == "Urgent"


def test_after_hours_a_yes_goes_to_the_on_call_technician(agent, clock):
    clock.now = EVENING
    call = gas_call(agent)
    agent.turn(call, "Yes please.")
    sent = read_back(agent, call)
    assert sent["data"]["dispatched"] and "on-call technician will call you" in sent["say"], sent


def test_a_no_carries_on_without_the_lines_or_the_offer_again(agent):
    call = gas_call(agent)
    no = agent.turn(call, "No thanks, the gas company is handling it. I still smell a little gas.")
    assert no["data"]["safety"] is None and no["say"] == ""
    later = agent.turn(call, "Actually, can you tell me your hours?")
    assert later["data"]["safety"] is None and later["say"] == ""


def test_no_dispatch_before_the_lines_are_said(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas.")
    early = read_back(agent, call)
    assert not (early.get("data") or {}).get("dispatched"), early
    with agent.db() as db:
        assert db.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0] == 0


def test_other_job_types_on_a_safety_call_are_never_dispatched(agent):
    call = gas_call(agent)
    agent.turn(call, "Also the heat's out.")
    sent = read_back(agent, call, job_type="no_heat")
    assert not sent["data"].get("dispatched"), sent
