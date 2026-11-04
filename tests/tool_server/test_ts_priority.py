"""The priority engine and the after-hours outcome (spec S-04, S-21, S-33; DECISIONS.md 006)."""

import pytest
from toolkit import TENANT_DIR, UNKNOWN

from app import priority as prio
from app.tenant import load_agent

AGENT = load_agent(TENANT_DIR)[0]
CFG = AGENT["priority"]
CUTOFFS = AGENT["after_hours"]


def decide(job_type: str, text: str = "", temp: float | None = None, **kw) -> prio.Priority:
    return prio.decide(
        prio.Facts(job_type, text, temp, kw.get("safety", False)), CFG, kw.get("model")
    )


@pytest.mark.parametrize(
    "text,temp",
    [
        ("my 84-year-old mother lives with me", None),
        ("we have a newborn baby at home", None),
        ("my dad is on oxygen", None),
        ("", 52),
    ],
)
def test_no_heat_with_someone_vulnerable_or_a_cold_house_is_urgent_with_a_reason(text, temp):
    result = decide("no_heat", text, temp)
    assert result.level == "Urgent"
    assert result.reasons


@pytest.mark.parametrize(
    "text",
    [
        "I'm 84 and I use a cane",
        "my mother’s 91",
        "she is 78",
        "age 84",
        "my dad's in his 80s",
        "grandma is in her nineties",
    ],
)
def test_an_older_person_is_recognized_however_the_age_is_said(text):
    assert decide("no_heat", text).level == "Urgent"


@pytest.mark.parametrize("text", ["the boiler is 30 years old", "I'm 35", "it's 55 in here"])
def test_younger_ages_and_temperatures_are_not_ages_that_count(text):
    assert "vulnerable_person" not in decide("no_heat", text).rules


def test_no_heat_alone_and_a_defrost_or_lockout_story_stay_high():
    assert decide("no_heat").level == "High"
    story = "the heat pump is steaming outside and stopped, maybe it's defrosting; the furnace locked out"
    assert decide("no_heat", story, 64).level == "High"


def test_an_old_system_does_not_count_as_an_old_person():
    assert decide("no_heat", "the boiler is 25 years old").level == "High"


def test_rules_only_raise_priority():
    # A rule for no heat never lowers a tune-up, and a tune-up never jumps on vulnerable words.
    assert decide("tune_up", "my grandmother is visiting").level == "Normal"
    result = decide("safety_check", safety=True)
    assert result.level == "Urgent"
    for level in prio.LEVELS:
        cfg = {**CFG, "base": {"tune_up": level}}
        out = prio.decide(prio.Facts("tune_up", "elderly", 40), cfg)
        assert prio.LEVELS.index(out.level) >= prio.LEVELS.index(level)


class FakeSideModel:
    def __init__(self, score):
        self.value = score

    def score(self, question, text):
        self.asked = (question, text)
        return self.value


def test_the_side_model_slot_needs_its_config_and_can_only_raise():
    # Without the config slot, a model that would score high changes nothing.
    no_slot = {**CFG, "side_model": None}
    tune_up = prio.decide(prio.Facts("tune_up"), no_slot, FakeSideModel(0.99))
    assert tune_up.level == "Normal"
    slot = {**CFG, "side_model": {"question": "q", "threshold": 0.8, "raise_to": "Urgent",
                                  "reason": "side model"}}
    high = prio.decide(prio.Facts("tune_up"), slot, FakeSideModel(0.95))
    assert high.level == "Urgent" and "side_model" in high.rules
    low = prio.decide(prio.Facts("no_heat", "", 50), slot, FakeSideModel(0.0))
    assert low.level == "Urgent", "a low score never lowers what a rule raised"


def test_the_agent_asking_for_urgent_does_not_make_it_urgent(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I need my annual tune-up.")
    found = agent.tool("find_slots", call, job_type="tune_up", priority="Urgent")
    assert found["data"]["priority"] == {"level": "Normal", "reasons": [], "requested": "Urgent"}


def test_caller_lines_feed_priority_even_if_the_agent_leaves_them_out(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The heat's out and my grandmother is staying with us.")
    found = agent.tool("find_slots", call, job_type="no_heat")
    assert found["data"]["priority"]["level"] == "Urgent"


@pytest.mark.parametrize(
    "job_type,text,temp,falling,outcome",
    [
        ("no_heat", "", 47, False, "tonight_if_on_call_agrees"),
        ("no_heat", "", 58, True, "tonight_if_on_call_agrees"),
        ("no_heat", "an infant at home", 60, False, "tonight_if_on_call_agrees"),
        ("no_heat", "", 60, False, "first_morning_slot"),
        ("no_heat", "", 53, False, "callback"),
        ("no_heat", "", None, False, "callback"),
        ("no_cooling", "", 88, False, "tonight_if_on_call_agrees"),
        ("no_cooling", "", 76, False, "first_morning_slot"),
    ],
)
def test_after_hours_outcome_follows_the_tenant_cutoffs(job_type, text, temp, falling, outcome):
    facts = prio.Facts(job_type, text, temp)
    assert prio.after_hours_outcome(facts, prio.decide(facts, CFG), CUTOFFS, falling) == outcome


def test_find_slots_returns_the_after_hours_outcome_only_after_hours(agent, clock):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    daytime = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 60})
    assert "after_hours_outcome" not in daytime["data"]
    clock.advance(hours=13)  # 10 p.m. New York
    night = agent.start("(212) 555-0902")["data"]
    assert night["after_hours"] is True
    found = agent.tool(
        "find_slots", night["call_id"], job_type="no_heat", facts={"inside_temp_f": 60}
    )
    assert found["data"]["after_hours_outcome"] == "first_morning_slot"


def facts(job_type, text="", inside=None, now=None, coldest=None):
    return prio.Facts(job_type, text, inside, False, now, coldest if coldest is not None else now)


@pytest.mark.parametrize(
    "job_type,inside,outside,level",
    [
        ("no_heat", 50, 70, "Urgent"),  # below 55 inside, whatever the weather
        ("no_heat", 60, 70, "High"),  # 55 to 64 inside
        ("no_heat", 68, 40, "High"),  # warm inside, but cold out: the house keeps cooling
        ("no_heat", None, 40, "High"),
        ("no_heat", 68, 62, "Normal"),  # warm inside, mild day
        ("no_heat", None, 62, "Normal"),
        ("no_heat", 68, None, "High"),  # weather unknown fails safe
        ("no_cooling", 90, 70, "Urgent"),
        ("no_cooling", 80, 70, "High"),
        ("no_cooling", 75, 88, "High"),
        ("no_cooling", 75, 72, "Normal"),
        ("no_cooling", None, None, "High"),
    ],
)
def test_the_starting_level_follows_the_temperature_brackets(job_type, inside, outside, level):
    assert prio.decide(facts(job_type, inside=inside, now=outside), CFG).level == level


def test_tonights_low_counts_for_no_heat():
    mild_now_cold_tonight = facts("no_heat", inside=68, now=60, coldest=45)
    assert prio.decide(mild_now_cold_tonight, CFG).level == "High"


def test_an_older_person_on_a_mild_day_is_high_not_urgent():
    result = prio.decide(facts("no_heat", "my 84-year-old mother", inside=68, now=64), CFG)
    assert result.level == "High" and "vulnerable_person" in result.rules


@pytest.mark.parametrize(
    "text,level",
    [
        ("she lives alone", "High"),
        ("my husband has COPD and he lives alone", "Urgent"),  # two risk factors together
        ("I'm pregnant", "High"),
        ("my son is in a wheelchair and has diabetes", "Urgent"),
        ("we've been using the oven to stay warm", "Urgent"),
        ("my dad's bedridden", "Urgent"),
        ("the pipes froze last night", "High"),
    ],
)
def test_risk_groups_raise_priority_on_a_cold_day(text, level):
    assert prio.decide(facts("no_heat", text, inside=66, now=45), CFG).level == level


def test_two_risk_factors_on_a_mild_day_stay_below_urgent():
    result = prio.decide(facts("no_heat", "she lives alone and has COPD", inside=68, now=64), CFG)
    assert result.level == "High"


def test_find_slots_reads_the_stored_outside_temperature(agent, clock):
    conn = agent.db()
    with conn:
        conn.execute(
            "INSERT INTO weather (tenant, fetched_at, now_f, low_f) VALUES ('summit-air', ?, 64, 60)",
            (clock().isoformat(),),
        )
    conn.close()
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The heat's not coming on, but it's 68 in here.")
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 68})
    assert found["data"]["priority"]["level"] == "Normal"


def test_an_urgent_caller_hears_it_once_and_a_routine_caller_never_does(agent):
    line = "marked this call top priority"
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "No heat, and my 84-year-old mother lives with me.")
    first = agent.tool("find_slots", call, job_type="no_heat")
    assert line in first["say"]
    agent.say(call, first["say"])
    again = agent.tool("find_slots", call, job_type="no_heat")
    assert line not in again["say"]

    routine = agent.start("(212) 555-0177")["data"]["call_id"]
    agent.turn(routine, "I'd like my annual tune-up.")
    assert line not in agent.tool("find_slots", routine, job_type="tune_up")["say"]


@pytest.mark.parametrize("text,urgent", [("my 61-year-old dad lives here", True), ("I'm 59", False)])
def test_older_adult_starts_at_60_in_line_with_nyc_health(text, urgent):
    result = decide("no_heat", text, None)
    assert (result.level == "Urgent") is urgent


@pytest.mark.parametrize(
    "text",
    [
        "we're both healthy no babies or older people here and nobody on any medical equipment",
        "We don't have a baby.",
        "There's no one elderly here, nobody on oxygen.",
        "two healthy adults no babies no older people nothing like medical equipment",
        "we're both healthy nobody's elderly or sick or anything",
    ],
)
def test_a_person_said_not_to_be_there_raises_nothing(text):
    """Eval round 2, seed 9: "nobody on any medical equipment" made a mild call urgent. The pair
    eval two-callers-last-slot: "nobody's elderly" did too, and took a technician held for urgent
    jobs."""
    result = decide("no_heat", text, 58)
    assert [r for r in result.rules if not r.startswith("bracket:")] == []


@pytest.mark.parametrize(
    "text",
    [
        "no heat and grandma is staying with us",
        "No, my grandmother lives with us.",
        "no my grandmother lives with us",
        "my dad's oxygen machine is not working",
        "not sure, but there's a baby here",
    ],
)
def test_a_negation_elsewhere_in_the_line_never_drops_a_raise(text):
    """A missed raise is worse than an extra one, so the look back stops at a new thought."""
    assert [r for r in decide("no_heat", text, 58).rules if not r.startswith("bracket:")]
