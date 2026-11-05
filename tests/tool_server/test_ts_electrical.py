"""The electrical route and water and breaker priority (DECISIONS.md 082): sparks get their own calm
lines, said once and never the gas or carbon monoxide ones; water through the ceiling is Urgent; a
breaker that keeps tripping is High. Gas, carbon monoxide and at-risk callers raise and fire exactly
as before."""

import pytest
from toolkit import TENANT_DIR, book, first_open, in_area_address, ready_to_book

from app import priority as prio
from app import safety
from app.tenant import load_agent

AGENT = load_agent(TENANT_DIR)[0]
SAFETY = AGENT["safety"]
CFG = AGENT["priority"]
ELECTRICAL = " ".join(SAFETY["electrical"]["lines"].split())
GAS_LINES = " ".join(SAFETY["kinds"]["gas"]["lines"].split())
CO_LINES = " ".join(SAFETY["kinds"]["co"]["lines"].split())


@pytest.mark.parametrize(
    "line",
    [
        "The AC unit is sparking",
        "I saw sparks coming out of the furnace",
        "it sparked when I turned it on",
        "there's a burning plastic smell from the vent",
        "It smells electrical near the air handler",
        "there's smoke coming from the unit",
        "the panel is smoking",
        "No. It sparked again this morning.",
        "it sparked once last year and now it's sparking again",
        "it's arcing at the disconnect",
        "I saw a spark",
        "there was a spark from the outdoor unit",
        "there's smoke from the furnace",
        "there's a burning wire smell",
        "a burnt smell from the AC",
    ],
)
def test_sparks_arcing_and_electrical_smells_fire(line):
    assert safety.electrical_signal(line, SAFETY)


@pytest.mark.parametrize(
    "line",
    [
        "no sparks",
        "there aren't any sparks or anything",
        "it's not sparking, it just won't turn on",
        "it sparked once last year and it's been fine",
        "it sparked a while ago but they fixed it",
        "there was a spark last year but it's been fine",
        "my breaker keeps tripping",
        "the furnace smells like burning dust the first time it runs",
        "I smell gas",
        "the outlet works fine",
    ],
)
def test_negations_history_and_other_problems_dont_fire(line):
    assert not safety.electrical_signal(line, SAFETY)


def test_the_electrical_lines_are_said_once_and_the_gas_state_is_untouched(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    first = agent.turn(call_id, "My AC is sparking and the breaker keeps tripping.")
    assert first["say"] == ELECTRICAL
    assert first["data"]["spoken"] == {"kind": "electrical", "text": ELECTRICAL}
    assert first["data"]["safety"] is None
    assert "journey:safety" not in first["data"]["hints"]
    again = agent.turn(call_id, "Okay, I turned it off. It was sparking a lot.")
    assert again["data"]["spoken"] is None
    conn = agent.db()
    try:
        call = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
        row = conn.execute("SELECT * FROM electrical WHERE call_id = ?", (call_id,)).fetchone()
    finally:
        conn.close()
    assert call["safety_state"] == "none" and call["safety_signal_at"] is None
    assert row["said_at"] is not None


def test_the_electrical_route_never_says_gas_or_co_lines(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    result = agent.turn(call_id, "There's smoke coming from the unit and it smells electrical.")
    assert result["say"] == ELECTRICAL
    assert GAS_LINES not in result["say"] and CO_LINES not in result["say"]
    assert "911" not in result["say"]


def test_gas_after_sparks_still_gets_the_gas_lines(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    agent.turn(call_id, "The furnace is sparking.")
    result = agent.turn(call_id, "Wait, now I smell gas too.")
    assert result["say"] == GAS_LINES
    assert result["data"]["safety"]["action"] == "speak_lines"


def test_sparks_and_gas_on_one_line_get_the_gas_lines_first(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    result = agent.turn(call_id, "I smell gas and the unit is sparking")
    assert result["say"] == GAS_LINES
    assert result["data"]["spoken"] is None


def test_an_electrical_smell_during_the_gas_confirm_question_is_said_once_it_clears(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    asked = agent.turn(call_id, "It smells like burning plastic from the furnace.")
    assert asked["data"]["safety"]["action"] == "ask_check"
    agent.say(call_id, asked["say"])
    cleared = agent.turn(call_id, "No, nothing like eggs.")
    assert cleared["say"] == ELECTRICAL


def test_burning_dust_on_the_first_heat_still_gets_only_the_confirm_question(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    result = agent.turn(call_id, "I turned the heat on and it smelled like burning dust.")
    assert result["data"]["safety"]["action"] == "ask_check"
    assert result["data"]["spoken"] is None


def decide(job_type: str, text: str = "", **facts) -> prio.Priority:
    electrical = safety.electrical_line(text, SAFETY) or ""
    return prio.decide(
        prio.Facts(job_type, text, electrical_line=electrical, **facts), CFG
    )


@pytest.mark.parametrize(
    "text",
    [
        "water is coming through the ceiling from the AC upstairs",
        "the AC is leaking into the bedroom",
        "it's dripping from the ceiling",
    ],
)
def test_water_into_the_home_is_urgent_even_on_a_mild_day(text):
    result = decide("no_cooling", text, outside_now_f=70)
    assert result.level == "Urgent"
    assert any("water coming into the home" in r for r in result.reasons)
    assert decide("tune_up", text).level == "Urgent"


def test_a_tripping_breaker_raises_one_level_to_high_not_urgent():
    assert decide("tune_up", "the breaker keeps tripping when the AC starts").level == "High"
    assert decide("tune_up", "my breaker tripped once in the storm").level == "Normal"


def test_sparks_raise_to_high_and_quote_the_caller():
    result = decide("tune_up", "the condenser was sparking this morning")
    assert result.level == "High"
    assert any('caller said "the condenser was sparking this morning"' in r for r in result.reasons)


def test_a_negated_or_old_spark_raises_nothing():
    assert decide("tune_up", "no sparks, it just hums").level == "Normal"
    assert decide("tune_up", "it sparked once last year and it's been fine").level == "Normal"
    assert decide("tune_up", "there's no water coming through the ceiling").level == "Normal"


@pytest.mark.parametrize(
    "job_type,text,safety_signal,level",
    [
        ("no_heat", "my 84-year-old mother lives with me", False, "Urgent"),
        ("no_heat", "we have a newborn baby at home", False, "Urgent"),
        ("tune_up", "", True, "Urgent"),
        ("no_heat", "I live alone and I use a walker", False, "Urgent"),
    ],
)
def test_gas_co_and_at_risk_callers_still_raise_as_before(job_type, text, safety_signal, level):
    assert decide(job_type, text, safety_signal=safety_signal).level == level


def test_a_booking_after_sparks_is_high_and_the_call_stays_off_the_safety_path(agent):
    call_id = ready_to_book(agent)
    agent.turn(call_id, "Also the outdoor unit was sparking when it kicked on.")
    slot = first_open(agent, call_id)
    booked = book(agent, call_id, slot, job_type="tune_up")
    assert booked["ok"], booked
    conn = agent.db()
    try:
        row = conn.execute("SELECT priority FROM bookings WHERE call_id = ?", (call_id,)).fetchone()
    finally:
        conn.close()
    assert row["priority"] == "High"


@pytest.mark.parametrize(
    "text",
    [
        "hi i've got water coming through my living room ceiling i think it's from the ac",
        "it's leaking, water's running down the wall and onto the carpet",
        "the carpet's getting soaked under the unit",
    ],
)
def test_water_in_the_callers_own_words_is_urgent(text):
    result = decide("no_cooling", text, outside_now_f=70)
    assert result.level == "Urgent"
    assert any("caller said" in r for r in result.reasons)


@pytest.mark.parametrize(
    "text",
    [
        "there's no water on the floor, it just won't cool",
        "no leaks on the floor or anything",
        "my water heater is on the basement floor",
        "the ac on the second floor is not cooling",
        "there's water. The unit in the bedroom won't turn on.",
        "there's a puddle on the kitchen floor from the sink",
    ],
)
def test_water_far_from_the_home_or_negated_raises_nothing(text):
    assert decide("no_cooling", text, outside_now_f=70).level == "Normal"


def test_the_signed_off_line_makes_the_visit_wait_on_nothing():
    assert SAFETY["electrical"]["signed_off"] is True
    assert ELECTRICAL.endswith("I can get a technician out to look at it.")
    assert "once" not in ELECTRICAL.lower()


def test_a_sparks_caller_can_book_right_after_the_line(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    said = agent.turn(call_id, "My AC is sparking, can you send someone?")
    assert said["say"] == ELECTRICAL
    assert "electrical_said" in said["data"]["hints"]
    agent.say(call_id, said["say"])
    # Nothing about the unit or the breaker being off: the booking goes through anyway.
    agent.confirm_read_back(call_id, in_area_address())
    booked = book(agent, call_id, first_open(agent, call_id), job_type="tune_up")
    assert booked["ok"], booked
    assert "shut it off" not in booked["say"]


@pytest.mark.parametrize(
    "text,level",
    [
        ("the kitchen faucet is dripping and the AC is broken", "Normal"),
        ("the water heater is leaking onto the floor", "Normal"),
        ("the AC is dripping water", "Urgent"),
        ("water leaking from the air handler", "Urgent"),
        ("the circuit breaker is tripping whenever the furnace starts", "High"),
        ("the breaker tripped when the AC kicked on", "High"),
        ("I keep resetting the breaker", "High"),
        ("my breaker tripped once in the storm", "Normal"),
        ("water is coming out of the vent", "Urgent"),
        ("the ceiling under the attic unit is wet", "Urgent"),
        ("there's a puddle under the indoor unit", "Urgent"),
        ("the condensate pan overflowed", "Urgent"),
        ("my breaker trips when the AC kicks on", "High"),
    ],
)
def test_the_review_cases_for_water_and_breakers(text, level):
    assert decide("no_cooling", text, outside_now_f=70).level == level


def test_the_two_caller_lines_after_the_electrical_lines_say_not_to_ask_about_the_breaker(agent):
    """Realism suite on #155, live-call-08-sparks-and-leak: the caller said "Yeah. Done." and was
    asked whether the breaker was off anyway."""
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    said = agent.turn(call_id, "I saw a spark from the AC unit, and there's water coming through.")
    agent.say(call_id, said["say"])
    first = agent.turn(call_id, "Yeah. Done.")
    assert "electrical_answered" in first["data"]["hints"]
    agent.say(call_id, "Thanks. What's the address?")
    second = agent.turn(call_id, "150 Greenpoint Avenue, Brooklyn.")
    assert "electrical_answered" in second["data"]["hints"]
    agent.say(call_id, "And the ZIP?")
    third = agent.turn(call_id, "11222.")
    assert "electrical_answered" not in third["data"]["hints"]


@pytest.mark.parametrize(
    "line",
    [
        "Hi, um, my outdoor AC unit just sparked and made a popping sound when it kicked on.",
        "The unit is sparking.",
        "I saw sparks come out of the furnace.",
        "There's a burning smell from the unit.",
        "There's a burning smell coming from the unit.",
        "There's smoke from the unit.",
    ],
)
def test_sparks_burning_smells_and_smoke_from_the_unit_fire(line):
    """Retell eval suite round 1 (ac-sparking-books, furnace-burning-plastic)."""
    assert safety.electrical_signal(line, SAFETY)
