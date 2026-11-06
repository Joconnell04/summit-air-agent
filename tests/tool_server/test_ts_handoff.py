"""Cold handoff (app/transfer.py): a caller who demands a person is sent straight to one, decided by
the server on the caller's own turn. A passing mention or a calm, single ask is not a demand; a gas
call waits for the fixed lines; the flag and callback are stored before anything is dialed, so an
unset number or a failed transfer leaves the caller with the callback line."""

from datetime import UTC, datetime

import pytest
from test_ts_safety import CO_LINES, GAS_LINES
from toolkit import UNKNOWN, Agent, count

from app import owner_settings

OFFICE = "+12125550100"
ON_CALL = "+12125550111"
# 10 p.m. Monday in New York: the office is closed (config.yaml hours).
EVENING = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)
HANDOFF_LINE = "Got it, transferring you now."
UNPUNCTUATED = (
    "this is ridiculous the furnace you fixed died again put me through to a real person right now"
)


@pytest.fixture
def numbers(monkeypatch):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", OFFICE)
    monkeypatch.setenv("TRANSFER_ON_CALL_NUMBER", ON_CALL)


def call_with(agent: Agent, *lines: str) -> tuple[str, dict]:
    """A call whose caller says these lines in turn; returns the last line's turn result."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    result = {}
    for line in lines:
        result = agent.turn(call, line)
    return call, result


def done(agent: Agent, call: str, handoff_id: int, outcome: str) -> dict:
    response = agent.client.post(
        f"/calls/{call}/handoff/{handoff_id}/done", json={"outcome": outcome}
    )
    assert response.status_code == 200
    return response.json()


def handoff(agent: Agent, call: str) -> dict | None:
    conn = agent.db()
    row = conn.execute("SELECT * FROM handoffs WHERE call_id = ?", (call,)).fetchone()
    conn.close()
    return dict(row) if row else None


def test_an_angry_demand_in_office_hours_goes_straight_to_the_office(agent, numbers):
    call, result = call_with(
        agent, "Put me through to a manager right now. Your guy fixed my furnace and it's dead again."
    )
    assert result["say"] == HANDOFF_LINE
    plan = result["data"]["handoff"]["transfer"]
    assert (plan["role"], plan["phone"], plan["ring_seconds"]) == ("office", OFFICE, 20)
    # Stored before anything is dialed: the flag, and the callback kept if the transfer fails.
    assert result["data"]["handoff"]["fallback"] == (
        "I've let our team know, and someone will call you by 9:30 AM."
    )
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'person' AND priority = 'High'") == 1
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE status = 'pending'") == 1
    assert handoff(agent, call)["status"] == "dialing"


@pytest.mark.parametrize(
    "lines",
    [
        # Anger in the line just before a plain ask, or in the same line.
        ("This is ridiculous, the furnace died again.", "Can I talk to someone?"),
        ("Damn it, can I talk to someone?",),
        # Asked twice.
        ("Can I speak to a person?", "I'd like to book a visit.", "Can I talk to someone, please?"),
        # Insistent words in the same line.
        ("I said I want a real person, not a robot.",),
        ("Transfer me right away.",),
        # Speech-to-text with the full stops dropped: the instruction is still a request (eval
        # angry-caller-demands-person, first run).
        (UNPUNCTUATED,),
    ],
)
def test_what_counts_as_a_demand(agent, numbers, lines):
    _, result = call_with(agent, *lines)
    assert result["say"] == HANDOFF_LINE


@pytest.mark.parametrize(
    "lines",
    [
        # A calm, single ask stays with the warm transfer, through flag_person.
        ("Can I just talk to a real person, please?",),
        # "Now" said calmly is a request, not a demand.
        ("Can I talk to someone now?",),
        ("Transfer me now, please.",),
        # Swearing about the equipment isn't anger at the call.
        ("The damn furnace is dead again. Can I talk to someone?",),
        # Anger two lines back, before an address, doesn't carry to a plain ask.
        ("This is ridiculous.", "It's 12 Court Street.", "Can I talk to someone?"),
        # A passing mention is not a request.
        ("Last time I tried to talk to someone it took a week, so I'm calling early.",),
        ("My landlord said to talk to someone about the heat, it's ridiculous.",),
        # Asking what it is talking to, however annoyed.
        ("Damn it, are you a real person?",),
        # "Get me someone" is a technician, not a person on the phone.
        ("Get me someone out here today, this is ridiculous.",),
        # Someone else putting them through, not the caller asking.
        ("last time they put me through to someone right away it was useless",),
        ("the guy said hed get me a manager right now and nobody called this is ridiculous",),
    ],
)
def test_what_does_not_count(agent, numbers, lines):
    call, result = call_with(agent, *lines)
    assert "handoff" not in result["data"]
    assert handoff(agent, call) is None
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'person'") == 0


def test_after_hours_a_demand_goes_to_the_on_call_technician(agent, clock, numbers):
    clock.now = EVENING
    _, result = call_with(agent, "This is bullshit, put me through to a person.")
    plan = result["data"]["handoff"]["transfer"]
    assert (plan["role"], plan["phone"]) == ("on_call", ON_CALL)
    # The on-call technician's own callback time, if the transfer fails.
    assert result["data"]["handoff"]["fallback"] == (
        "I've let our team know, and someone will call you by 10:20 PM."
    )


def test_with_no_number_set_the_caller_hears_the_callback_line(agent):
    call, result = call_with(agent, "Put me through to a manager right now.")
    assert result["say"] == "I've let our team know, and someone will call you by 9:30 AM."
    assert result["data"]["handoff"]["transfer"] is None
    assert handoff(agent, call)["status"] == "not_dialed"
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE status = 'pending'") == 1


def test_with_the_owners_transfer_switch_off_nothing_is_dialed(agent, numbers):
    """The settings page's warm transfer switch covers the cold handoff as well."""
    conn = agent.db()
    try:
        owner_settings.change(
            conn, agent.app.state.services.tenants.default, {"warm_transfer": False}, "Pat",
            agent.clock(),
        )
    finally:
        conn.close()
    call, result = call_with(agent, "Put me through to a manager right now.")
    assert result["say"] == "I've let our team know, and someone will call you by 9:30 AM."
    assert result["data"]["handoff"]["transfer"] is None
    assert handoff(agent, call)["status"] == "not_dialed"


def test_with_no_on_call_number_after_hours_nothing_is_dialed(agent, clock, monkeypatch):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", OFFICE)
    clock.now = EVENING
    _, result = call_with(agent, "Put me through to a manager right now.")
    assert result["data"]["handoff"]["transfer"] is None
    assert result["say"].endswith("by 10:20 PM.")


def test_a_failed_transfer_gives_the_stored_callback_time(agent, numbers):
    call, result = call_with(agent, "Put me through to a manager right now.")
    plan = result["data"]["handoff"]["transfer"]
    after = done(agent, call, plan["id"], "failed")
    assert after["say"] == (
        "I couldn't reach anyone just now, so I've let the team know and someone will call you "
        "by 9:30 AM."
    )
    assert handoff(agent, call)["status"] == "failed"
    # Reported once.
    assert done(agent, call, plan["id"], "handed_off")["refused"] == "invalid_state"


def test_handed_off_leaves_the_flag_and_callback_open(agent, numbers):
    """Nobody here knows whether a person or a voicemail took the call."""
    call, result = call_with(agent, "Put me through to a manager right now.")
    plan = result["data"]["handoff"]["transfer"]
    assert done(agent, call, plan["id"], "handed_off")["data"] == {"status": "handed_off"}
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'person' AND status = 'open'") == 1
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE status = 'pending'") == 1
    agent.end(call, "transfer")
    assert handoff(agent, call)["status"] == "handed_off"


def test_a_handoff_still_dialing_when_the_call_ends_is_settled_by_how_it_ended(agent, numbers):
    """The worker marks the end as a transfer before sending one, so a report lost in the race
    with the caller leaving still records where the call went."""
    sent, _ = call_with(agent, "Put me through to a manager right now.")
    agent.end(sent, "transfer")
    assert handoff(agent, sent)["status"] == "handed_off"
    dropped = agent.start("(212) 555-0123")["data"]["call_id"]
    agent.turn(dropped, "Put me through to a manager right now.")
    agent.end(dropped, "caller_hangup")
    assert handoff(agent, dropped)["status"] == "failed"


def test_once_per_call_and_never_after_a_warm_transfer(agent, numbers):
    call, first = call_with(agent, "Put me through to a manager right now.")
    done(agent, call, first["data"]["handoff"]["transfer"]["id"], "failed")
    again = agent.turn(call, "I said put me through to a manager right now!")
    assert "handoff" not in again["data"]
    # A warm transfer already tried on the call rules out a handoff too.
    warm = agent.start("(212) 555-0123")["data"]["call_id"]
    agent.turn(warm, "Can I talk to a real person please?")
    flagged = agent.tool("flag_person", warm, reason="Caller wants to talk to someone")
    body = {"flag_id": flagged["data"]["flag_id"], "callback_id": flagged["data"]["callback_id"]}
    assert agent.client.post(f"/calls/{warm}/transfer", json=body).json()["data"]["transfer"]
    assert "handoff" not in agent.turn(warm, "Put me through to a person right now!")["data"]


def test_on_a_gas_call_the_lines_come_first_then_a_demand_is_handed_off(agent, numbers):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    # A demand on the same line as the signal gets the lines, word for word, and nothing else.
    first = agent.turn(call, "I smell gas in the kitchen, get me a person right now!")
    assert first["data"]["safety"]["action"] == "speak_lines"
    assert first["say"] == GAS_LINES
    assert "handoff" not in first["data"]
    agent.say(call, GAS_LINES)
    result = agent.turn(call, "We're outside. Put me through to a person right now.")
    assert result["say"] == HANDOFF_LINE
    assert result["data"]["handoff"]["transfer"]["role"] == "office"
    # As on any safety call, no callback time: the safety flag already has a person following up.
    assert result["data"]["handoff"]["callback_id"] is None
    assert result["data"]["handoff"]["fallback"] == "I've let our team know."
    reasons = [
        r["reason"]
        for r in agent.db().execute("SELECT reason FROM flags WHERE kind = 'person'").fetchall()
    ]
    assert reasons == ["Caller demanded a person after the safety lines"]


def test_on_a_gas_call_after_hours_the_demand_goes_to_on_call(agent, clock, numbers):
    clock.now = EVENING
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas in the hallway.")
    agent.say(call, GAS_LINES)
    result = agent.turn(call, "We're out. I need to talk to a person right now.")
    assert result["data"]["handoff"]["transfer"]["phone"] == ON_CALL


def test_on_a_gas_call_before_the_lines_are_said_nobody_is_dialed(agent, numbers):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas in the kitchen.")
    # The lines haven't been said yet (the agent's line hasn't come back), so it waits.
    result = agent.turn(call, "Put me through to a person right now.")
    assert "handoff" not in result["data"]
    assert handoff(agent, call) is None


def test_a_turn_that_brings_safety_text_is_never_handed_off(agent, numbers):
    """The turn endpoint's guard alone: after the lines the call may be handed off, but a line that
    raises a new hazard gets its lines first, and the next demand is handed off."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I smell gas in the kitchen.")
    agent.say(call, GAS_LINES)
    again = agent.turn(
        call, "Now the carbon monoxide alarm is going off too, put me through to a person right now."
    )
    assert again["data"]["safety"]["action"] == "speak_lines"
    assert "handoff" not in again["data"]
    assert handoff(agent, call) is None
    agent.say(call, CO_LINES)
    after = agent.turn(call, "I said put me through to a person right now.")
    assert after["say"] == HANDOFF_LINE


def test_while_the_safety_check_question_is_open_nobody_is_dialed(agent, numbers):
    """The safety-state guard alone: the turn brings no safety text, but the check question hasn't
    been answered, so whether the caller should be outside isn't known yet."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    assert agent.turn(call, "There's a weird smell near the furnace.")["data"]["safety"][
        "action"
    ] == "ask_check"
    result = agent.turn(call, "Put me through to a person right now.")
    assert result["data"].get("safety") is None
    assert "handoff" not in result["data"]
    assert handoff(agent, call) is None


def test_never_on_a_commercial_call(agent, numbers):
    _, result = call_with(
        agent, "I manage a restaurant on Court Street. Put me through to a manager right now."
    )
    assert "handoff" not in result["data"]
