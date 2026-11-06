"""The safety check and its gates (spec S-17 to S-20, and S-21b for indoor fuel heating)."""

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
    # A tenant that allows repeats with shorter lines; Summit Air's repeat_limit is 0.
    variants = ["Please get outside and call 9-1-1 from there.", "The safest thing is outside."]
    kinds = {**CFG["kinds"], "gas": {**CFG["kinds"]["gas"], "variants": variants}}
    unlimited = {**{k: v for k, v in CFG.items() if k != "repeat_limit"}, "kinds": kinds}
    first = safety.check_caller_turn("I smell gas", "none", None, 0, unlimited)
    assert first.action == "speak_lines" and first.text == GAS_LINES
    seen = []
    variant = 0
    for _ in range(4):
        again = safety.check_caller_turn(
            "I really do smell gas", "lines_said", "gas", variant, unlimited
        )
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


def test_a_repeat_mention_gets_nothing_more(agent):
    """Jackson's gas test call (call_0246b91f77e01a4b18583e6ca8b): "it keeps repeating the same
    statement". The lines are said once per hazard."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "There's a CO alarm going off")
    agent.say(call, CO_LINES)
    again = agent.turn(call, "The CO alarm is still beeping")
    assert again["data"]["safety"] is None and again["say"] == ""
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'safety'") == 1


def test_a_new_hazard_still_gets_its_own_lines(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas in the kitchen.")
    agent.say(call, GAS_LINES)
    co = agent.turn(call, "And now the carbon monoxide alarm is going off too.")
    assert co["data"]["safety"]["action"] == "speak_lines" and co["say"] == CO_LINES


def test_booking_tools_wait_for_the_lines_then_an_insisting_caller_can_book(agent):
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    agent.turn(call, "Wait, I smell gas.")
    refused = book(agent, call, slot)
    assert refused["refused"] == "gate_safety"
    # The caller's line handed out the lines; the refusal doesn't say them again.
    assert refused["say"] == ""
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
    # The agent's own goodbye is the sign-off; end_call adds no second one (Retell call
    # call_f0846787038f654309219adb1e8), and hangs up (Jackson's gas test call stayed open).
    assert signed_off["say"] == ""
    assert signed_off["data"]["hang_up"] is True


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


def test_a_dropped_safety_call_that_calls_back_starts_fresh_with_a_person_flagged(agent, clock):
    """Safety is per call (DECISIONS.md 115): the drop already flagged a person to call back, and
    the new call hears the lines only if the caller raises it again."""
    first = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(first, "I smell gas")
    agent.end(first, "dropped")
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'safety_dropped'") == 1
    clock.advance(minutes=2)
    second = agent.start(UNKNOWN)
    assert second["say"] == "" and not second["data"].get("safety")
    again = agent.turn(second["data"]["call_id"], "I still smell gas.")
    assert again["say"] == GAS_LINES


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


CO_QUESTION = CFG["vague"]["co"]["question"]


@pytest.mark.parametrize(
    "line",
    [
        "the heat's out so we've had the oven on to keep warm",
        "I opened the oven to heat the kitchen",
        "we're heating with the stove right now",
        "we've been using the burners for warmth",
        "my brother set up a generator in the basement",
        "we're running a kerosene heater in the bedroom",
        "keeping warm with the grill, it's in the living room",
        "my boiler quit so we've had the oven on with the door open to keep warm",
        "I've been leaving the oven door open a crack",
        # A negator elsewhere in the line ("no heat") doesn't cancel the use.
        "We have no heat, oven on to keep warm",
        "we have no heat and the stove is on for warmth",
        "No heat, so the oven's on to keep warm.",
    ],
)
def test_heating_with_a_fuel_burning_source_asks_the_co_check(line):
    """S-21b: heating with an oven, stove, grill or generator indoors asks the CO check."""
    assert safety.heating_signal(line, CFG)
    action = safety.check_caller_turn(line, "none", None, 0, CFG)
    assert action.action == "ask_check"
    assert action.kind == "co"
    assert action.text == CO_QUESTION


@pytest.mark.parametrize(
    "line",
    [
        "my oven works fine, it's just the heat",
        "I have a gas stove and the heat is out",
        "the generator is outside on the patio",
        "I was using the oven to cook when the heat went out",
        "I'd never use the oven for heat",
        "the kids are warm with blankets",
        "the stove is on the same gas line as the furnace",
        "My oven works fine, to keep warm we use blankets",
        "we don't have the stove on for heat",
    ],
)
def test_ordinary_mentions_of_an_oven_stove_or_generator_ask_nothing(line):
    """S-21b: the trigger is narrow, so an ordinary mention asks nothing."""
    assert not safety.heating_signal(line, CFG)
    assert safety.check_caller_turn(line, "none", None, 0, CFG).action == "none"


@pytest.mark.parametrize(
    "answer,kind",
    [
        ("No, a little bit of a smell near the water heater", "gas"),
        ("No, just a bit", "gas"),
        ("No, but it smells a little off", "gas"),
        ("no alarm but my head hurts a bit", "co"),
        ("No, but my chest feels a bit tight", "co"),
        ("No, sick and dizzy", "co"),
    ],
)
def test_a_no_followed_by_a_smell_or_symptom_still_fires_the_lines(answer, kind):
    """Review of PR #64: a leading "no" must not clear a check when a smell or symptom follows."""
    action = safety.check_caller_turn(answer, "checking", kind, 0, CFG)
    assert action.action == "speak_lines"
    assert action.kind == kind


def test_person_negators_count_only_in_the_check_answer():
    """Outside the check answer, "nobody's dizzy" is left as the existing rules read it."""
    assert safety.vague_signal("nobody's feeling sick or dizzy", CFG) == "co"
    assert safety.vague_signal("nobody's feeling sick or dizzy", CFG, answering_check=True) is None


def test_the_heating_check_is_asked_once_per_call():
    """S-21b: asked once per call, never more than it needs to be."""
    line = "we have the oven open for heat"
    # Asked already on this call, or the same question was already cleared: not asked again.
    assert safety.check_caller_turn(line, "none", None, 0, CFG, heating_asked=True).action == "none"
    assert safety.check_caller_turn(line, "cleared", "co", 0, CFG).action == "none"
    # A cleared gas question is a different one, so the carbon monoxide question is still asked.
    assert safety.check_caller_turn(line, "cleared", "gas", 0, CFG).action == "ask_check"


KEVIN_NO = (
    "no alarm going off that i've heard and nobody's feeling sick or dizzy i'm fine i just feel a "
    "little chilled"
)
ROSA_NO = (
    "um no nobody's sick no headaches we're both fine there's a carbon monoxide detector in the "
    "hallway and it's been quiet the whole time"
)
ROSA_NO_AGAIN = (
    "no um no alarm going off i've got a carbon monoxide detector in the hallway and it's quiet "
    "nobody feels sick or dizzy me and my son are both fine"
)


@pytest.mark.parametrize(
    "answer,result",
    [
        ("No, no alarm, nobody's dizzy, we're fine.", "cleared"),
        ("no one is feeling dizzy, the alarm is quiet", "cleared"),
        ("Nope, we're okay.", "cleared"),
        ("Actually yeah, I've got headaches and feel dizzy.", "speak_lines"),
        ("The CO detector just started beeping.", "speak_lines"),
        ("I'm not sure, maybe a little lightheaded.", "speak_lines"),
        # Eval runs 20261103-193421 and -193756: clear noes that still fired the lines.
        (KEVIN_NO, "cleared"),
        (ROSA_NO, "cleared"),
        (ROSA_NO_AGAIN, "cleared"),
        ("No, wait, yes, a little dizzy.", "speak_lines"),
        ("No, but I do feel dizzy.", "speak_lines"),
        ("The CO alarm is going off, it was quiet before.", "speak_lines"),
    ],
)
def test_the_answer_to_the_heating_check_decides(answer, result):
    assert safety.check_caller_turn(answer, "checking", "co", 0, CFG).action == result


def test_the_server_asks_the_heating_check_once_and_a_no_carries_on(agent):
    call = ready_to_book(agent)
    asked = agent.turn(call, "No heat since last night, so we've got the oven on to keep warm.")
    assert asked["data"]["safety"] == {"action": "ask_check", "kind": "co", "text": CO_QUESTION}
    agent.say(call, CO_QUESTION)
    cleared = agent.turn(call, "No, the alarm's quiet and nobody's dizzy.")
    assert cleared["data"]["safety"]["action"] == "cleared"
    again = agent.turn(call, "Like I said, the oven is on to keep warm.")
    assert again["data"]["safety"] is None
    assert book(agent, call, first_open(agent, call))["ok"]


def test_the_oven_priority_rule_hears_the_heating_check_wording(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    # "heating with the stove" is not one of the rule's own phrases.
    agent.turn(call, "The heat's out and we're heating with the stove.")
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 66})
    assert found["data"]["priority"]["level"] == "Urgent"
    assert "heating with an oven, stove, generator or grill indoors" in (
        found["data"]["priority"]["reasons"]
    )


@pytest.mark.links_calls
def test_the_heating_check_is_asked_once_across_a_linked_repeat_call(agent, clock):
    first = agent.start(UNKNOWN)["data"]["call_id"]
    assert agent.turn(first, "No heat, so the oven's on to keep warm.")["data"]["safety"][
        "action"
    ] == "ask_check"
    agent.say(first, CO_QUESTION)
    agent.turn(first, "No, nobody's dizzy and the alarm is quiet.")
    agent.end(first, "dropped")
    clock.advance(minutes=2)
    second = agent.start(UNKNOWN)["data"]
    assert second["linked_call"]["call_id"] == first
    again = agent.turn(second["call_id"], "Sorry, cut off. The oven is still on to keep warm.")
    assert again["data"]["safety"] is None


def test_symptoms_in_the_answer_to_the_heating_check_fire_the_co_lines(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The furnace died so we're heating with the stove.")
    agent.say(call, CO_QUESTION)
    lines = agent.turn(call, "Now that you mention it, I do have headaches and feel dizzy.")
    assert lines["data"]["safety"] == {"action": "speak_lines", "kind": "co", "text": CO_LINES}
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'safety'") == 1


def test_a_no_heat_call_that_mentions_a_gas_stove_gets_no_check(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    for line in (
        "My heat's out, it's a gas furnace.",
        "The gas stove still works so the gas is on.",
        "My oven works fine too.",
    ):
        assert agent.turn(call, line)["data"]["safety"] is None


def test_a_safety_call_flags_a_person_without_asking_for_a_callback_time(agent):
    """Eval round 1, co-alarm-clear: the caller leaving the house was asked for a callback time."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My carbon monoxide alarm is going off and it won't stop.")
    agent.say(call, CO_LINES)
    flagged = agent.tool("flag_person", call, reason="CO alarm, caller outside", priority="Urgent")
    assert flagged["ok"], flagged
    assert flagged["say"] == "I've let our team know."
    assert flagged["data"]["callback_id"] is None
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE call_id = ?", call) == 0


def test_after_the_lines_each_caller_line_reminds_the_agent_how_to_carry_on(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    before = agent.turn(call, "Hi, my carbon monoxide alarm is going off.")
    assert "safety_call" not in before["data"]["hints"]
    agent.say(call, CO_LINES)
    after = agent.turn(call, "Okay, I'm going outside now.")
    assert "safety_call" in after["data"]["hints"]
    from prompts.assemble import server_notes

    note = server_notes(after["data"]["hints"])[0]
    assert "Offer once to book" in note
    # A caller who wants a visit gets one (spec S-18); the note must not read as a no.
    assert "book if the caller wants it" in note
    assert "outside" not in note and "9-1-1" not in note


def test_the_sign_off_suits_a_caller_who_is_alone_and_asks_nothing_of_them():
    sign_off = " ".join(CFG["sign_off"].split())
    assert "everyone" not in sign_off and "hanging up" not in sign_off
    assert sign_off == "Thanks for calling Summit Air. Please stay safe, and we'll follow up with you."


def test_a_caller_saying_goodbye_after_the_sign_off_does_not_hear_it_again(agent):
    """Eval run 20261103-193421, co-alarm-clear: the sign-off was said twice in a row."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My carbon monoxide alarm is going off.")
    agent.say(call, CO_LINES)
    sign_off = agent.tool("end_call", call, reason="resolved")["say"]
    agent.say(call, sign_off)
    agent.turn(call, "Okay, goodbye.")
    bye = agent.tool("end_call", call, reason="caller_goodbye")
    assert bye["say"] == ""
    assert bye["data"]["hang_up"] is True


# ---- eval round 2: a lighter confirm question, and a visit booked on a safety call --------------

CONFIRM = CFG["vague"]["gas"]["confirm_question"]
BOOKING_NOTE = " ".join(CFG["booking_note"].split())


@pytest.mark.parametrize(
    "line",
    [
        "It smelled like burning dust for a few minutes, it's gone now",
        "there was a burning smell when the heat came on",
    ],
)
def test_a_burning_dust_smell_gets_the_confirm_question(line):
    action = safety.check_caller_turn(line, "none", None, 0, CFG)
    assert action.action == "ask_check"
    assert action.kind == "gas"
    assert action.text == CONFIRM


def test_the_confirm_question_is_asked_once_per_call():
    line = "it smelled like burning dust again this morning"
    assert safety.check_caller_turn(line, "none", None, 0, CFG, confirm_asked=True).action == "none"
    assert safety.check_caller_turn("no burning smell at all", "none", None, 0, CFG).action == "none"


@pytest.mark.parametrize(
    "answer,result",
    [
        ("Yeah", "cleared"),
        ("Yeah, it doesn't smell like eggs, just dusty", "cleared"),
        ("Right, nothing like that", "cleared"),
        ("Nope, just dusty, you know", "cleared"),
        ("No rotten eggs or sulfur, just dust", "cleared"),
        ("It doesn't smell like sulfur or rotten eggs", "cleared"),
        ("Correct", "cleared"),
        # The standard run after the merge of #72: "didn't smell like eggs" fired the lines.
        ("no nothing like that it was just dusty, it didn't smell like eggs or gas or anything", "cleared"),
        ("no nothing like that it was just dusty, like burning dust, no gas smell no alarm", "cleared"),
        ("no nothing like that, like when you first fire up a heater after the summer", "cleared"),
        # Eval run 20261104-173249: "nothing like eggs" fired the lines. A no, or at worst unclear.
        ("no, nothing like eggs, just dusty", "cleared"),
        (
            (
                "no nothing like that it was just dusty like burning dust you know the way a "
                "heater smells the first time it runs nothing like eggs it's gone now"
            ),
            "cleared",
        ),
        # Retell call call_b608687ce923e70a8318a6a6f0e: a no that names another smell is a no.
        ("No, it's not like rotten eggs, it's more like burning plastic.", "cleared"),
        ("it smells like eggs", "speak_lines"),
        ("Actually it does, a bit like eggs", "speak_lines"),
        ("No, it smells like rotten eggs", "speak_lines"),
        ("Yeah, kind of", "speak_lines"),
        ("I'm not sure", "speak_lines"),
        # PR #74 review: agreement plus anything that isn't filler is not a clear.
        ("Right, and my head hurts", "speak_lines"),
        ("Right, we feel sick", "speak_lines"),
        ("Yeah I smell something strong", "speak_lines"),
        # Unclear: no second question; the next line is read as the answer.
        ("Yeah, but the kids were coughing", "none"),
    ],
)
def test_the_confirm_question_is_asked_the_negative_way_so_yeah_clears_it(answer, result):
    """Eval round 2: "it doesn't smell like sulfur or rotten eggs, right?" is answered "yeah"
    when it doesn't. The smell named again, "it does", symptoms or an unsure answer bring the lines;
    an answer with more than agreement in it is unclear and waits for the next line."""
    action = safety.check_caller_turn(answer, "checking", "gas", 0, CFG, answering_confirm=True)
    assert action.action == result


def test_symptoms_in_the_confirm_answer_bring_the_carbon_monoxide_lines():
    action = safety.check_caller_turn(
        "Right, and my head hurts", "checking", "gas", 0, CFG, answering_confirm=True
    )
    assert action.kind == "co" and action.text == CO_LINES


def test_the_gas_check_question_still_reads_yeah_as_a_smell():
    action = safety.check_caller_turn("Yeah", "checking", "gas", 0, CFG)
    assert action.action == "speak_lines"


def test_a_cleared_burning_dust_check_books_without_the_lines(agent):
    """Eval round 2, burning-dust-first-heat."""
    call = ready_to_book(agent)
    asked = agent.turn(call, "It smelled like burning dust for a few minutes, it's gone now.")
    assert asked["data"]["safety"]["text"] == CONFIRM
    agent.say(call, CONFIRM)
    assert agent.turn(call, "Yeah, just dusty.")["data"]["safety"]["action"] == "cleared"
    again = agent.turn(call, "Yeah the burning dust smell only lasted a minute.")
    assert again["data"]["safety"] is None
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    assert BOOKING_NOTE not in booked["say"]


def test_after_the_lines_a_visit_can_be_booked_without_insisting(agent):
    """Eval round 2, already-outside-asks-for-visit: the lines and the flag come first, then the
    agent may offer a visit for after the problem is shut off, and the server says so."""
    call = ready_to_book(agent)
    agent.turn(call, "My carbon monoxide alarm went off.")
    agent.say(call, CO_LINES)
    turn = agent.turn(call, "Okay, I'm outside now.")
    assert "safety_call" in turn["data"]["hints"]
    booked = book(agent, call, first_open(agent, call, "safety_check"), job_type="safety_check")
    assert booked["ok"], booked
    assert booked["say"].endswith(f"{BOOKING_NOTE} Anything else?")
    assert booked["data"]["safety_override"] is True
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'safety'") == 1


def test_the_safety_call_note_offers_a_visit_and_never_a_callback_time():
    from prompts.assemble import SERVER_NOTES

    note = SERVER_NOTES["safety_call"]
    assert "Offer once to book" in note and "Never ask for a callback time" in note
    assert "insist" not in note


def test_a_safety_caller_who_leaves_after_the_read_back_gets_a_person_to_book(agent):
    """PR #74 review: no time was picked, so nothing is booked; a person calls back to book."""
    call = ready_to_book(agent)
    agent.turn(call, "My carbon monoxide alarm went off.")
    agent.say(call, CO_LINES)
    agent.turn(call, "I'm outside, I have to go call 911 now, bye.")
    agent.end(call, "caller_hangup")
    reasons = [
        r[0]
        for r in agent.db().execute("SELECT reason FROM flags WHERE kind = 'person'").fetchall()
    ]
    assert any("left before a time was set" in r for r in reasons)
    assert count(agent, "SELECT COUNT(*) FROM bookings") == 0


def test_a_plain_call_that_ends_after_the_read_back_gets_no_safety_flag(agent):
    call = ready_to_book(agent)
    agent.end(call, "caller_hangup")
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'person'") == 0


def test_the_gas_check_is_asked_once_and_an_unclear_answer_waits_for_the_next_line(agent):
    """Retell call call_b608687ce923e70a8318a6a6f0e: while the caller was still answering the
    confirm question, the plain gas question was asked too. Jackson: less repetitive."""
    call = ready_to_book(agent)
    agent.turn(call, "It smelled like burning dust for a minute when the heat came on.")
    agent.say(call, CONFIRM)
    unclear = agent.turn(call, "Right, but the dog has been acting strange")
    assert unclear["data"]["safety"] is None and unclear["say"] == ""
    partial = agent.turn(call, "No,")
    assert partial["data"]["safety"]["action"] == "cleared"
    later = agent.turn(call, "Yeah, it's a house, the one bedroom upstairs is cold.")
    assert later["data"]["safety"] is None


def test_the_confirm_answer_is_read_the_negative_way_after_the_agent_moves_on(agent):
    call = ready_to_book(agent)
    agent.turn(call, "There was a burning smell when the heat came on.")
    agent.say(call, f"Got it. {CONFIRM}")
    assert agent.turn(call, "Yeah, just dusty.")["data"]["safety"]["action"] == "cleared"


# ---- eval round 3: safety tone (DECISIONS.md 033, evals/rounds/03-judge-labels.md) ---------------


@pytest.mark.parametrize(
    "line",
    [
        "I didn't go down there, I don't want to mess with anything if I smell gas or anything",
        "is it a gas leak?",
        "I'm worried about a gas leak, honestly",
        "I don't know if I smell gas",
    ],
)
def test_a_worry_with_nothing_described_gets_the_check_question_not_the_lines(line):
    action = safety.check_caller_turn(line, "none", None, 0, CFG)
    assert action.action == "ask_check"
    assert action.text == CFG["vague"]["gas"]["question"]


@pytest.mark.parametrize(
    "line",
    [
        "could it be a gas thing?",
        "it's a gas furnace, could that be why it's loud?",
    ],
)
def test_a_question_about_gas_with_no_smell_asks_nothing(line):
    assert safety.check_caller_turn(line, "none", None, 0, CFG).action == "none"


@pytest.mark.parametrize(
    "line",
    [
        "what if there's a gas leak? I smell gas in the kitchen",
        "if it's on, I smell gas",
        "I smell gas, is it a gas leak?",
    ],
)
def test_a_report_next_to_a_worry_word_still_brings_the_lines(line):
    assert safety.check_caller_turn(line, "none", None, 0, CFG).action == "speak_lines"


@pytest.mark.parametrize(
    "line,stands_down",
    [
        ("wait, I didn't say I smelled gas, I just didn't want to go near the furnace", True),
        ("there's no gas smell", True),
        ("no, I never smelled gas, I meant if", True),
        ("I don't smell it anymore", False),
        ("I don't smell gas out here", False),
        ("I smell gas, I didn't say it was bad", False),
        ("I still don't smell anything", False),
    ],
)
def test_after_the_lines_only_a_correction_stands_the_call_down(line, stands_down):
    action = safety.check_caller_turn(line, "lines_said", "gas", 0, CFG)
    assert (action.action == "cleared") is stands_down


def test_a_stood_down_call_books_and_ends_like_any_other(agent):
    from prompts.assemble import server_notes

    call = ready_to_book(agent)
    agent.turn(call, "Is the gas smell thing a problem? I smell gas near the stove sometimes.")
    agent.say(call, GAS_LINES)
    turn = agent.turn(call, "Wait, I didn't say I smelled gas. I said if.")
    assert turn["data"]["safety"]["action"] == "cleared"
    assert "safety_stood_down" in turn["data"]["hints"]
    assert "no longer a safety call" in server_notes(turn["data"]["hints"])[0]
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'safety_check_cleared'") == 1
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    assert booked["data"]["safety_override"] is False
    agent.say(call, booked["say"])
    agent.turn(call, "That's all, bye.")
    bye = agent.tool("end_call", call, reason="caller_goodbye")
    assert bye["say"] == "" and bye["data"]["hang_up"] is True


def test_the_caller_hears_the_lines_once_then_nothing_more(agent):
    """Eval round 3, gas-downplayed-wants-booking, and Jackson's gas test call: the caller stays in
    control, and the lines aren't repeated."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "There's a gas smell by the furnace but that's normal.")
    agent.say(call, GAS_LINES)
    again = agent.turn(call, "Come on, it's just a little gas smell, it always does that.")
    assert again["data"]["safety"] is None and again["say"] == ""
    third = agent.turn(call, "I told you, the gas smell is normal. Just book Thursday.")
    assert third["data"]["safety"] is None
    assert third["say"] == ""
    assert "safety_call" in third["data"]["hints"]


def test_end_call_never_says_the_sign_off_itself(agent):
    """Eval round 3, after-hours-no-heat-cold: the sign-off closed three turns in a row; Retell
    call call_f0846787038f654309219adb1e8 heard it after the agent's own goodbye."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My carbon monoxide alarm is going off.")
    agent.say(call, CO_LINES)
    for _ in range(2):
        ended = agent.tool("end_call", call, reason="resolved")
        assert ended["ok"] and ended["say"] == ""
        assert ended["data"]["hang_up"] is True and ended["data"]["signed_off"] is True


def test_a_caller_on_a_safety_call_can_ask_for_a_callback_at_their_time(agent):
    """Eval round 3: "the agent says it can't schedule a callback... why not?"."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas in the basement.")
    agent.say(call, GAS_LINES)
    agent.turn(call, "Okay, I'm outside. Can someone call me back after 5?")
    flagged = agent.tool(
        "flag_person", call, reason="Gas smell; wants a callback", callback_after="5 PM"
    )
    assert flagged["ok"], flagged
    assert flagged["say"] == "I've let our team know, and someone will call you around 5:30 PM."
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE status = 'pending'") == 1


@pytest.mark.parametrize(
    "line",
    [
        "nobody smelled anything, so it's not a gas leak or anything",
        "I haven't smelled anything, nothing like rotten eggs",
    ],
)
def test_a_denial_with_an_article_or_nothing_like_triggers_nothing(line):
    """Eval round 3 run 20261104-014518, gas-worry-no-smell: "it's not a gas leak" brought the
    gas lines."""
    assert safety.check_caller_turn(line, "none", None, 0, CFG).action == "none"


def test_a_cleared_check_is_not_asked_again_for_the_same_kind():
    """Eval round 3 run 20261104-024140, oven-heat-co-check: "nobody's dizzy" after the cleared
    carbon monoxide question asked it a second time."""
    again = safety.check_caller_turn("we're fine, nobody's dizzy or sick", "cleared", "co", 0, CFG)
    assert again.action == "none"
    other = safety.check_caller_turn("there's a weird smell in the basement", "cleared", "co", 0, CFG)
    assert other.action == "ask_check" and other.kind == "gas"


def test_a_denial_that_ends_with_or_anything_like_eggs_clears_the_confirm_question(agent):
    """Eval round 3 run 20261104-031113, burning-dust-first-heat."""
    call = ready_to_book(agent)
    agent.turn(call, "It smelled like burning dust for a few minutes when the heat came on.")
    agent.say(call, CONFIRM)
    answer = agent.turn(
        call,
        "no nothing like that it was just dusty, it's gone now, no gas smell or anything like eggs",
    )
    assert answer["data"]["safety"]["action"] == "cleared"


def test_i_dont_feel_sick_or_dizzy_is_a_no_and_i_wont_use_the_oven_asks_nothing():
    """Eval round 4 run 20261104-042822, no-heat-gas-stove: both brought the carbon monoxide
    question or lines on a call with no hazard."""
    answer = "no nothing like that no alarm going off and i don't feel sick or dizzy just cold"
    assert safety.check_caller_turn(answer, "checking", "co", 0, CFG).action == "cleared"
    promise = "okay i won't use the stove or oven for heat and the generator's staying off"
    assert safety.check_caller_turn(promise, "none", None, 0, CFG).action == "none"


def test_the_carbon_monoxide_alarms_quiet_is_a_no_to_the_gas_check():
    """Eval run 20261104-060544, vague-smell-cleared: "the carbon monoxide alarm's quiet too"."""
    answer = "no um definitely not gas the carbon monoxide alarm's quiet too i think it's the trash"
    assert safety.check_caller_turn(answer, "checking", "gas", 0, CFG).action == "cleared"


def test_asking_whether_they_smell_gas_is_a_worry_not_a_report():
    """Eval run 20261104-154616, gas-worry-no-smell: the caller echoed the question and the gas
    lines fired."""
    line = "oh um no we're still inside do you smell gas do i smell gas i don't think i do"
    assert safety.check_caller_turn(line, "none", None, 0, CFG).action != "speak_lines"
    assert safety.check_caller_turn("I do smell gas, yes", "none", None, 0, CFG).action == "speak_lines"


@pytest.mark.parametrize("line", ["Do you smell gas? I do.", "do i smell gas yeah i think so"])
def test_an_echoed_question_answered_yes_is_a_report(line):
    """PR #102 review: "Do you smell gas? I do" must bring the lines at once."""
    assert safety.check_caller_turn(line, "none", None, 0, CFG).action == "speak_lines"


@pytest.mark.parametrize(
    "line",
    [
        "What do I do? I smell gas",
        "what should I do, I smell gas in the kitchen",
        "do I smell gas? yes, strongly",
    ],
)
def test_a_real_gas_report_after_a_question_word_brings_the_lines(line):
    """PR #102 review: the bare "do i", "should i" and "would i" worries held these back."""
    assert safety.check_caller_turn(line, "none", None, 0, CFG).action == "speak_lines", line


def test_if_we_do_smell_gas_what_should_we_do_is_a_worry():
    """Eval run 20261104-163112, gas-worry-no-smell: "we do" in the question read as an answer."""
    line = "yes that's right but if we do smell gas what should we do"
    assert safety.check_caller_turn(line, "none", None, 0, CFG).action != "speak_lines"


@pytest.mark.parametrize(
    "line",
    [
        "The c o alarm is going off",
        "my c.o. detector keeps beeping",
        "the see oh alarm went off",
        "the CO2 detector is going off and I feel dizzy",
    ],
)
def test_carbon_monoxide_spelled_by_speech_to_text_brings_the_lines(line):
    """The Ink-2 lane: Deepgram Nova-3 wrote "The c o alarm is going off" for the same clip twice."""
    action = safety.check_caller_turn(line, "none", None, 0, CFG)
    assert action.action == "speak_lines" and action.kind == "co", line


@pytest.mark.parametrize(
    "line",
    ["I paid c o d for the last part", "the company co sent a bill, my heat is out"],
)
def test_c_o_without_an_alarm_or_symptom_is_not_carbon_monoxide(line):
    assert safety.check_caller_turn(line, "none", None, 0, CFG).action == "none"


@pytest.mark.parametrize(
    "line",
    [
        "I reset the breaker and got nothing. Gas smell in the basement.",
        "Heat's out, thermostat shows nothing. Smells like gas down there.",
    ],
)
def test_a_negation_never_reaches_across_a_sentence_end(line):
    # PR #126 review: "nothing" as a general negator hid these gas reports.
    assert safety.clear_signal(line, CFG) == "gas"


@pytest.mark.parametrize(
    "answer,result",
    [
        ("Nothing, the gas smell is strong", "yes"),
        ("nothing but rotten eggs", "yes"),
        ("no, it does smell like eggs", "yes"),
        ("no, nothing like eggs, just dusty", "no"),
    ],
)
def test_nothing_negates_only_a_like_phrase_in_a_check_answer(answer, result):
    assert safety.answer_to_check(answer, CFG) == result


@pytest.mark.parametrize("line", ["No. Gas smell in the basement.", "Never. Gas smell in the garage."])
def test_a_one_word_no_before_a_gas_report_still_brings_the_lines(line):
    # PR #126 re-check: the live turn check joined the sentences and "no" negated the smell.
    assert safety.check_caller_turn(line, "none", None, 0, CFG).action == "speak_lines"


@pytest.mark.parametrize(
    "line,action,kind",
    [
        # Retell eval suite round 1: the lines fired on a hedged denial. Unsure, so the question.
        ("I don't think there's a CO alarm going off", "ask_check", "co"),
        ("I don't believe there's a carbon monoxide alarm going off", "ask_check", "co"),
        ("No, the CO alarm isn't going off.", "none", None),
        ("There's no CO alarm going off.", "none", None),
        ("My CO alarm is going off.", "speak_lines", "co"),
        ("I think there's a gas smell.", "speak_lines", "gas"),
        ("It's not, there is a gas leak.", "speak_lines", "gas"),
        ("Oh no, the carbon monoxide alarm is going off.", "speak_lines", "co"),
    ],
)
def test_a_hedged_denial_gets_the_check_question_and_a_plain_one_nothing(line, action, kind):
    found = safety.check_caller_turn(line, "none", None, 0, CFG)
    assert (found.action, found.kind) == (action, kind)


GAS_CALL = "call_0246b91f77e01a4b18583e6ca8b"


def test_the_lines_are_advice_with_9_1_1_written_out_and_one_question_the_dispatch_offer():
    """Jackson's gas test call: "it shouldn't be demanding me to do anything", and "911" was read
    as nine hundred and eleven. The one question is the offer of a technician (DECISIONS.md 129),
    never "are you outside?"."""
    for kind in ("gas", "co"):
        lines = " ".join(CFG["kinds"][kind]["lines"].split())
        assert "the safest thing is" in lines and "9-1-1" in lines
        assert "911" not in lines.replace("9-1-1", "") and lines.count("?") == 1
        assert lines.endswith("would you like us to send a technician out right away?")
        assert not lines.startswith("Please")
    assert CFG["repeat_limit"] == 0
    assert "9-1-1" not in CFG["booking_note"] and "stay outside" not in CFG["booking_note"]


def test_the_lines_count_as_said_once_the_agent_has_spoken_even_in_pieces(agent):
    """Jackson's gas test call: the agent's line reached the server in pieces, so the lines never
    matched and the caller's next line ("No, I mean...") was handed them again."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Well, it smells a little like gas, like near the boiler. Is that normal?")
    # The lines reach the server in two pieces, neither of them whole.
    agent.say(call, "If you're smelling gas, the safest thing is to get everyone outside")
    agent.say(call, "and call 9-1-1 or the gas company from there.")
    nxt = agent.turn(call, "Um, I just want you to schedule a technician.")
    assert nxt["say"] == "" and nxt["data"]["safety"] is None
    state = agent.db().execute("SELECT safety_state FROM calls WHERE id = ?", (call,)).fetchone()[0]
    assert state == "lines_said"


def test_the_agent_saying_911_as_a_number_still_counts_as_the_lines(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas.")
    agent.say(call, GAS_LINES.replace("9-1-1", "911"))
    state = agent.db().execute("SELECT safety_state FROM calls WHERE id = ?", (call,)).fetchone()[0]
    assert state == "lines_said"


@pytest.mark.parametrize("said", [
    "No, I mean, I don't think it's leaking.",
    "Wait, never mind, I'm tripping.",
    "Nvm, it's not gas.",
    "False alarm, sorry.",
    "Okay, it's fine.",
    "It's been checked.",
    "I checked with the gas company. And they said it's fine.",
    "Oh, actually no, my roommate was cooking, it's gone now.",
])
def test_a_caller_who_takes_it_back_clears_the_call_and_can_book(agent, said):
    """Jackson's gas test call: "the caller should be able to be like 'wait nvm, im tripping'...
    it should be able to get out of its own safety loop"."""
    call = ready_to_book(agent)
    agent.turn(call, "It smells a little like gas near the boiler.")
    agent.say(call, GAS_LINES)
    took_back = agent.turn(call, said)
    assert took_back["data"]["safety"]["action"] == "cleared", said
    assert "safety_stood_down" in took_back["data"]["hints"]
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    assert "safety_override" not in booked["data"] or not booked["data"]["safety_override"]
    assert CFG["booking_note"].split()[0] not in booked["say"]
    notes = [r[0] for r in agent.db().execute("SELECT text FROM notes WHERE attach_to = 'job'")]
    assert any(n.startswith("Caller took back the gas smell report") for n in notes), notes
    ended = agent.tool("end_call", call, reason="caller_goodbye")
    assert ended["say"] != " ".join(CFG["sign_off"].split())


def test_a_take_back_before_the_lines_are_said_clears_too(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas.")
    cleared = agent.turn(call, "Never mind, false alarm, it's the stove pilot.")
    assert cleared["data"]["safety"]["action"] == "cleared"


@pytest.mark.parametrize("said", [
    "My roommate was cooking and now I smell gas.",
    "Never mind the boiler, I still smell gas.",
    "It's fine, I'm outside now.",
    "Maybe it's fine, I'm not sure.",
])
def test_a_line_that_still_reports_it_or_only_moved_outside_is_not_a_take_back(agent, said):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas.")
    agent.say(call, GAS_LINES)
    turn = agent.turn(call, said)
    assert (turn["data"]["safety"] or {}).get("action") != "cleared", said


def test_a_caller_who_still_wants_a_visit_after_the_lines_books_with_the_note(agent):
    """Jackson's gas test call: booking after the lines is allowed once a person is flagged, with
    the booking note, which advises rather than demands."""
    call = ready_to_book(agent)
    agent.turn(call, "There's a gas smell by the boiler.")
    agent.say(call, GAS_LINES)
    agent.turn(call, "I still want a technician to come out.")
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    assert booked["data"]["safety_override"] is True
    assert booked["say"].endswith(" ".join(CFG["booking_note"].split()) + " Anything else?") or (
        " ".join(CFG["booking_note"].split()) in booked["say"]
    )


def test_the_greetings_name_brooke():
    """Jackson's gas test call: the assistant is Brooke, and a touch warmer."""
    voice = load_agent(TENANT_DIR)[0]["voice"]
    assert voice["greeting_unknown"] == (
        "Hi there, thanks for calling Summit Air! I'm Brian, the virtual "
        "assistant. How can I help?"
    )
    assert "I'm Brian" in voice["greeting_known"]
