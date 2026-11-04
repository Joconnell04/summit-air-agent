"""Speaking rate per call (app/pace.py, DECISIONS.md 053): the server moves the pace from the
caller's lines and records it; the worker only applies what it is given."""

import pytest
from toolkit import TENANT_DIR, UNKNOWN, in_area_address

from app import pace
from app.tenant import load_agent

CFG = load_agent(TENANT_DIR)[0]["pace"]


def speed(result: dict) -> float:
    return result["data"]["pace"]["speed"]


def changes(agent, call_id: str) -> list[tuple]:
    conn = agent.db()
    rows = conn.execute(
        "SELECT seq, speed, scope, source FROM pace_changes WHERE call_id = ? ORDER BY id",
        (call_id,),
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def remembered(agent, number: str = UNKNOWN) -> float | None:
    conn = agent.db()
    found = pace.remembered(conn, "summit-air", "".join(c for c in number if c.isdigit())[-10:])
    conn.close()
    return found


# ---- reading a line ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, asked",
    [
        ("Can you slow down a little? I'm writing this down.", pace.SLOWER),
        ("Whoa, you're talking too fast.", pace.SLOWER),
        ("Slower please.", pace.SLOWER),
        ("You can talk faster, I'm in a rush.", pace.FASTER),
        ("You're talking so slow.", pace.FASTER),
        ("Can you go back to normal speed?", pace.NORMAL),
    ],
)
def test_a_caller_asking_for_a_pace_is_heard_either_way(line, asked):
    assert pace.hear(line, CFG, after_read_back=False).request == asked


@pytest.mark.parametrize(
    "line",
    [
        "The fan is spinning slower than it used to.",
        "It heats too slow, it takes hours.",
        "Can someone come faster than Thursday?",
        "The thermostat drops too fast at night.",
        "The blower started to slow down and then stopped.",
    ],
)
def test_talk_about_the_equipment_is_not_a_pace_request(line):
    heard = pace.hear(line, CFG, after_read_back=False)
    assert heard.request is None and not heard.repeat_ask


@pytest.mark.parametrize(
    "line, after_read_back",
    [
        ("Sorry, can you say that again slower?", True),
        ("Could you repeat that slowly?", True),
        ("Can you say the address slower?", False),
    ],
)
def test_a_slow_down_for_the_address_alone_is_told_apart(line, after_read_back):
    heard = pace.hear(line, CFG, after_read_back)
    assert heard.request == pace.SLOWER and heard.address_only


def test_say_that_again_slower_away_from_the_read_back_slows_the_whole_call():
    heard = pace.hear("Can you say that again slower?", CFG, after_read_back=False)
    assert heard.request == pace.SLOWER and not heard.address_only


@pytest.mark.parametrize("line", ["Sorry, what?", "What?", "Huh?", "I didn't catch that."])
def test_sorry_what_is_a_repeat_ask(line):
    assert pace.hear(line, CFG, after_read_back=False).repeat_ask


@pytest.mark.parametrize(
    "line, older",
    [
        ("I'm 84 and the heat's out.", True),
        ("I am 65 years old.", True),
        ("Im 70, I live alone.", True),
        ("I'm in my seventies.", True),
        ("I'm elderly, I can't climb to the attic.", True),
        ("I'm 64.", False),
        ("I'm 100 percent sure it's the boiler.", False),
        ("I'm 70 minutes away from the house.", False),
        ("My elderly mother lives with me.", False),
        ("My dad is 90.", False),
        ("The house is 100 years old.", False),
    ],
)
def test_only_the_callers_own_age_or_saying_they_are_elderly_counts(line, older):
    assert pace.hear(line, CFG, after_read_back=False).older_adult is older


def test_steps_are_the_same_size_each_way_and_stay_inside_the_bounds():
    assert pace.stepped(1.0, pace.SLOWER, CFG) == 0.85
    assert pace.stepped(1.0, pace.FASTER, CFG) == 1.15
    assert pace.stepped(0.7, pace.SLOWER, CFG) == CFG["slowest"]
    assert pace.stepped(1.3, pace.FASTER, CFG) == CFG["fastest"]
    assert pace.stepped(0.7, pace.NORMAL, CFG) == 1.0
    assert 0.6 <= CFG["slowest"] < CFG["normal"] < CFG["fastest"] <= 2.0


# ---- on a call -----------------------------------------------------------------------------


def test_a_call_starts_at_the_normal_pace_and_every_turn_says_the_pace(agent):
    started = agent.start()
    call = started["data"]["call_id"]
    assert started["data"]["pace"] == {"speed": 1.0}
    assert speed(agent.turn(call, "Hi, my heat's out.")) == 1.0
    assert changes(agent, call) == []


def test_asking_to_slow_down_holds_for_the_call_and_is_remembered(agent):
    call = agent.start()["data"]["call_id"]
    result = agent.turn(call, "Can you slow down a little, please?")
    assert speed(result) == 0.85
    assert "pace_slower" in result["data"]["hints"]
    assert speed(agent.turn(call, "The heat's out in the bedroom.")) == 0.85
    assert changes(agent, call) == [(1, 0.85, "call", "request")]
    assert remembered(agent) == 0.85


def test_asking_to_speed_up_works_the_same_way(agent):
    call = agent.start()["data"]["call_id"]
    result = agent.turn(call, "You can talk faster, I'm in a rush.")
    assert speed(result) == 1.15
    assert "pace_faster" in result["data"]["hints"]
    assert remembered(agent) == 1.15


def test_asking_for_normal_speed_goes_back_and_forgets_the_request(agent):
    call = agent.start()["data"]["call_id"]
    agent.turn(call, "Please slow down.")
    assert speed(agent.turn(call, "Okay, normal speed is fine now.")) == 1.0
    assert remembered(agent) is None


def test_the_pace_never_goes_past_its_bounds(agent):
    call = agent.start()["data"]["call_id"]
    for _ in range(4):
        result = agent.turn(call, "Can you slow down?")
    assert speed(result) == CFG["slowest"]
    assert "pace_slower" not in result["data"]["hints"]


def test_sorry_what_twice_is_one_step_slower_and_not_remembered(agent):
    call = agent.start()["data"]["call_id"]
    assert speed(agent.turn(call, "Sorry, what?")) == 1.0
    agent.say(call, "I said, what's the ZIP code there?")
    result = agent.turn(call, "Sorry, what?")
    assert speed(result) == 0.85
    assert "pace_slower" not in result["data"]["hints"]
    assert changes(agent, call) == [(3, 0.85, "call", "repeats")]
    assert remembered(agent) is None


def test_an_older_caller_is_spoken_to_more_slowly(agent):
    call = agent.start()["data"]["call_id"]
    assert speed(agent.turn(call, "I'm 82 and the boiler stopped.")) == CFG["older_adult_speed"]
    assert changes(agent, call) == [(1, 0.85, "call", "older_adult")]
    assert remembered(agent) is None


def test_an_age_under_65_or_someone_elses_age_changes_nothing(agent):
    call = agent.start()["data"]["call_id"]
    agent.turn(call, "I'm 61. My father is 90 and lives with me.")
    assert changes(agent, call) == []


def test_a_caller_who_asked_for_a_pace_keeps_it_when_they_give_their_age(agent):
    call = agent.start()["data"]["call_id"]
    agent.turn(call, "You can speed up, I'm fine.")
    assert speed(agent.turn(call, "I'm 70 by the way.")) == 1.15


def test_an_older_caller_who_asks_to_slow_down_further_gets_another_step(agent):
    call = agent.start()["data"]["call_id"]
    agent.turn(call, "I'm elderly, so be patient with me.")
    assert speed(agent.turn(call, "Could you slow down?")) == 0.7


def test_slowing_down_for_the_address_applies_to_the_address_alone(agent):
    call = agent.start()["data"]["call_id"]
    agent.turn(call, "It's 101 Union St, 11231.")
    address = in_area_address()
    asked = agent.tool("read_back", call, address=address, phone="caller_id")
    assert "parts" not in asked["data"]
    agent.say(call, asked["say"])
    result = agent.turn(call, "Sorry, can you say that again slower?")
    assert speed(result) == 1.0
    assert "pace_address" in result["data"]["hints"]
    again = agent.tool("read_back", call, address=address, phone="caller_id")
    parts = again["data"]["parts"]
    assert " ".join(p["text"] for p in parts) == again["say"]
    assert parts[1] == {"text": "101 Union St, Brooklyn, 11231,", "speed": 0.85}
    assert parts[0]["speed"] == parts[2]["speed"] == 1.0
    # Used once: the call's pace is unchanged and the next read-back is whole.
    assert changes(agent, call) == [(3, 0.85, "address", "request")]
    agent.say(call, again["say"])
    assert speed(agent.turn(call, "Yes, that's right.")) == 1.0
    third = agent.tool("read_back", call, address=address, phone="caller_id")
    assert "parts" not in third["data"]
    assert remembered(agent) is None


def test_the_address_slows_one_step_below_the_calls_own_pace(agent):
    call = agent.start()["data"]["call_id"]
    agent.turn(call, "I'm 77.")
    agent.turn(call, "Please say the address slower when you read it.")
    agent.turn(call, "It's 101 Union St, 11231.")
    asked = agent.tool("read_back", call, address=in_area_address(), phone="caller_id")
    assert [p["speed"] for p in asked["data"]["parts"]] == [0.85, 0.7, 0.85]


def test_a_requested_pace_carries_to_a_linked_call_the_same_day(agent, clock):
    first = agent.start()["data"]["call_id"]
    agent.turn(first, "Slow down please.")
    agent.end(first, "dropped")
    clock.advance(minutes=3)
    second = agent.start()["data"]
    assert second["linked_call"]["call_id"] == first
    assert second["pace"] == {"speed": 0.85}
    assert changes(agent, second["call_id"]) == [(0, 0.85, "call", "linked")]


def test_a_remembered_pace_starts_the_callers_next_call(agent, clock):
    first = agent.start()["data"]["call_id"]
    agent.turn(first, "Could you talk slower?")
    agent.end(first)
    clock.advance(days=1)
    second = agent.start()["data"]
    assert second["linked_call"] is None
    assert second["pace"] == {"speed": 0.85}
    assert changes(agent, second["call_id"]) == [(0, 0.85, "call", "record")]


def test_an_older_callers_pace_is_not_remembered_for_the_next_call(agent, clock):
    first = agent.start()["data"]["call_id"]
    agent.turn(first, "I'm 80.")
    agent.end(first)
    clock.advance(days=1)
    assert agent.start()["data"]["pace"] == {"speed": 1.0}


def test_the_safety_lines_come_back_with_the_calls_pace(agent):
    call = agent.start()["data"]["call_id"]
    agent.turn(call, "Slow down please.")
    result = agent.turn(call, "I smell gas in the kitchen.")
    assert result["data"]["safety"]["action"] == "speak_lines"
    assert speed(result) == 0.85


def test_the_trace_shows_the_pace_and_its_changes(agent):
    from app.trace import timeline

    call = agent.start()["data"]["call_id"]
    agent.turn(call, "Slow down please.")
    conn = agent.db()
    trace = timeline(conn, call)
    conn.close()
    assert trace["pace"][0]["speed"] == 0.85
    assert trace["pace_changes"][0]["source"] == "request"
    assert trace["caller_pace"][0]["speed"] == 0.85


# ---- false triggers (PR #83 review) ----------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "You don't need to speak slower, you're fine.",
        "No need to talk slowly, I can hear you.",
        "You don't have to slow down for me.",
        "I don't need you to slow down.",
        "You're not talking too fast.",
    ],
)
def test_a_negated_request_changes_nothing(line):
    assert pace.hear(line, CFG, after_read_back=False).request is None


def test_a_negated_request_is_not_remembered(agent):
    call = agent.start()["data"]["call_id"]
    assert speed(agent.turn(call, "You don't need to speak slower, I'm following.")) == 1.0
    assert remembered(agent) is None


@pytest.mark.parametrize(
    "line",
    [
        "Can you send someone faster please?",
        "Can you speed up the install?",
        "Can you speed up the repair?",
        "Could the technician come a bit faster please?",
        "Can you schedule it slower please, like next week?",
        "The motor will slow down and stop.",
        "Things speed up when the heat kicks on.",
    ],
)
def test_talk_about_the_job_is_not_a_pace_request(line):
    heard = pace.hear(line, CFG, after_read_back=False)
    assert heard.request is None and not heard.address_only


def test_a_job_speed_request_is_not_remembered(agent):
    call = agent.start()["data"]["call_id"]
    assert speed(agent.turn(call, "Can you send someone faster please?")) == 1.0
    assert remembered(agent) is None


@pytest.mark.parametrize(
    "line, asked",
    [
        ("Slower please.", pace.SLOWER),
        ("Um, a little slower.", pace.SLOWER),
        ("Can you speed up?", pace.FASTER),
        ("Faster please.", pace.FASTER),
        ("I need you to slow down.", pace.SLOWER),
        ("Could you please slow down there?", pace.SLOWER),
    ],
)
def test_short_requests_and_slow_down_said_to_the_agent_still_count(line, asked):
    assert pace.hear(line, CFG, after_read_back=False).request == asked


@pytest.mark.parametrize("after_read_back", [False, True])
@pytest.mark.parametrize(
    "line",
    [
        "My heat is slow, I live on Main Street.",
        "The slow drain is at 12 Oak Street.",
        "The fan runs slower at 45 Elm Street, can you repeat the visit time?",
        "It's slowly getting colder at my address.",
    ],
)
def test_equipment_talk_with_an_address_is_not_an_address_request(line, after_read_back):
    heard = pace.hear(line, CFG, after_read_back)
    assert not heard.address_only and heard.request is None


@pytest.mark.parametrize(
    "line",
    ["Can you say the address slower?", "Slower on the address, please.",
     "Repeat the address slowly for me."],
)
def test_asking_to_hear_the_address_slower_counts_anywhere_in_the_call(line):
    heard = pace.hear(line, CFG, after_read_back=False)
    assert heard.address_only and heard.request == pace.SLOWER


@pytest.mark.parametrize(
    "line",
    [
        "I'm 70 yards from the house.",
        "I'm 60 to 70 percent sure it's the boiler.",
        "I'm 80-90 percent sure.",
        "I'm 75 or 80 feet up the driveway.",
    ],
)
def test_numbers_that_are_not_ages_are_not_read_as_ages(line):
    assert pace.own_age(line) is None


def test_an_older_caller_asking_for_the_address_slower_gets_both(agent):
    call = agent.start()["data"]["call_id"]
    result = agent.turn(call, "I'm 80, so say the address slower when you read it.")
    assert speed(result) == 0.85
    assert changes(agent, call) == [(1, 0.85, "call", "older_adult"), (1, 0.7, "address", "request")]
    agent.turn(call, "It's 101 Union St, 11231.")
    asked = agent.tool("read_back", call, address=in_area_address(), phone="caller_id")
    assert [p["speed"] for p in asked["data"]["parts"]] == [0.85, 0.7, 0.85]


# ---- bounds and scope ------------------------------------------------------------------------


def test_every_pace_is_kept_inside_what_the_voice_accepts():
    assert pace.clamp(0.3) == pace.SPEED_MIN == 0.6
    assert pace.clamp(5) == pace.SPEED_MAX == 2.0
    loose = {**CFG, "slowest": 0.2, "fastest": 3.0, "normal": 1.0, "older_adult_speed": 0.1}
    tight = pace.clamped_config(loose)
    assert (tight["slowest"], tight["fastest"], tight["older_adult_speed"]) == (0.6, 2.0, 0.6)
    assert pace.stepped(0.6, pace.SLOWER, tight) == 0.6


def test_the_tenant_is_loaded_with_clamped_speeds():
    from app.tenant import Tenant

    tenant = Tenant.load(TENANT_DIR)
    assert all(0.6 <= tenant.pace[k] <= 2.0 for k in pace.SPEED_KEYS)


def test_a_stored_pace_outside_the_voices_range_starts_the_call_clamped(agent):
    conn = agent.db()
    conn.execute(
        "INSERT INTO caller_pace (tenant, phone_key, speed, call_id, set_at) "
        "VALUES ('summit-air', '2125550999', 4.5, NULL, '2026-11-01T00:00:00Z')"
    )
    conn.close()
    assert agent.start()["data"]["pace"] == {"speed": 2.0}


def test_a_remembered_pace_belongs_to_one_number_and_one_tenant(agent, clock):
    first = agent.start()["data"]["call_id"]
    agent.turn(first, "Could you talk slower?")
    agent.end(first)
    clock.advance(days=1)
    assert agent.start("(718) 555-0199")["data"]["pace"] == {"speed": 1.0}
    conn = agent.db()
    conn.execute(
        "INSERT INTO calls (id, tenant, caller_id, caller_key, dialed_number, direction, started_at)"
        " VALUES ('other-1', 'other-co', '(212) 555-0999', '2125550999', '', 'inbound', "
        "'2026-11-03T00:00:00Z')"
    )
    started = pace.start(conn, CFG, "other-co", "other-1", "2125550999", None, clock())
    conn.close()
    assert started == {"speed": 1.0}
