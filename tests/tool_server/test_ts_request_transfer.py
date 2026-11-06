"""Retell's warm transfer (request_transfer, issue #184): the server decides whether a caller goes
to the office now. In office hours, with the owner's switch on and an office number set, a caller
who asked for a person in their own words, or has a complaint or a billing question, is put
through; never on a safety or commercial call, never twice; after hours the callback path stays."""

from datetime import UTC, datetime

import pytest
from test_ts_safety import GAS_LINES
from toolkit import UNKNOWN, Agent, count

OFFICE = "+12125550100"
# 10 p.m. Monday in New York: the office is closed (config.yaml hours).
EVENING = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)


@pytest.fixture
def numbers(monkeypatch):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", OFFICE)


def row(agent: Agent, sql: str, *params):
    conn = agent.db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def started(agent: Agent, line: str) -> str:
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, line)
    return call


def request(agent: Agent, call: str, reason: str = "person", **args) -> dict:
    summary = args.pop("summary", "Wants to talk to someone about a visit")
    return agent.tool("request_transfer", call, reason=reason, summary=summary, **args)


def test_a_caller_who_asks_for_a_person_in_office_hours_is_put_through(agent, numbers):
    call = started(agent, "Can I just talk to a real person, please?")
    found = request(agent, call)
    assert found["ok"], found
    assert found["say"] == "Okay, I'll connect you with someone from our team now. One moment."
    data = found["data"]
    assert data["transfer"]["number"] == OFFICE and data["transfer"]["role"] == "office"
    # A transfer nobody answers leaves a named callback, stored before anything is dialed.
    assert data["fallback"] == (
        "I couldn't reach anyone just now, so I've let the team know and someone will call you "
        "by 9:30 AM."
    )
    flag = row(agent, "SELECT * FROM flags WHERE id = ?", data["flag_id"])
    assert flag["kind"] == "transfer"
    assert flag["reason"] == "Asked for a person: Wants to talk to someone about a visit"
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE call_id = ?", call) == 1
    sent = row(agent, "SELECT * FROM transfers WHERE call_id = ?", call)
    assert (sent["role"], sent["status"], sent["flag_id"]) == ("office", "dialing", data["flag_id"])


def test_a_complaint_or_billing_question_goes_through_without_asking_for_a_person(agent, numbers):
    for reason, line in (
        ("complaint", "The technician left a mess yesterday and I'm not happy about it."),
        ("billing", "I was charged twice for the same visit."),
    ):
        call = started(agent, line)
        found = request(agent, call, reason, summary=line)
        assert found["ok"], found
        flag = row(agent, "SELECT * FROM flags WHERE id = ?", found["data"]["flag_id"])
        assert flag["reason"].startswith(reason.capitalize() + ": ")


def test_a_request_for_a_person_must_be_in_the_callers_own_words(agent, numbers):
    call = started(agent, "Wait, are you a real person?")
    found = request(agent, call)
    assert found["refused"] == "transfer_not_asked"
    assert "hasn't asked for a person" in found["data"]["need"]
    assert count(agent, "SELECT COUNT(*) FROM transfers") == 0
    assert count(agent, "SELECT COUNT(*) FROM flags") == 0


def test_after_hours_the_caller_is_offered_a_callback_instead(agent, clock, numbers):
    clock.now = EVENING
    call = started(agent, "Can I speak to someone in the office?")
    found = request(agent, call)
    assert found["refused"] == "transfer_closed"
    assert found["say"].startswith("Our office is closed right now")
    assert "flag_person" in found["data"]["need"]
    assert count(agent, "SELECT COUNT(*) FROM transfers") == 0


def test_with_no_office_number_set_the_caller_is_offered_a_callback(agent):
    call = started(agent, "Can I speak to someone in the office?")
    found = request(agent, call)
    assert found["refused"] == "transfer_unavailable"
    assert found["say"].startswith("I can't put you through right now")
    assert count(agent, "SELECT COUNT(*) FROM flags") == 0


def test_with_the_owners_switch_off_nobody_is_put_through(agent, numbers, monkeypatch):
    from app import transfer

    monkeypatch.setattr(transfer, "switched_on", lambda conn, tenant, call: False)
    call = started(agent, "Can I speak to someone in the office?")
    assert request(agent, call)["refused"] == "transfer_unavailable"


def test_never_on_an_open_safety_call(agent, numbers):
    call = started(agent, "I smell gas in the kitchen.")
    agent.say(call, GAS_LINES)
    agent.turn(call, "Okay, we're outside. Can I talk to a person?")
    found = request(agent, call)
    assert found["refused"] == "transfer_safety"
    assert count(agent, "SELECT COUNT(*) FROM transfers") == 0


def test_never_on_a_commercial_call(agent, numbers):
    call = started(agent, "I manage a restaurant on Court Street. Can I speak to someone?")
    assert request(agent, call)["refused"] == "transfer_commercial"


def test_a_call_is_put_through_once(agent, numbers):
    call = started(agent, "Can I just talk to a real person, please?")
    assert request(agent, call)["ok"]
    again = request(agent, call, key="second")
    assert again["refused"] == "transfer_tried"
    assert count(agent, "SELECT COUNT(*) FROM transfers WHERE call_id = ?", call) == 1


def test_a_connected_transfer_closes_the_flag_and_callback(agent, numbers):
    call = started(agent, "Can I just talk to a real person, please?")
    data = request(agent, call)["data"]
    done = agent.client.post(
        f"/calls/{call}/transfer/{data['transfer']['id']}/done", json={"outcome": "connected"}
    )
    assert done.json()["ok"]
    assert row(agent, "SELECT status FROM flags WHERE id = ?", data["flag_id"])["status"] == "done"
    callback = row(agent, "SELECT status FROM callbacks WHERE id = ?", data["callback_id"])
    assert callback["status"] == "done"


def test_a_transfer_nobody_answers_keeps_the_callback_and_says_its_time(agent, numbers):
    call = started(agent, "Can I just talk to a real person, please?")
    data = request(agent, call)["data"]
    done = agent.client.post(
        f"/calls/{call}/transfer/{data['transfer']['id']}/done", json={"outcome": "no_answer"}
    ).json()
    assert done["say"] == data["fallback"]
    assert row(agent, "SELECT status FROM flags WHERE id = ?", data["flag_id"])["status"] == "open"


def test_a_call_that_ends_by_transfer_settles_an_unreported_transfer(agent, numbers):
    call = started(agent, "Can I just talk to a real person, please?")
    assert request(agent, call)["ok"]
    ended = agent.end(call, "transfer")
    assert row(agent, "SELECT status FROM transfers WHERE call_id = ?", call)["status"] == (
        "connected"
    )
    # Shown as put through, not as the fallback callback it kept.
    assert ended["data"]["outcome"] == "transferred"


def test_the_owner_view_names_the_transfer_flag():
    from app import owner

    assert owner.FLAG_TITLES["transfer"] == "Put through to the office"
