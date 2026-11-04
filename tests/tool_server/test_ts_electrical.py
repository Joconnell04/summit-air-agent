"""The electrical route and water and breaker priority (DECISIONS.md 082): sparks get their own calm
lines, said once and never the gas or carbon monoxide ones; water through the ceiling is Urgent; a
breaker that keeps tripping is High. Gas, carbon monoxide and at-risk callers raise and fire exactly
as before."""

import pytest
from toolkit import TENANT_DIR, book, first_open, ready_to_book

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
