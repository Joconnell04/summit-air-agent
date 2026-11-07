"""The priority engine and the after-hours outcome (spec S-04, S-21, S-33; DECISIONS.md 006)."""

import pytest
from toolkit import START, TENANT_DIR, UNKNOWN, book, in_area_address, ready_to_book

from app import priority as prio
from app.tenant import load_agent
from prompts.assemble import SpokenReply, for_model, spoken_by_server, spoken_first

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
    assert decide("tune_up", "my grandmother is visiting").level == "Low"
    result = decide("safety_check", safety=True)
    assert result.level == "High"
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
    assert tune_up.level == "Low"
    slot = {**CFG, "side_model": {"question": "q", "threshold": 0.8, "raise_to": "Urgent",
                                  "reason": "side model"}}
    high = prio.decide(prio.Facts("tune_up"), slot, FakeSideModel(0.95))
    assert high.level == "Urgent" and "side_model" in high.rules
    low = prio.decide(prio.Facts("no_heat", "", 50), slot, FakeSideModel(0.0))
    assert low.level == "Urgent", "a low score never lowers what a rule raised"


def test_the_tenant_side_model_needs_065_not_050_to_raise():
    # DECISIONS.md 090: 0.63 was the one false alarm on the labeled lines; true yeses scored 0.78+.
    assert CFG["side_model"]["threshold"] == 0.65
    mild = prio.Facts("no_heat", "", 64, caller_text="my daughter just turned five")
    assert "side_model" not in prio.decide(mild, CFG, FakeSideModel(0.63)).rules
    assert "side_model" in prio.decide(mild, CFG, FakeSideModel(0.78)).rules


def test_the_agent_asking_for_urgent_does_not_make_it_urgent(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I need my annual tune-up.")
    found = agent.tool("find_slots", call, job_type="tune_up", priority="Urgent")
    assert found["data"]["priority"] == {
        "level": "Low",
        "reasons": [],
        "requested": "Urgent",
        "raised_after_wait": False,
    }


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
    # The inside temperature counts only once the caller says it (DECISIONS.md 072).
    agent.turn(night["call_id"], "The heat's out, it's about 60 in here.")
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
    # November: no heat in the heating season is at least High whatever the temperatures
    # (DECISIONS.md 130).
    assert found["data"]["priority"]["level"] == "High"


def test_an_urgent_caller_hears_it_once_and_a_routine_caller_never_does(agent):
    line = "marked this call top priority"
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "No heat, and my 84-year-old mother lives with me.")
    first = agent.tool("find_slots", call, job_type="no_heat")
    # Spoken by the voice layer word for word before the times, never left for the model to say;
    # the record's `say` keeps it, and the model is shown the rest.
    assert line in first["data"]["spoken_first"] and first["say"].startswith(
        first["data"]["spoken_first"]
    )
    assert line not in for_model(first)["say"] and for_model(first)["say"]
    agent.say(call, first["say"])
    again = agent.tool("find_slots", call, job_type="no_heat")
    assert "spoken_first" not in again["data"]

    routine = agent.start("(212) 555-0177")["data"]["call_id"]
    agent.turn(routine, "I'd like my annual tune-up.")
    assert "spoken_first" not in agent.tool("find_slots", routine, job_type="tune_up")["data"]


def test_an_urgent_booking_made_without_find_slots_opens_with_the_line(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "No heat, and my 84-year-old mother lives with me.")
    agent.confirm_read_back(call, in_area_address())
    slot = {"date": START.isoformat(), "window": "1200-1600"}
    booked = book(agent, call, slot, job_type="no_heat")
    assert booked["ok"], booked
    assert booked["say"].startswith("I've marked this call top priority")


def test_the_voice_layer_speaks_the_line_and_drops_the_models_own():
    reply = SpokenReply()
    envelope = {"ok": True, "say": "I've got openings this afternoon.",
                "data": {"priority": {"level": "Urgent"},
                         "spoken_first": "I've marked this call top priority, so you get our "
                                         "soonest opening."}}
    assert spoken_first(envelope).startswith("I've marked this call top priority")
    assert spoken_by_server("find_slots", envelope) is None  # the model's reply still follows
    reply.tool_done("find_slots", envelope)
    assert reply.sentence("I've marked this as top priority for you.") == ""
    assert reply.sentence("I have this afternoon from 12 to 4.") == "I have this afternoon from 12 to 4."


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


@pytest.mark.parametrize("text", ["nobody's elderly here", "nobody's a baby, just me and my wife", "no one's elderly"])
def test_nobodys_negates_a_risk_word(text):
    assert decide("no_heat", text, 66).level != "Urgent"


def test_nobody_whos_a_baby_or_elderly_raises_nothing_but_a_real_mention_still_does():
    """Eval run 20261104-052332, mild-after-hours-no-heat: "elderly" sat five words after
    "nobody" and sent a mild night to the on-call technician."""
    from app.priority import _said_not_negated
    from app.text import words

    said = "just two adults both healthy nobody whos a baby or elderly nobody on any medical equipment"
    assert not _said_not_negated(words(said), "elderly")
    assert _said_not_negated(words("no heat since this morning grandma is here"), "grandma")


@pytest.mark.parametrize(
    "who_home",
    [
        "It's just me at home. I'm 84, and I use a cane to get around.",
        "its just me at home im 84 and i use a cane",
        "just me at home, I use a cane",
    ],
)
def test_an_older_caller_alone_with_a_cane_is_urgent(agent, who_home):
    """A rig call (#98): an 84-year-old alone with a cane was booked High while told it was
    urgent."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat isn't working, it's about 55 in here.")
    agent.turn(call, who_home)
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 56})
    assert found["data"]["priority"]["level"] == "Urgent"


def test_the_booking_keeps_the_priority_find_slots_already_gave(agent):
    """The rig call's find_slots facts said "elderly"; the booking's details didn't, and it was
    booked one level lower than the caller was told. Priority now comes from the caller's own lines
    (DECISIONS.md 072), which the booking reads as well, and from the inside temperature the caller
    gave."""
    call = ready_to_book(agent)
    agent.turn(call, "It's my elderly mother's place, about 60 degrees in there.")
    found = agent.tool("find_slots", call, job_type="no_heat",
                       facts={"who_home": "elderly woman, cold but managing", "inside_temp_f": 60})
    assert found["data"]["priority"]["level"] == "Urgent"
    day = found["data"]["days"][0]
    booked = book(agent, call, {"date": day["date"], "window": day["windows"][0]},
                  job_type="no_heat", who_home="Caller, cold but managing", inside_temp_f=None)
    assert booked["ok"], booked
    assert booked["data"]["priority"]["level"] == "Urgent"


def test_a_negated_list_goes_on_past_a_comma_but_a_bare_no_does_not():
    """Eval run 20261104-154616, mild-after-hours-no-heat: "no babies, older people or medical
    equipment" raised medical_equipment and sent a mild night to the on-call path."""
    from app import priority as prio

    listed = prio.Facts("no_heat", "Two healthy adults, no babies, older people or medical equipment")
    assert not prio._rule_fires({"phrases": ["medical equipment"]}, listed)
    answer = prio.Facts("no_heat", "No, my mother lives with us and she's on oxygen")
    assert prio._rule_fires({"phrases": ["oxygen"]}, answer)
    later = prio.Facts("no_heat", "No babies here, but grandma is visiting, she's elderly")
    assert prio._rule_fires({"phrases": ["elderly"]}, later)


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
    ],
)
def test_a_no_before_a_comma_never_hides_someone_at_risk(agent, line):
    """PR #102 review: the comma carry dropped each of these from Urgent to High at 66 F."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, line)
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 66})
    assert found["data"]["priority"]["level"] == "Urgent", line


@pytest.mark.parametrize(
    "text",
    [
        "my husband had a stroke last year",
        "she's recovering from a stroke",
        "my mother-in-law moved in after her stroke",
        "my husband and my mother in law who moved in after her stroke",
    ],
)
def test_a_stroke_counts_as_a_chronic_illness(text):
    """stroke-in-passing-no-heat came out High twice: a stroke read as no risk at all."""
    # The reason may go on to quote the caller (DECISIONS.md 072).
    reasons = decide("no_heat", text, 61).reasons
    assert any(r.startswith("a chronic illness at home") for r in reasons)


def test_a_spouses_parent_after_a_stroke_is_urgent():
    """The caller said "his mother's here too, she moved in after her stroke" and it booked High
    (eval run 20261104-172529, stroke-in-passing-no-heat#3)."""
    text = "its me and my husband and his mothers here too she moved in with us after her stroke"
    assert decide("no_heat", text, 61).level == "Urgent"


def test_the_stroke_in_passing_call_is_urgent_like_other_at_risk_callers():
    stroke = decide("no_heat", "my husband and my mother-in-law, who moved in after her stroke", 61)
    heart = decide("no_heat", "my husband and my mother-in-law, she has a heart condition", 61)
    assert stroke.level == heart.level == "Urgent"


@pytest.mark.parametrize(
    "text", ["it was a stroke of luck we caught it early", "a real stroke of genius, that thermostat"]
)
def test_a_stroke_of_luck_is_not_an_illness(text):
    result = decide("no_heat", text, 61)
    assert "a chronic illness at home" not in result.reasons
    assert result.level == "High"



def test_two_find_slots_in_one_turn_hand_out_the_line_once(agent):
    """PR #139 review: "once" was read from the transcript, written only after the turn."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "No heat, and my 84-year-old mother lives with me.")
    first = agent.tool("find_slots", call, job_type="no_heat")
    second = agent.tool("find_slots", call, job_type="no_heat", time_of_day="morning")
    assert "spoken_first" in first["data"]
    assert "spoken_first" not in second["data"] and "top priority" not in second["say"]


def test_the_line_waits_for_the_safety_lines(agent):
    """PR #139 review: it could play before the safety lines."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas, and the heat is out.")
    held = agent.tool("find_slots", call, job_type="no_heat")
    assert "spoken_first" not in held["data"]


@pytest.mark.parametrize(
    "job_type,text,inside,now",
    [
        ("no_heat", "it's chilly in here", 68, 64),
        ("no_heat", "I'm freezing, it's so cold", 68, 64),
        ("no_cooling", "I'm sweating, it's really hot", 75, 70),
        ("no_cooling", "it's so hot I can't sleep", 75, 70),
    ],
)
def test_a_comfort_complaint_alone_raises_nothing(job_type, text, inside, now):
    """Jackson: the caller hears they were moved up only when what they said raised them; being
    uncomfortable isn't a reason."""
    result = prio.decide(facts(job_type, text, inside=inside, now=now), CFG)
    assert result.level == "Normal" and not result.raised_by_caller


def test_the_side_models_question_says_comfort_alone_is_no():
    import yaml

    from app.side_model import QUESTIONS_PATH

    question = yaml.safe_load(QUESTIONS_PATH.read_text())["vulnerable_person"]
    assert "only being uncomfortable, hot or cold" in question["instructions"]
    assert "only being uncomfortable, hot or cold" in question["system"]


def test_a_caller_moved_up_below_urgent_hears_it_once(agent, clock):
    """Jackson: tell the caller whenever their own words raised the priority ("it makes them feel
    heard"). On a mild day an 84-year-old at home is High, not Urgent."""
    conn = agent.db()
    with conn:
        conn.execute(
            "INSERT INTO weather (tenant, fetched_at, now_f, low_f) VALUES ('summit-air', ?, 64, 60)",
            (clock().isoformat(),),
        )
    conn.close()
    call = agent.start(UNKNOWN)["data"]["call_id"]
    # No cooling in November, out of the cooling season: a mild day starts Normal, so an older
    # person at home moves it up to High, not Urgent.
    agent.turn(call, "The AC's out, it's 72 in here, and my 84-year-old mother lives with me.")
    first = agent.tool("find_slots", call, job_type="no_cooling", facts={"inside_temp_f": 72})
    assert first["data"]["priority"]["level"] == "High"
    line = "Thanks for telling me. I've moved you up so you'll get one of our earlier openings."
    assert first["data"]["spoken_first"] == line and first["say"].startswith(line)
    agent.say(call, first["say"])
    again = agent.tool("find_slots", call, job_type="no_cooling", facts={"inside_temp_f": 72})
    assert "spoken_first" not in again["data"]


def test_a_caller_who_starts_high_from_the_temperature_hears_nothing_extra(agent):
    """60 F inside is High from the temperature alone: nothing the caller said moved them up."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "No heat, it's 60 in here.")
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 60})
    assert found["data"]["priority"]["level"] == "High"
    assert "spoken_first" not in found["data"]


def test_no_heat_in_the_heating_season_is_at_least_high_and_out_of_it_follows_the_weather():
    """DECISIONS.md 130: October 1 to May 31 is the heating season, June 15 to September 15 the
    cooling season."""
    mild = {"outside_now_f": 70, "outside_coldest_f": 68}
    out = prio.decide(prio.Facts("no_heat", "it's not keeping up", 68, **mild), CFG)
    assert out.level == "Normal"
    inside = prio.decide(prio.Facts("no_heat", "it's not keeping up", 68, in_season=True, **mild), CFG)
    assert inside.level == "High"


def test_a_described_fault_is_high_and_planned_work_is_low():
    mild = {"outside_now_f": 70, "outside_coldest_f": 68}
    assert prio.decide(prio.Facts("no_cooling", "it's completely out", 75, **mild), CFG).level == "High"
    assert prio.decide(prio.Facts("no_cooling", "it's not keeping up", 75, **mild), CFG).level == "Normal"
    assert prio.decide(prio.Facts("tune_up", "time for my yearly check"), CFG).level == "Low"


def test_asking_for_someone_right_away_after_the_offer_is_urgent():
    cfg = CFG["right_away"]
    offer = "Can this wait for a scheduled visit, or do you need someone out right away?"
    assert prio.asked_right_away([("agent", offer), ("caller", "I need someone right away.")], cfg)
    assert not prio.asked_right_away([("agent", offer), ("caller", "It can wait.")], cfg)
    # Without the offer, insisting alone raises nothing.
    assert not prio.asked_right_away([("caller", "I need someone right away.")], cfg)
    raised = prio.decide(prio.Facts("no_heat", "x", 68, in_season=True, right_away=True), CFG)
    assert raised.level == "Urgent"


def test_a_high_call_asks_whether_it_can_wait_then_dispatches_a_yes(agent):
    call = ready_to_book(agent)
    asked = agent.tool("find_slots", call, job_type="no_heat")
    assert asked["data"]["asked_dispatch"]
    assert asked["say"] == "Can this wait for a scheduled visit, or do you need someone out right away?"
    agent.say(call, asked["say"])
    agent.turn(call, "No, I need someone out right away.")
    sent = agent.tool("find_slots", call, job_type="no_heat")
    assert sent["data"].get("dispatched"), sent


@pytest.mark.parametrize("job_type", ["no_heat", "no_cooling"])
@pytest.mark.parametrize("who", ["my 84-year-old mother lives with me", "my grandma is here",
                                 "we have a newborn baby"])
def test_an_older_person_or_a_baby_in_season_is_urgent_even_on_a_mild_day(job_type, who):
    """The assignment's urgent cases: no heat in winter or no AC in summer with an older person or
    a baby at home. In season the call is never treated as mild, so the household rule raises it to
    Urgent whatever the weather that day."""
    mild = {"outside_now_f": 70, "outside_coldest_f": 68}
    result = prio.decide(prio.Facts(job_type, who, 72, in_season=True, caller_text=who, **mild), CFG)
    assert result.level == "Urgent", result.reasons
