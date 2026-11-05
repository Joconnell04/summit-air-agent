"""A caller who asks the agent to wait (DECISIONS.md 081): the hold is recorded, a line said to
someone else on hold is an aside, a reply or a safety line ends the hold, and a call that ended
because the caller stepped away is greeted as such when they call back."""

import pytest
import yaml
from toolkit import TENANT_DIR

from app import dead_air
from prompts.assemble import greeting

VOICE = yaml.safe_load((TENANT_DIR / "agent.yaml").read_text())["voice"]
CALLER = "(718) 555-0106"


@pytest.mark.parametrize(
    "line,holds",
    [
        ("Hold on, let me grab my account number.", True),
        ("Give me a sec, the dog's barking", True),
        ("hang on one second", True),
        ("Can you hold? Someone's at the door.", True),
        ("I can hold the ladder for the technician", False),
        ("Let me check, it says 48", False),
        ("The heat's been out since Sunday", False),
        ("my furnace runs for about one minute then shuts off", False),
        ("it clicks for one second", False),
        ("one minute please", True),
        ("just a minute, let me check", True),
        ("The heat is out. Hold on, let me grab my bill.", True),
    ],
)
def test_asking_to_wait(line, holds):
    assert dead_air.asks_to_hold(line) is holds


@pytest.mark.parametrize(
    "line,reply",
    [
        ("Okay, I'm back.", True),
        ("Sorry about that", True),
        ("Hello? You still there?", True),
        ("it says 48", True),
        ("I found it, it's a Carrier", True),
        ("Can you still hear me?", True),
        ("Um, what was the question?", True),
        ("um what was the question", True),
        ("Sorry, which day did you say?", True),
        ("Okay, honey, where's the remote?", False),
        ("It's a Carrier, I think", False),
        ("Honey, where did you put the last bill?", False),
        ("okay kids come on", False),
        ("hey get off that", False),
        ("And now, the weather on Channel Seven", False),
        ("Kids, keep it down in there", False),
    ],
)
def test_on_hold_a_line_said_to_someone_else_is_an_aside(line, reply):
    assert dead_air.is_reply(line) is reply


def test_a_question_after_a_wait_ends_the_hold_and_gets_no_take_your_time(agent):
    """Slate call 2 (Retell call_3cd4c8065f26d3de686f7a1e002): "Uh, yeah, one second", then "Um,
    what was the question?" still got "Take your time."."""
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    held = agent.turn(call_id, "Uh, yeah, one second")
    assert "caller_holding" in held["data"]["hints"]
    agent.say(call_id, "Sure, take your time.")
    back = agent.turn(call_id, "Um, what was the question?")
    assert back["data"]["hold"] == {"state": "back"}
    assert "caller_holding" not in back["data"]["hints"]


def test_a_hold_then_an_aside_then_the_caller_back(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    held = agent.turn(call_id, "Hold on, let me find the paperwork.")
    assert held["data"]["hold"] == {"state": "holding"}
    assert "caller_holding" in held["data"]["hints"]
    aside = agent.turn(call_id, "Honey, where's the folder with the warranty?")
    assert aside["data"]["hold"] == {"state": "aside"}
    back = agent.turn(call_id, "Okay, sorry, I've got it.")
    assert back["data"]["hold"] == {"state": "back"}
    after = agent.turn(call_id, "The furnace is a Lennox.")
    assert after["data"]["hold"] is None


def test_a_gas_smell_on_hold_still_gets_the_lines_and_ends_the_hold(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    agent.turn(call_id, "One sec, I'll go look at the furnace.")
    result = agent.turn(call_id, "oh god it smells like gas down here")
    assert result["data"]["safety"]["action"] == "speak_lines"
    assert result["data"]["hold"] == {"state": "back"}


def test_asking_to_wait_on_a_gas_call_is_not_a_hold(agent):
    call_id = agent.start(call_id="call-1")["data"]["call_id"]
    result = agent.turn(call_id, "Hold on, I smell gas")
    assert result["data"]["safety"]["action"] == "speak_lines"
    assert result["data"]["hold"] is None


def test_a_call_back_after_stepping_away_keeps_the_earlier_turns_and_the_usual_greeting(agent, clock):
    first = agent.start(caller_id=CALLER, call_id="call-1")["data"]["call_id"]
    agent.turn(first, "Hi, this is Tomasz. Our boiler's not firing.")
    agent.turn(first, "Hold on, let me find the model number.")
    agent.end(first, "stepped_away")
    clock.advance(minutes=30)
    started = agent.start(caller_id=CALLER, call_id="call-2")["data"]
    linked = started["linked_call"]
    assert linked["stepped_away"] is True and linked["dropped"] is True
    assert any("boiler" in t["text"] for t in linked["turns"])
    # No "we got disconnected" greeting (DECISIONS.md 115): the usual one.
    assert greeting(started, VOICE) == greeting({}, VOICE)


def test_a_plain_dropped_call_gets_no_cut_off_greeting(agent, clock):
    first = agent.start(caller_id=CALLER, call_id="call-1")["data"]["call_id"]
    agent.turn(first, "Hi, this is Tomasz. Our boiler's not firing.")
    agent.end(first, "dropped")
    clock.advance(minutes=2)
    started = agent.start(caller_id=CALLER, call_id="call-2")["data"]
    assert started["linked_call"]["stepped_away"] is False
    assert "cut off" not in greeting(started, VOICE)


@pytest.mark.parametrize(
    "line",
    ["I'm still here", "I'm here", "yeah, still here", "right here", "I'm back",
     "one more sec", "still looking", "I can't find it", "almost done"],
)
def test_answering_the_check_in_is_a_reply(line):
    assert dead_air.is_reply(line)
