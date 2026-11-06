"""Real-caller lines that must always bring the safety lines or raise priority, collected from the
reviews that caught regressions (PRs #102, #126, #132, #139). Every change to the safety or priority
rules runs these; a line here is only ever added, never relaxed without a decision entry."""

import pytest
from toolkit import TENANT_DIR

from app import priority as prio
from app import safety
from app.tenant import load_agent

AGENT = load_agent(TENANT_DIR)[0]
SAFETY = AGENT["safety"]
PRIORITY = AGENT["priority"]


@pytest.mark.parametrize(
    "line,kind",
    [
        ("I smell gas", "gas"),
        ("What do I do? I smell gas", "gas"),
        ("what should I do, I smell gas in the kitchen", "gas"),
        ("I reset the breaker and got nothing. Gas smell in the basement.", "gas"),
        ("Heat's out, thermostat shows nothing. Smells like gas down there.", "gas"),
        ("No. Gas smell in the basement.", "gas"),
        ("Never. Gas smell in the garage.", "gas"),
        ("I smell gas and saw a spark", "gas"),
        ("The c o alarm is going off", "co"),
        ("my c.o. detector keeps beeping", "co"),
        ("the see oh alarm went off", "co"),
        ("my carbon monoxide alarm keeps going off", "co"),
    ],
)
def test_a_real_gas_or_co_caller_always_hears_the_lines(line, kind):
    action = safety.check_caller_turn(line, "none", None, 0, SAFETY)
    assert action.action == "speak_lines", line
    assert action.kind == kind, line


@pytest.mark.parametrize(
    "answer",
    ["Yes, a little.", "No, but it does smell like rotten eggs.", "nothing but rotten eggs"],
)
def test_a_yes_to_the_gas_check_brings_the_lines(answer):
    action = safety.check_caller_turn(answer, "checking", "gas", 0, SAFETY)
    assert action.action == "speak_lines", answer


@pytest.mark.parametrize(
    "line",
    [
        "No heat, baby in the house",
        "No heat, newborn at home",
        "No heat at all, infant in the house",
        "No heat since last night, elderly mother lives with me",
        "No heat, oxygen concentrator running",
        "no kids, no pets, elderly mother lives here",
        "no heat, no hot water, elderly man upstairs",
        "Please tell them there's a baby in the house",
        "my 84-year-old mother lives with me",
    ],
)
def test_someone_at_risk_at_home_makes_no_heat_urgent(line):
    decided = prio.decide(prio.Facts("no_heat", line, None, caller_text=line), PRIORITY)
    assert decided.level == "Urgent", line


@pytest.mark.parametrize("line", ["I live alone and my heat is out", "I'm all alone and it's cold"])
def test_living_alone_raises_no_heat(line):
    decided = prio.decide(prio.Facts("no_heat", line, None, caller_text=line), PRIORITY)
    assert prio.LEVELS.index(decided.level) >= prio.LEVELS.index("High"), line


@pytest.mark.parametrize(
    "line",
    ["it's an emergency, put me first, I'll pay extra", "just mark it urgent"],
)
def test_insisting_alone_never_raises_a_tune_up(line):
    decided = prio.decide(prio.Facts("tune_up", line, None, caller_text=line), PRIORITY)
    assert decided.level == "Low", line


@pytest.mark.parametrize(
    "line",
    [
        "I saw a spark from the AC unit",
        "there was a spark from the outdoor unit",
        "smoke from the furnace",
        "burning wire smell",
        "sparks and a burning smell",
    ],
)
def test_sparks_or_an_electrical_smell_bring_the_electrical_line(line):
    assert safety.electrical_signal(line, SAFETY), line


@pytest.mark.parametrize(
    "line",
    ["no sparks", "it sparked once last year and it has been fine", "no smoke, no sparks",
     "smoke detector chirping"],
)
def test_a_negated_or_past_spark_and_a_smoke_detector_do_not(line):
    assert not safety.electrical_signal(line, SAFETY), line


@pytest.mark.parametrize(
    "line", ["water is coming through the ceiling under the AC", "the AC is dripping water"]
)
def test_ac_water_in_the_home_is_urgent(line):
    decided = prio.decide(prio.Facts("tune_up", line, None, caller_text=line), PRIORITY)
    assert decided.level == "Urgent", line


def test_a_breaker_that_keeps_tripping_raises_a_tune_up():
    line = "the breaker keeps tripping when the heat comes on"
    decided = prio.decide(prio.Facts("tune_up", line, None, caller_text=line), PRIORITY)
    assert prio.LEVELS.index(decided.level) >= prio.LEVELS.index("High")
