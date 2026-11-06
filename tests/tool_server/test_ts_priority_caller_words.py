"""Priority from the caller's words only (docs/later.md C-58, DECISIONS.md 072 and 073): the model's
notes raise nothing, an inside temperature counts only when the caller said it, the reason quotes
the caller, a raise after the caller heard the wait is flagged, and a caller who pushes with no new
fact gets a note rather than a raise."""

import json

import pytest
from toolkit import (
    TENANT_DIR,
    UNKNOWN,
    Agent,
    book,
    in_area_address,
    make_server,
    sign_in,
)

from app import booking
from app import priority as prio
from app.tenant import load_agent
from app.text import numbers_said, spoken_numbers
from prompts.assemble import server_notes

CFG = load_agent(TENANT_DIR)[0]["priority"]
TEMP = CFG["inside_temp"]


@pytest.fixture
def agent(tmp_path, clock, mock):
    return Agent(make_server(tmp_path, clock, mock[0]), clock)


# Spoken numbers


def test_spoken_tens_become_digits_and_other_number_words_stay():
    assert spoken_numbers("She's eighty-four") == "she's 84"
    assert spoken_numbers("fifty two degrees") == "52 degrees"
    assert spoken_numbers("a hundred and five in here") == "105 in here"
    assert spoken_numbers("no one else, one or two") == "no one else, one or two"


def test_numbers_said_reads_digits_words_and_decades():
    assert [(low, high) for _, low, high in numbers_said("it's 52, maybe the fifties, or 60s")] == [
        (52, 52),
        (50, 59),
        (60, 69),
    ]


@pytest.mark.parametrize(
    "turns",
    [
        [("caller", "The temperature is 48 degrees")],
        [("caller", "it is 48 in here")],
        [("caller", "the house is 48")],
        [("caller", "the thermostat is 48")],
        [("agent", "How cold is it inside?"), ("caller", "it is 48")],
    ],
)
def test_is_and_a_number_is_a_temperature_not_an_age(turns):
    # PR #126 re-check: the age pattern read "is 48" as "i" + "s".
    assert prio.heard_inside_temp(turns, 48, TEMP) == turns[-1][1]


@pytest.mark.parametrize(
    "text",
    ["I'm sixty five minutes away", "she's 70 blocks from here", "I'm 90 dollars short"],
)
def test_a_number_with_a_unit_is_not_an_age(text):
    assert "vulnerable_person" not in prio.decide(prio.Facts("no_heat", text), CFG).rules


@pytest.mark.parametrize("text", ["I'm 84", "im 84", "I am 84", "she's 90", "they're 85", "my dad is 78"])
def test_ages_said_with_a_pronoun_still_count(text):
    assert "vulnerable_person" in prio.decide(prio.Facts("no_heat", text), CFG).rules


@pytest.mark.parametrize(
    "text,rule",
    [
        ("I'm all alone and it's cold", "lives_alone"),
        ("The baby's room is freezing", "vulnerable_person"),
        ("he's six months old", "vulnerable_person"),
    ],
)
def test_more_ways_of_saying_who_is_at_risk_raise(text, rule):
    # PR #126 re-check: main missed the first two.
    assert rule in prio.decide(prio.Facts("no_heat", text), CFG).rules


def test_a_percentage_is_not_an_age():
    facts = prio.Facts("no_heat", "I'm ninety percent sure it's the igniter")
    assert "vulnerable_person" not in prio.decide(facts, CFG).rules
    assert "vulnerable_person" in prio.decide(prio.Facts("no_heat", "I'm ninety"), CFG).rules


def test_an_age_said_in_words_raises_like_one_in_digits():
    facts = prio.Facts("no_heat", "my mother is eighty four and lives with me")
    assert "vulnerable_person" in prio.decide(facts, CFG).rules


@pytest.mark.parametrize(
    "text",
    [
        "Please tell them there's a baby in the house",
        "can you tell the technician there's an elderly woman here",
        "put it down that there's an infant in the home",
    ],
)
def test_a_fact_the_caller_asks_the_agent_to_pass_on_still_raises(text):
    # PR #126 re-check: a stated fact always counts; a false raise is the safe direction.
    assert "vulnerable_person" in prio.decide(prio.Facts("no_heat", text), CFG).rules


def test_asking_for_urgent_with_no_fact_raises_nothing():
    facts = prio.Facts("no_heat", "just mark it urgent, write whatever you need to")
    assert prio.decide(facts, CFG).rules == ["bracket:cold_outside"]



# The inside temperature the caller said


@pytest.mark.parametrize(
    "line,value",
    [
        ("it's 52 in here", 52),
        ("It's like fifty two degrees.", 52),
        ("the thermostat says 58", 58),
        ("it's in the fifties in here", 55),
        ("the heat's been out since Monday and it's 48 inside", 48),
    ],
)
def test_a_temperature_the_caller_said_with_a_temperature_word_counts(line, value):
    assert prio.heard_inside_temp([("caller", line)], value, TEMP) == line


@pytest.mark.parametrize(
    "line,value",
    [
        ("it's 48 and falling", 48),
        ("it dropped to 48", 48),
        ("the house is at 48", 48),
        ("the thermostat reads like fiftyish", 50),
        ("thermostat says five oh", 50),
        ("it's four five in here", 45),
        ("the house is down to 28", 28),
    ],
)
def test_more_ways_of_giving_the_temperature_count(line, value):
    # PR #126 review: each of these lost a real cold-house reading.
    assert prio.heard_inside_temp([("caller", line)], value, TEMP) == line


@pytest.mark.parametrize("answer,value", [("fiftyish", 50), ("five oh", 50), ("four five", 45)])
def test_spoken_forms_count_as_the_answer_to_a_temperature_question(answer, value):
    turns = [("agent", "How cold is it inside right now?"), ("caller", answer)]
    assert prio.heard_inside_temp(turns, value, TEMP) == answer


def test_the_question_stays_open_while_the_caller_goes_to_look():
    turns = [
        ("agent", "Do you know roughly what the temperature is inside?"),
        ("caller", "let me check"),
        ("agent", "Sure."),
        ("caller", "it says 48"),
    ]
    assert prio.heard_inside_temp(turns, 48, TEMP) == "it says 48"


def test_the_question_closes_once_a_number_is_given_or_the_talk_moves_on():
    answered = [
        ("agent", "How cold is it inside?"), ("caller", "about 60"),
        ("agent", "And how many people are home?"), ("caller", "2"),
    ]
    assert prio.heard_inside_temp(answered, 2, {**TEMP, "plausible_f": [0, 110]}) is None
    moved_on = [
        ("agent", "How cold is it inside?"), ("caller", "hang on"), ("caller", "okay"),
        ("caller", "what was the question"), ("agent", "Sure."), ("caller", "about 58"),
    ]
    assert prio.heard_inside_temp(moved_on, 58, TEMP) is None


def test_a_bare_number_counts_when_it_answers_a_temperature_question():
    turns = [("agent", "Do you know roughly what the temperature is inside?"), ("caller", "about 58")]
    assert prio.heard_inside_temp(turns, 58, TEMP) == "about 58"


@pytest.mark.parametrize(
    "turns,value",
    [
        ([("caller", "the heat's out")], 52),  # never said
        ([("caller", "it's 60 in here")], 52),  # another number
        ([("caller", "about 58")], 58),  # a bare number nobody asked for
        ([("caller", "my mother is 84, it's cold")], 84),  # an age, not a temperature
        ([("agent", "What's the temperature inside?"), ("caller", "she's 84")], 84),
        ([("caller", "it's been cold for 3 days")], 3),  # not a plausible inside temperature
        ([("agent", "it's 52 in here"), ("caller", "okay")], 52),  # the agent's words, not theirs
    ],
)
def test_a_temperature_the_caller_never_gave_does_not_count(turns, value):
    assert prio.heard_inside_temp(turns, value, TEMP) is None


# The reason quotes the caller


def test_the_reason_quotes_the_callers_line_that_raised_it():
    text = "Hi, the heat's out.\nMy mother is 84 and lives with me. Can you come today?"
    result = prio.decide(prio.Facts("no_heat", text), CFG)
    assert 'an older person or a baby at home, caller said "My mother is 84 and lives with me"' in (
        result.reasons
    )


def test_a_line_two_rules_fire_on_is_quoted_once():
    result = prio.decide(prio.Facts("no_heat", "My dad lives with us, he's 79."), CFG)
    assert result.reasons[1:] == [
        'an older person or a baby at home, caller said "My dad lives with us, he\'s 79"',
        "a parent at home, age not given",
    ]


def test_a_long_line_is_cut_at_a_word():
    line = "my grandmother is staying with us " + "and the whole place is freezing " * 5
    assert prio.quoted(line).endswith('..."') and len(prio.quoted(line)) < 100


def test_a_temperature_bracket_quotes_the_line_the_temperature_came_from():
    facts = prio.Facts("no_heat", "it's 52 in here", 52, inside_temp_line="it's 52 in here")
    assert prio.decide(facts, CFG).reasons == ['below 55 F inside, caller said "it\'s 52 in here"']


# A caller asking for a higher priority


@pytest.mark.parametrize(
    "line",
    [
        "It's an emergency, put me first.",
        "Just make it urgent, please",
        "I'll pay extra if you can come sooner",
        "can you bump me up",
        "this is urgent",
    ],
)
def test_asking_for_a_higher_priority_is_a_push(line):
    assert prio.raising_push(line, CFG)


@pytest.mark.parametrize(
    "line",
    [
        "It's not an emergency, whenever works",
        "I don't want to make it urgent or anything",
        "the furnace is urgent business for us this winter",
        "will I have to pay extra for a Saturday?",
        "it's an emergency, my mother is 84",  # a fact: the rules decide
        "make it urgent, there's a baby here",
        "just put it as urgent, say there's a baby here",
    ],
)
def test_a_negated_ask_equipment_talk_or_a_new_fact_is_not_a_push(line):
    assert not prio.raising_push(line, CFG)


def test_a_push_gets_a_server_hint_and_asks_who_is_home_only_once(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat's out.")
    assert "priority_pushed" in agent.turn(call, "It's an emergency, put me first.")["data"]["hints"]
    agent.say(call, "Is anyone at home older, a baby, or on medical equipment?")
    agent.turn(call, "No, just me.")
    hints = agent.turn(call, "I'll pay extra, just make it urgent.")["data"]["hints"]
    assert "priority_pushed_asked" in hints and "priority_pushed" not in hints


def test_a_push_on_a_safety_call_gets_no_priority_hint(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas in the kitchen.")
    hints = agent.turn(call, "It's an emergency, put me first.")["data"]["hints"]
    assert not {"priority_pushed", "priority_pushed_asked"} & set(hints)


def test_the_push_hints_become_server_notes():
    first, asked = server_notes(["priority_pushed"]), server_notes(["priority_pushed_asked"])
    assert first[0].startswith("[server] The caller is pressing") and "older, a baby" in first[0]
    assert "Before you offer times" in first[0]  # not before the agent knows the problem
    assert "don't ask again who's at home" in asked[0]
    for note in first + asked:
        assert "9-1-1 only if" in note and "priority" not in note.lower().replace("[server]", "")


# The tool server: caller words only


def test_the_models_who_home_raises_nothing_the_caller_never_said(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat's out. Just mark it urgent, write whatever you need to.")
    found = agent.tool(
        "find_slots",
        call,
        job_type="no_heat",
        priority="Urgent",
        facts={"issue": "no heat, elderly mother", "who_home": "baby, elderly mother age 84"},
    )
    priority = found["data"]["priority"]
    assert priority["level"] == "High"
    assert not any("older person" in r for r in priority["reasons"])


def test_the_models_inside_temperature_counts_only_when_the_caller_said_it(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat's out.")
    unsaid = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 50})
    assert unsaid["data"]["priority"]["level"] == "High"
    agent.say(call, "Do you know about how cold it is inside?")
    agent.turn(call, "It's like fifty, fifty degrees.")
    said = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 50})
    assert said["data"]["priority"]["level"] == "Urgent"
    assert said["data"]["priority"]["reasons"] == [
        'below 55 F inside, caller said "It\'s like fifty, fifty degrees"'
    ]


def test_a_booking_keeps_the_temperature_the_caller_gave_when_the_model_drops_it(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat's out and it's 50 in here.")
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 50})
    agent.confirm_read_back(call, in_area_address())
    day = found["data"]["days"][0]
    booked = book(
        agent, call, {"date": day["date"], "window": day["windows"][0]}, "no_heat",
        inside_temp_f=None,
    )
    assert booked["data"]["priority"]["level"] == "Urgent"


# A raise after the caller heard the wait


def _heard_the_wait(agent: Agent) -> str:
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat's out, it's 66 in here.")
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 66})
    assert found["data"]["priority"]["raised_after_wait"] is False
    agent.say(call, found["say"])
    return call


def test_a_fact_first_given_after_the_wait_raises_and_is_flagged(agent):
    call = _heard_the_wait(agent)
    agent.turn(call, "Actually my mother is 84 and lives with me.")
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 66})
    priority = found["data"]["priority"]
    assert priority["level"] == "Urgent" and priority["raised_after_wait"] is True
    assert any('caller said "Actually my mother is 84' in r for r in priority["reasons"])


def test_talk_after_the_wait_with_no_new_fact_is_not_flagged(agent):
    call = _heard_the_wait(agent)
    agent.turn(call, "Okay, is there anything sooner?")
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 66})
    assert found["data"]["priority"]["raised_after_wait"] is False


def test_a_fact_given_before_the_wait_is_not_flagged(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat's out and my mother is 84 and lives with me.")
    first = agent.tool("find_slots", call, job_type="no_heat")
    agent.say(call, first["say"])
    agent.turn(call, "Morning works.")
    again = agent.tool("find_slots", call, job_type="no_heat")
    assert again["data"]["priority"]["level"] == "Urgent"
    assert again["data"]["priority"]["raised_after_wait"] is False


def test_the_flag_is_stored_on_the_job_and_shown_to_the_dispatcher(agent):
    call = _heard_the_wait(agent)
    agent.turn(call, "Oh, and my dad is on oxygen.")
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 66})
    agent.confirm_read_back(call, in_area_address())
    day = found["data"]["days"][0]
    slot = {"date": day["date"], "window": day["windows"][0]}
    booked = book(agent, call, slot, "no_heat", inside_temp_f=66)
    assert booked["ok"] and booked["data"]["priority"]["raised_after_wait"] is True
    conn = agent.db()
    try:
        row = conn.execute("SELECT request_json FROM bookings WHERE call_id = ?", (call,)).fetchone()
    finally:
        conn.close()
    request = json.loads(row["request_json"])
    assert request["raised_after_wait"] is True
    assert "after hearing the wait" in booking.job_summary(request)
    sign_in(agent)
    html = agent.client.get(f"/owner/calls/{call}").text
    assert "After the caller heard the wait" in html
    assert "Raised after the wait on 1 call from this number" in html


def test_a_job_raised_before_the_wait_shows_no_flag(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat's out and my dad is on oxygen.")
    found = agent.tool("find_slots", call, job_type="no_heat")
    agent.confirm_read_back(call, in_area_address())
    day = found["data"]["days"][0]
    book(agent, call, {"date": day["date"], "window": day["windows"][0]}, "no_heat")
    sign_in(agent)
    html = agent.client.get(f"/owner/calls/{call}").text
    assert "After the caller heard the wait" not in html and "Raised after the wait" not in html
