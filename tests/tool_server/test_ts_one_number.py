"""Five scripted calls from one number, one after another, as different people (Retell calls
call_feed25cb0e5df1017695f9dc210, call_ad292013eb2db2c6323a003ecf9, call_de4fe39a91aee4db1c3e61a611f,
call_79af0414dc77fcabc47b36f49cd, call_f0846787038f654309219adb1e8)."""

import pytest
from test_ts_safety import CO_LINES
from toolkit import TENANT_DIR, UNKNOWN, book, first_open, in_area_address, ready_to_book

from app.tenant import load_agent

AGENT_CFG = load_agent(TENANT_DIR)[0]
ELECTRICAL = " ".join(AGENT_CFG["safety"]["electrical"]["lines"].split())


def test_two_calls_from_one_number_share_nothing(agent, clock):
    """A tune-up after a sparks call heard the sparks line and its priority reason, and the next
    call's booking was refused as if the tune-up's counted against it. Jackson: "gross"."""
    sparks = ready_to_book(agent)
    agent.turn(sparks, "Hey, yeah, there are sparks coming from the unit.")
    booked = book(agent, sparks, first_open(agent, sparks, "no_heat"), job_type="no_heat",
                  name="Pat Rivera")
    assert booked["ok"], booked
    agent.end(sparks, "caller_hangup")
    clock.advance(minutes=1)

    tune_up = ready_to_book(agent, address=in_area_address(2))
    start = agent.db().execute(
        "SELECT linked_call_id FROM calls WHERE id = ?", (tune_up,)
    ).fetchone()[0]
    assert start is None
    turn = agent.turn(tune_up, "I just need a boiler tune-up.")
    assert turn["say"] != ELECTRICAL
    found = agent.tool("find_slots", tune_up, job_type="tune_up")
    assert not any("sparks" in r or "electrical" in r for r in found["data"]["priority"]["reasons"])
    assert "spoken_first" not in found["data"]
    # A different person on the same number: their own account, as the caller would answer.
    again = book(agent, tune_up, first_open(agent, tune_up), name="Sam Lee", account="own")
    assert again["ok"], again
    agent.end(tune_up, "caller_hangup")
    clock.advance(minutes=1)

    third = ready_to_book(agent, address=in_area_address(3))
    # Two accounts are on the number now, so the server asks whose name; never a booking limit
    # carried from the calls before.
    last = book(agent, third, first_open(agent, third), name="Jo Park", account="own")
    assert last.get("refused") != "limit", last


@pytest.mark.parametrize("said", [
    "Yeah, my newborn's here and it's really cold.",
    "We have an infant at home.",
    "My baby's in the house.",
])
def test_a_newborn_or_a_baby_raises_the_priority(agent, said):
    call = ready_to_book(agent)
    agent.turn(call, said)
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"who_home": "baby"})
    assert found["data"]["priority"]["level"] in ("High", "Urgent"), found["data"]["priority"]


def test_really_cold_is_never_called_warm_inside(agent):
    """A comfort complaint alone raises nothing (test_ts_priority.py); it just isn't "warm"."""
    call = ready_to_book(agent)
    agent.turn(call, "It's really cold in here.")
    found = agent.tool("find_slots", call, job_type="no_heat")
    assert not any("warm inside" in r for r in found["data"]["priority"]["reasons"])


@pytest.mark.parametrize("args", [
    {"callback_after": "2 PM"},
    {"callback_after": "5"},
    {"callback_before": "3 PM"},
])
def test_a_callback_window_that_has_passed_is_the_soonest_callback(agent, clock, args):
    """"after 2 PM" and "anytime after 5", said at 7:30 PM, were refused with "What time should I
    tell them someone will call by?", and the model ended the call with no callback."""
    clock.advance(hours=10, minutes=30)  # 7:30 PM in New York
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Can someone call me back after 2?")
    flagged = agent.tool("flag_person", call, reason="Wants a call about pricing", **args)
    assert flagged["ok"], flagged
    assert flagged["data"]["callback_id"] is not None
    assert "What time" not in flagged["say"]


def test_a_bare_time_that_has_passed_is_that_time_tomorrow(agent, clock):
    clock.advance(hours=10, minutes=30)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Call me at 2.")
    flagged = agent.tool("flag_person", call, reason="Wants a call", callback_time="2 PM")
    assert flagged["ok"], flagged
    assert flagged["say"].endswith("tomorrow around 2 PM.") or "tomorrow" in flagged["say"]
    # Said at 7:30 PM on Monday, November 2 in New York: 2 PM Tuesday.
    assert agent.db().execute("SELECT due_at FROM callbacks").fetchone()[0] == "2026-11-03T19:00:00Z"


def test_a_long_flag_reason_is_cut_not_refused(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    flagged = agent.tool("flag_person", call, reason="CO alarm went off, then " * 40,
                         callback_in_minutes=30)
    assert flagged["ok"], flagged
    reason = agent.db().execute("SELECT reason FROM flags").fetchone()[0]
    assert 300 < len(reason) <= 600


def test_a_safety_call_hangs_up_with_no_second_goodbye(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My carbon monoxide alarm is going off.")
    agent.say(call, CO_LINES)
    agent.say(call, "Thanks for calling, and please stay safe. We'll follow up with you.")
    ended = agent.tool("end_call", call, reason="resolved")
    assert ended["ok"] and ended["say"] == "" and ended["data"]["hang_up"] is True


def test_the_sparks_line_is_said_once_even_after_a_flag(agent):
    call = ready_to_book(agent)
    first = agent.turn(call, "There are sparks coming from the furnace.")
    assert first["say"] == ELECTRICAL
    agent.say(call, first["say"])
    flagged = agent.tool("flag_person", call, reason="Sparks at the furnace", callback_in_minutes=30)
    assert ELECTRICAL not in flagged["say"]
    again = agent.turn(call, "It's still sparking a bit.")
    assert again["say"] != ELECTRICAL


def test_a_whole_day_offers_two_windows_not_all_of_them(agent):
    """Saturday after 9 read out all 16 half-hour windows."""
    call = ready_to_book(agent)
    found = agent.tool("find_slots", call, job_type="tune_up", day="saturday")
    assert found["ok"], found
    assert len(found["data"]["windows"]) > 2
    assert found["say"].count(" to ") == 2 and found["say"].count(" or ") == 1, found["say"]


def test_the_commercial_lead_line_says_the_details_are_passed_on(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I run a deli and the walk-in cooler is warm.")
    lead = agent.tool("record_lead", call, kind="commercial",
                      details={"contact_name": "Sam", "company": "Sam's Deli", "need": "cooler"})
    assert lead["say"] == (
        "Thanks for thinking of us. Unfortunately, we only service homes, but I'll pass your "
        "details to our team."
    )


def test_a_sparks_call_asks_the_gas_check_once(agent):
    """Retell call call_b608687ce923e70a8318a6a6f0e: the confirm question, then the plain gas
    question while the caller was still answering "no, it's not like rotten eggs", then sparks.
    A burning smell from the furnace now gets the sparks advice first, and no gas question."""
    confirm = AGENT_CFG["safety"]["vague"]["gas"]["confirm_question"]
    plain = AGENT_CFG["safety"]["vague"]["gas"]["question"]
    call = ready_to_book(agent)
    first = agent.turn(call, "There's a burning smell coming from the furnace.")
    assert first["say"] == ELECTRICAL
    agent.say(call, first["say"])
    said = []
    for line in ["no,", "No, it's not like rotten eggs, it's more like burning plastic.",
                 "And I saw some sparks by the switch."]:
        result = agent.turn(call, line)
        if result["say"]:
            said.append(result["say"])
            agent.say(call, result["say"])
    assert plain not in " ".join(said) and confirm not in " ".join(said)
    assert ELECTRICAL not in " ".join(said), "the sparks advice was said once, first"


def test_a_caller_line_sent_again_whole_replaces_its_part(agent):
    """The bridge posted "One", "$199.", "East", "East 4th, apartment 2." as four caller lines
    (Retell call call_feed25cb0e5df1017695f9dc210); the rewritten ones now replace their part."""
    call = agent.start(UNKNOWN)["data"]["call_id"]

    def caller(text, replaces=False):
        body = {"speaker": "caller", "text": text, "timings": {}, "replaces_last": replaces}
        assert agent.client.post(f"/calls/{call}/turn", json=body).status_code == 200

    agent.say(call, "What's the street address for the visit?")
    caller("One")
    caller("$199.", replaces=True)
    agent.say(call, "Got it, and what street is that on?")
    caller("East")
    caller("East 4th, apartment 2.", replaces=True)
    lines = [r[0] for r in agent.db().execute(
        "SELECT text FROM turns WHERE call_id = ? AND speaker = 'caller' ORDER BY seq", (call,)
    )]
    assert lines == ["$199.", "East 4th, apartment 2."]


def test_refusals_dont_narrate_the_agents_steps(agent):
    """The side-by-side heard "Let me check that again." and "Before I book it, let me read back
    the address and number." from refusals."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    bad = agent.tool("add_note", call, text="")
    assert bad["refused"] == "invalid_arguments" and bad["say"] == ""
    early = book(agent, call, {"date": "2026-11-03", "window": "0900-0930"}, name="Pat Rivera")
    assert early["refused"] == "gate_read_back" and early["say"] == ""


@pytest.mark.parametrize("tool", ["flag_person", "record_lead"])
def test_the_callback_day_the_caller_named_wins_over_the_models(agent, tool):
    """On every Haiku arm, "Monday morning" went in as callback_day "tomorrow" and was confirmed
    as "tomorrow before 12 PM" (Retell calls call_5b998cf4ab209eb48c2ae5e9a0b and
    call_fc470f8ddd2d56cf55be68fc119). The clock is Monday, November 2, 9 AM."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Could someone call me back Thursday morning about a new furnace?")
    window = {"callback_day": "tomorrow", "callback_before": "12 PM"}
    if tool == "flag_person":
        result = agent.tool(tool, call, reason="Wants a call about a new furnace", **window)
    else:
        result = agent.tool(tool, call, kind="install", details={
            "contact_name": "Pat Rivera", "need": "a new furnace", **window})
    assert result["ok"], result
    assert "Thursday" in result["say"] and "tomorrow" not in result["say"], result["say"]
    due = agent.db().execute("SELECT due_at FROM callbacks").fetchone()[0]
    assert due.startswith("2026-11-05"), due


def test_the_part_of_day_the_caller_named_wins_over_the_models(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Tomorrow afternoon works for a call back.")
    result = agent.tool("flag_person", call, reason="Call back", callback_day="tomorrow",
                        callback_before="11 AM")
    assert result["ok"], result
    assert "tomorrow" in result["say"] and "11 AM" not in result["say"], result["say"]


def _co_lines():
    return " ".join(AGENT_CFG["safety"]["kinds"]["co"]["lines"].split())


def test_cut_off_advice_isnt_said_again_when_the_caller_is_already_safe(agent):
    """Retell call call_72009cbe16703210509127925b0: only "If a CO" was spoken before the caller
    cut in, but they were outside and the fire department had been: no second advice."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Hi, my carbon monoxide alarm is going off.")
    agent.say(call, "If a")
    first = agent.turn(call, "I")
    assert first["say"] == "", "a partial line waits"
    agent.say(call, "f a CO")
    answer = agent.turn(call, (
        "opened the window, Sorry, I opened the windows and we're all outside now. The fire "
        "department came and said it"
    ))
    assert _co_lines() not in answer["say"]
    later = agent.turn(call, "Yeah, can someone still come check the furnace?")
    assert _co_lines() not in later["say"]


def test_cut_off_advice_is_said_again_once(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Hi, my carbon monoxide alarm is going off.")
    agent.say(call, "If a CO")
    again = agent.turn(call, "Sorry, what do I do about the furnace though?")
    assert again["say"] == _co_lines()
    assert again["data"]["spoken"] == {"kind": "co", "text": _co_lines()}
    agent.say(call, again["say"][:20])
    third = agent.turn(call, "Okay, and can someone come out today?")
    assert _co_lines() not in third["say"], "once a hazard"


def test_advice_said_in_full_isnt_said_again(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Hi, my carbon monoxide alarm is going off.")
    agent.say(call, _co_lines())
    assert agent.turn(call, "Okay, what about the furnace though?")["say"] == ""


def test_cut_off_sparks_advice_is_said_again_once(agent):
    call = ready_to_book(agent)
    first = agent.turn(call, "There are sparks coming from the furnace.")
    assert first["say"] == ELECTRICAL
    agent.say(call, "Please step")
    again = agent.turn(call, "Sorry, what was that about the furnace?")
    assert again["say"] == ELECTRICAL


@pytest.mark.parametrize("opener", [
    "There's a burning smell and sparks coming from the furnace.",
    "There are sparks and smoke coming from the unit, and a burning smell.",
])
def test_the_sparks_advice_is_the_first_thing_said_on_a_sparks_opener(agent, opener):
    """The side-by-side measured 21 to 38 seconds from "sparks" to the first instruction on every
    arm, because the gas check came first; gas and carbon monoxide took about 3."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    first = agent.turn(call, opener)
    assert first["say"].startswith(ELECTRICAL), first["say"]
    confirm = AGENT_CFG["safety"]["vague"]["gas"]["confirm_question"]
    assert confirm not in first["say"]
    agent.say(call, first["say"])
    nxt = agent.turn(call, "Okay, I turned it off.")
    assert ELECTRICAL not in nxt["say"] and confirm not in nxt["say"]


def test_sparks_with_a_smell_that_leaves_gas_possible_ask_the_gas_check_after(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    first = agent.turn(call, "There are sparks by the furnace and a weird smell in the basement.")
    question = AGENT_CFG["safety"]["vague"]["gas"]["question"]
    assert first["say"] == f"{ELECTRICAL} {question}"
