"""The priority engine and the after-hours outcome (spec S-04, S-21, S-33; DECISIONS.md 006)."""

import pytest
import yaml
from toolkit import TENANT_DIR, UNKNOWN

from app import priority as prio

AGENT = yaml.safe_load((TENANT_DIR / "agent.yaml").read_text())
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

    def score(self, text):
        return self.value


def test_the_side_model_slot_is_off_by_default_and_can_only_raise():
    assert CFG["side_model"] is None
    # Off by default: a model that would score high changes nothing without the config slot.
    assert decide("tune_up", model=FakeSideModel(0.99)).level == "Normal"
    slot = {**CFG, "side_model": {"threshold": 0.8, "raise_to": "Urgent", "reason": "side model"}}
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
