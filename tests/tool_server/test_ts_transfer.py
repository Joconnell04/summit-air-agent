"""Warm transfer to a person (C-11, app/transfer.py): the server decides when, never the model.
In office hours a caller who asks for a person goes to the office; after hours an urgent call goes
to the on-call technician; never a safety call, never with no number set, once per call; and a
transfer nobody answers falls back to the flag's named callback."""

from datetime import UTC, datetime

import pytest
from test_ts_safety import GAS_LINES
from toolkit import UNKNOWN, Agent, count

OFFICE = "+12125550100"
ON_CALL = "+12125550111"
# 10 p.m. Monday in New York: the office is closed (config.yaml hours).
EVENING = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)


@pytest.fixture
def numbers(monkeypatch):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", OFFICE)
    monkeypatch.setenv("TRANSFER_ON_CALL_NUMBER", ON_CALL)


def flag(agent: Agent, call: str, **args) -> dict:
    found = agent.tool("flag_person", call, reason="Caller wants to talk to someone", **args)
    assert found["ok"], found
    return found


def ask(agent: Agent, call: str, flagged: dict) -> dict:
    data = flagged["data"]
    body = {"flag_id": data["flag_id"], "callback_id": data["callback_id"]}
    response = agent.client.post(f"/calls/{call}/transfer", json=body)
    assert response.status_code == 200
    return response.json()


def report(agent: Agent, call: str, transfer_id: int, outcome: str) -> dict:
    response = agent.client.post(
        f"/calls/{call}/transfer/{transfer_id}/done", json={"outcome": outcome}
    )
    assert response.status_code == 200
    return response.json()


def asks_for_a_person(agent: Agent, caller: str = UNKNOWN) -> str:
    call = agent.start(caller)["data"]["call_id"]
    agent.turn(call, "Can I just talk to a real person, please?")
    return call


def test_a_caller_who_asks_for_a_person_in_office_hours_goes_to_the_office(agent, numbers):
    call = asks_for_a_person(agent)
    # No time asked for: this callback is the one kept if nobody picks up.
    flagged = flag(agent, call)
    assert flagged["data"]["callback_spoken"] == "by 9:30 AM"
    found = ask(agent, call, flagged)
    assert found["ok"]
    assert found["say"] == "Got it, transferring you now."
    plan = found["data"]["transfer"]
    assert plan["role"] == "office" and plan["phone"] == OFFICE
    assert (plan["ring_seconds"], plan["accept_seconds"]) == (20, 10)
    # What they hear on picking up names no caller, in case a voicemail greeting answered.
    assert plan["prompt"] == "Summit Air has a caller for the office. Press 1 to take the call."
    assert plan["summary"] == (
        "Caller at 212, 555, 0999: Caller wants to talk to someone. Putting you through now."
    )


def test_with_no_number_set_the_flag_and_callback_stay_as_they_were(agent):
    call = asks_for_a_person(agent)
    # As before transfers: a time is needed, because a person calls back.
    asked = agent.tool("flag_person", call, reason="Caller wants to talk to someone")
    assert asked["refused"] == "invalid_callback_time"
    flagged = flag(agent, call, callback_time="11 AM")
    assert ask(agent, call, flagged)["data"]["transfer"] is None
    assert count(agent, "SELECT COUNT(*) FROM transfers") == 0


def test_a_caller_who_only_asks_whether_it_is_a_person_is_not_put_through(agent, numbers):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Wait, are you a real person?")
    flagged = flag(agent, call, callback_time="11 AM")
    assert ask(agent, call, flagged)["data"]["transfer"] is None


def test_a_flag_the_caller_did_not_ask_a_person_for_stays_a_callback(agent, numbers):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The technician left a mess and I'm not happy about it.")
    flagged = flag(agent, call, callback_time="11 AM")
    assert ask(agent, call, flagged)["data"]["transfer"] is None


def test_never_on_a_safety_call(agent, numbers):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas in the kitchen.")
    agent.say(call, GAS_LINES)
    agent.turn(call, "Okay, we're outside. Can I talk to a person?")
    flagged = flag(agent, call, priority="Urgent")
    assert ask(agent, call, flagged)["data"]["transfer"] is None
    # Not even a callback time asked for: someone follows up once they're safe (journey 3).
    assert flagged["data"]["callback_id"] is None


def test_never_on_a_commercial_call(agent, numbers):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I manage a restaurant on Court Street. Can I speak to someone?")
    flagged = flag(agent, call, callback_time="11 AM")
    assert ask(agent, call, flagged)["data"]["transfer"] is None


def no_heat_tonight(agent: Agent, call: str, who_home: str, inside_temp_f: float | None = None):
    """The agent looks for openings the way it does on journey 11; the server scores it."""
    facts = {"issue": "No heat since this afternoon", "who_home": who_home}
    if inside_temp_f is not None:
        facts["inside_temp_f"] = inside_temp_f
    return agent.tool("find_slots", call, job_type="no_heat", facts=facts)


def test_after_hours_a_call_the_server_sends_to_on_call_goes_to_the_on_call_technician(
    agent, clock, numbers
):
    clock.now = EVENING
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The heat's out and my mother is 88.")
    found = no_heat_tonight(agent, call, "My mother, she's 88")
    assert found["data"]["after_hours_outcome"] == "tonight_if_on_call_agrees"
    flagged = flag(agent, call, priority="Urgent")
    assert flagged["data"]["callback_spoken"] == "by 10:20 PM"
    plan = ask(agent, call, flagged)["data"]["transfer"]
    assert plan["role"] == "on_call" and plan["phone"] == ON_CALL
    assert plan["prompt"] == (
        "Summit Air has a caller for the on-call technician. Press 1 to take the call."
    )
    assert plan["summary"].startswith("Urgent. Caller at 212, 555, 0999:")


def test_after_hours_a_callback_the_caller_timed_for_tomorrow_rings_nobody(agent, clock, numbers):
    """Done-bar persona e: on a no-heat call the server sends to on-call, the caller then asked for
    a call about a bill tomorrow afternoon, and the on-call phone rang at 3:30 AM."""
    clock.now = EVENING
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The heat's out and my mother is 88.")
    no_heat_tonight(agent, call, "My mother, she's 88")
    agent.turn(call, "Also, I've got a question about a bill, can someone call me tomorrow afternoon?")
    flagged = flag(agent, call, callback_day="tomorrow", callback_after="12 PM",
                   callback_before="5 PM")
    assert ask(agent, call, flagged)["data"]["transfer"] is None


def test_after_hours_a_flag_after_the_visit_was_dispatched_rings_nobody(agent, clock, numbers):
    """The on-call technician already has the job, so a later flag isn't put through to them."""
    from test_ts_dispatch import _dispatch

    clock.now = EVENING
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The heat's out and my mother is 88.")
    no_heat_tonight(agent, call, "My mother, she's 88")
    assert _dispatch(agent, call)["data"]["dispatched"]
    flagged = flag(agent, call, priority="Urgent")
    assert ask(agent, call, flagged)["data"]["transfer"] is None


def test_after_hours_an_urgent_flag_from_the_model_alone_rings_nobody(agent, clock, numbers):
    """The flag's priority is the model's to set, so it is never what wakes the technician."""
    clock.now = EVENING
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I'd like to book my yearly tune-up.")
    agent.tool("find_slots", call, job_type="tune_up")
    flagged = flag(agent, call, priority="Urgent")
    assert ask(agent, call, flagged)["data"]["transfer"] is None


def test_after_hours_no_heat_that_can_wait_for_morning_rings_nobody(agent, clock, numbers):
    clock.now = EVENING
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The heat stopped, it's still 64 in here. Just me at home.")
    found = no_heat_tonight(agent, call, "Just me", inside_temp_f=64)
    assert found["data"]["after_hours_outcome"] != "tonight_if_on_call_agrees"
    flagged = flag(agent, call, priority="Urgent")
    assert ask(agent, call, flagged)["data"]["transfer"] is None


def test_after_hours_a_caller_who_asks_for_a_person_on_a_routine_call_gets_a_callback(
    agent, clock, numbers
):
    clock.now = EVENING
    call = asks_for_a_person(agent)
    flagged = flag(agent, call, callback_in_minutes=600)
    assert ask(agent, call, flagged)["data"]["transfer"] is None


def test_after_hours_with_no_on_call_number_the_urgent_callback_stays(agent, clock, monkeypatch):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", OFFICE)
    clock.now = EVENING
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The heat's out and my mother is 88.")
    no_heat_tonight(agent, call, "My mother, she's 88")
    flagged = flag(agent, call, priority="Urgent")
    assert ask(agent, call, flagged)["data"]["transfer"] is None


def test_one_transfer_per_call(agent, numbers):
    call = asks_for_a_person(agent)
    first = ask(agent, call, flag(agent, call))
    report(agent, call, first["data"]["transfer"]["id"], "no_answer")
    again = flag(agent, call, callback_time="11 AM")
    assert ask(agent, call, again)["data"]["transfer"] is None


def test_nobody_answers_and_the_caller_hears_the_callback_time(agent, numbers):
    call = asks_for_a_person(agent)
    flagged = flag(agent, call)
    plan = ask(agent, call, flagged)["data"]["transfer"]
    result = report(agent, call, plan["id"], "no_answer")
    assert result["ok"]
    assert result["say"] == (
        "I couldn't reach anyone just now, so I've let the team know and someone will call you "
        "by 9:30 AM."
    )
    # The flag and its callback are still there for a person.
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'person' AND status = 'open'") == 1
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE status = 'pending'") == 1
    assert count(agent, "SELECT COUNT(*) FROM transfers WHERE status = 'no_answer'") == 1


def test_connected_closes_the_flag_and_callback(agent, numbers):
    call = asks_for_a_person(agent)
    plan = ask(agent, call, flag(agent, call))["data"]["transfer"]
    assert report(agent, call, plan["id"], "connected") == {
        "ok": True,
        "say": "",
        "data": {"status": "connected"},
        "refused": None,
    }
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE status = 'open'") == 0
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE status = 'done'") == 1
    # Reported once; a second report is refused.
    assert report(agent, call, plan["id"], "no_answer")["refused"] == "invalid_state"


def test_a_flag_or_callback_from_another_call_is_refused(agent, numbers):
    other = asks_for_a_person(agent)
    flagged = flag(agent, other)
    call = asks_for_a_person(agent, "(212) 555-0123")
    assert ask(agent, call, flagged)["refused"] == "invalid_state"
    mine = flag(agent, call)
    mine["data"]["callback_id"] = flagged["data"]["callback_id"]
    assert ask(agent, call, mine)["refused"] == "invalid_state"
