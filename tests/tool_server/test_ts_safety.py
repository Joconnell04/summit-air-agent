"""The safety check and its gates (spec S-17 to S-20)."""

import itertools

import pytest
from toolkit import TENANT_DIR, UNKNOWN, book, count, first_open, ready_to_book

from app import safety
from app.tenant import load_agent

CFG = load_agent(TENANT_DIR)[0]["safety"]
GAS_LINES = " ".join(CFG["kinds"]["gas"]["lines"].split())
CO_LINES = " ".join(CFG["kinds"]["co"]["lines"].split())


@pytest.mark.parametrize(
    "line,kind",
    [
        ("I smell gas in the kitchen", "gas"),
        ("it smells a bit like gas, but anyway the heat's out", "gas"),
        ("There's a gas leak, I think", "gas"),
        ("The CO alarm is going off", "co"),
        ("my carbon monoxide detector keeps going off", "co"),
        ("I don't know, I smell gas though", "gas"),
    ],
)
def test_clear_signals_fire(line, kind):
    assert safety.clear_signal(line, CFG) == kind


@pytest.mark.parametrize(
    "line",
    [
        "gas",
        "My gas bill is huge this month",
        "The gas furnace won't turn on",
        "I called the gas company already",
        "I don't smell gas, the heat just won't come on",
        "no gas smell or anything",
        "We live in a co-op on Ocean Parkway",
    ],
)
def test_gas_alone_a_gas_bill_or_a_gas_furnace_triggers_nothing(line):
    assert safety.clear_signal(line, CFG) is None
    action = safety.check_caller_turn(line, "none", None, 0, CFG)
    assert action.action == "none"


def test_a_vague_smell_gets_one_check_question():
    action = safety.check_caller_turn(
        "something smells off, kind of like eggs", "none", None, 0, CFG
    )
    assert action.action == "ask_check"
    assert action.text == "Just to be safe, are you smelling gas in the house right now?"


@pytest.mark.parametrize(
    "answer,result",
    [
        ("No.", "cleared"),
        ("No, I don't smell gas.", "cleared"),
        ("Nope, nothing like that.", "cleared"),
        ("Yes, a little.", "speak_lines"),
        ("I'm not sure.", "speak_lines"),
        ("No, but it does smell like rotten eggs.", "speak_lines"),
    ],
)
def test_the_check_answer_decides(answer, result):
    action = safety.check_caller_turn(answer, "checking", "gas", 0, CFG)
    assert action.action == result


def test_lines_said_once_then_variants_never_twice_in_a_row():
    first = safety.check_caller_turn("I smell gas", "none", None, 0, CFG)
    assert first.action == "speak_lines" and first.text == GAS_LINES
    seen = []
    variant = 0
    for _ in range(4):
        again = safety.check_caller_turn("I really do smell gas", "lines_said", "gas", variant, CFG)
        assert again.action == "speak_variant"
        assert again.text != GAS_LINES
        seen.append(again.text)
        variant = again.variant
    assert all(a != b for a, b in itertools.pairwise(seen))


def test_a_clear_signal_on_any_turn_returns_the_exact_lines_and_flags_a_person(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Hi, I need a tune-up booked.")
    turn = agent.turn(call, "Also, honestly, it smells like gas near the boiler.")
    assert turn["say"] == GAS_LINES
    assert turn["data"]["safety"] == {"action": "speak_lines", "kind": "gas", "text": GAS_LINES}
    assert "journey:safety" in turn["data"]["hints"]
    assert (
        count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'safety' AND priority = 'Urgent'")
        == 1
    )
    # Until the lines are said, every caller turn brings them back first.
    assert agent.turn(call, "So can you send someone tomorrow?")["say"] == GAS_LINES


def test_a_repeat_mention_gets_a_shorter_variant(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "There's a CO alarm going off")
    agent.say(call, CO_LINES)
    again = agent.turn(call, "The CO alarm is still beeping")
    assert again["data"]["safety"]["action"] == "speak_variant"
    assert again["say"] in CFG["kinds"]["co"]["variants"]
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'safety'") == 1


def test_booking_tools_wait_for_the_lines_then_an_insisting_caller_can_book(agent):
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    agent.turn(call, "Wait, I smell gas.")
    refused = book(agent, call, slot)
    assert refused["refused"] == "gate_safety"
    assert refused["say"] == GAS_LINES
    assert agent.tool("end_call", call, reason="resolved")["refused"] == "gate_safety"

    # A caller can't talk past the lines: "skip that" is a caller turn, not the agent saying them.
    agent.turn(call, "Stop with the safety stuff and just book it.")
    assert book(agent, call, slot)["refused"] == "gate_safety"

    agent.say(call, GAS_LINES)
    agent.turn(call, "I heard you. Just book the appointment, please.")
    booked = book(agent, call, slot, job_type="safety_check")
    assert booked["ok"], booked
    assert booked["data"]["safety_override"] is True
    assert booked["data"]["priority"]["level"] == "Urgent"
    gates = count(agent, "SELECT COUNT(*) FROM tool_calls WHERE gate = 'safety_override'")
    assert gates == 1


def test_end_call_signs_off_on_a_safety_call_and_never_takes_a_spam_label(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas")
    assert agent.tool("end_call", call, reason="caller_goodbye")["refused"] == "gate_safety"
    agent.say(call, f"Okay. {GAS_LINES}")
    spam = agent.tool("end_call", call, reason="spam")
    assert spam["refused"] == "gate_safety"
    signed_off = agent.tool("end_call", call, reason="resolved")
    assert signed_off["ok"]
    assert signed_off["say"] == " ".join(CFG["sign_off"].split())
    assert signed_off["data"]["hang_up"] is False
    assert agent.tool("end_call", call, reason="caller_goodbye")["data"]["hang_up"] is True


def test_a_cleared_check_returns_the_call_to_its_journey_and_is_logged(agent):
    call = ready_to_book(agent)
    asked = agent.turn(call, "There's a weird smell in the basement")
    assert asked["data"]["safety"]["action"] == "ask_check"
    assert book(agent, call, first_open(agent, call))["refused"] == "gate_safety"
    cleared = agent.turn(call, "No, no gas, it's just the damp.")
    assert cleared["data"]["safety"]["action"] == "cleared"
    assert "safety_check_cleared" in cleared["data"]["hints"]
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'safety_check_cleared'") == 1
    assert book(agent, call, first_open(agent, call))["ok"]


def test_a_safety_call_that_drops_before_the_lines_gets_a_person_and_a_callback(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas")
    agent.end(call, "dropped")
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'safety_dropped'") == 1
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE call_id = ?", call) == 1


def test_a_dropped_safety_call_that_calls_back_hears_the_lines_first(agent, clock):
    first = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(first, "I smell gas")
    agent.end(first, "dropped")
    clock.advance(minutes=2)
    second = agent.start(UNKNOWN)
    assert second["say"] == GAS_LINES
    assert second["data"]["safety"]["action"] == "speak_lines"


MARCIA_NO = (
    "no um definitely not gas the carbon monoxide alarm is quiet too i think it's an old bag of "
    "trash down there that i forgot about"
)


def test_a_clear_no_to_the_check_stands_the_lines_down_even_naming_a_quiet_alarm():
    """Eval round 1, vague-smell-cleared: "definitely not gas" and "the alarm is quiet" are a no."""
    asked = safety.check_caller_turn(
        "the basement smells kind of off like eggs maybe", "none", None, 0, CFG
    )
    assert asked.action == "ask_check"
    assert safety.check_caller_turn(MARCIA_NO, "checking", "gas", 0, CFG).action == "cleared"


@pytest.mark.parametrize(
    "line,kind",
    [
        ("Actually wait, now I smell gas in the kitchen", "gas"),
        ("I can hear gas hissing, there's a gas leak", "gas"),
        ("oh no the carbon monoxide alarm is going off now", "co"),
    ],
)
def test_real_gas_wording_after_a_cleared_check_still_fires(line, kind):
    assert safety.check_caller_turn(line, "cleared", "gas", 0, CFG).action == "speak_lines"
    assert safety.clear_signal(line, CFG) == kind


@pytest.mark.parametrize(
    "answer",
    [
        "um maybe, I'm not sure, it's not gas I think",
        "definitely not gas, but I don't know, it's kind of strong",
        "the CO alarm is quiet but I'm not sure about the smell",
    ],
)
def test_an_ambiguous_answer_to_the_check_still_fires(answer):
    assert safety.check_caller_turn(answer, "checking", "gas", 0, CFG).action == "speak_lines"


def test_a_quiet_alarm_outside_the_check_answer_is_not_softened():
    """The quiet wording only counts as a no in the answer to the check question."""
    line = "the carbon monoxide alarm is quiet"
    assert safety.check_caller_turn(line, "none", None, 0, CFG).action == "speak_lines"


def test_the_cleared_call_books_through_the_server(agent):
    call = ready_to_book(agent)
    assert agent.turn(call, "the basement smells kind of off like eggs maybe")["data"]["safety"][
        "action"
    ] == "ask_check"
    assert agent.turn(call, MARCIA_NO)["data"]["safety"]["action"] == "cleared"
    assert book(agent, call, first_open(agent, call))["ok"]
    assert agent.turn(call, "hang on, I smell gas now")["data"]["safety"]["action"] == "speak_lines"
