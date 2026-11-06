"""The model's tools go through the tool server, and only the server decides (docs/tools.md)."""

import asyncio
import json

import httpx
from livekit.agents.beta.tools.end_call import EndCallTool

from app.tenant import load_agent
from app.tools import TOOLS
from prompts import assemble
from tests.tool_server.toolkit import DIALED, TENANT_DIR, in_area_address
from tests.voice.replies import no_reply
from voice.archive.livekit.bridge import Bridge
from voice.archive.livekit.server import UNREACHABLE, ToolServer, idempotency_key
from voice.archive.livekit.tools import ToolLog, build_tools

SAFETY = load_agent(TENANT_DIR)[0]["safety"]
JOB_TYPES = ["no_heat", "no_cooling", "tune_up", "safety_check", "estimate"]


class Hangups:
    def __init__(self):
        self.calls = []

    def __call__(self, context, last_line, quiet=False):
        self.calls.append((context, last_line))
        self.quiet = quiet


class Voice:
    """What the worker said itself, word for word."""

    def __init__(self):
        self.said = []

    def __call__(self, text):
        self.said.append(text)
        return f"handle:{len(self.said)}"


def tools_for(server: ToolServer, hang_up=None, voice=None) -> dict:
    bridge = Bridge([], play=None, silent=lambda: True)
    built = build_tools(
        assemble.tool_specs(JOB_TYPES),
        server,
        bridge,
        ToolLog(),
        hang_up or Hangups(),
        voice or Voice(),
    )
    return {tool.info.name: tool for tool in built}


def test_the_model_gets_exactly_the_servers_tools_and_no_built_in_end_call(connect):
    tools = tools_for(connect("call-1"))
    assert set(tools) == set(TOOLS)
    assert not any(isinstance(t, EndCallTool) for t in tools.values())
    assert "transfer_call" not in tools


def test_a_tool_call_posts_to_the_server_with_the_call_id(agent, connect):
    agent.start(call_id="call-1")
    agent.turn("call-1", "I'm in 11231.")
    tools = tools_for(connect("call-1"))
    result = json.loads(asyncio.run(tools["check_service_area"]({"zip": "11231"}, None)))
    assert result["ok"] is True
    conn = agent.db()
    row = conn.execute(
        "SELECT * FROM tool_calls WHERE call_id = 'call-1' AND tool = 'check_service_area'"
    ).fetchone()
    conn.close()
    assert row is not None


def test_writes_carry_an_idempotency_key_so_a_retried_note_is_stored_once(
    agent, connect, scenarios
):
    agent.start(call_id="call-1", caller_id=scenarios["repeatCallerOpenJob"]["phone"])
    tools = tools_for(connect("call-1"))
    args = {"text": "Daughter Ana usually answers", "attach_to": "customer"}
    first = json.loads(asyncio.run(tools["add_note"](args, None)))
    again = json.loads(asyncio.run(tools["add_note"](args, None)))
    assert first["ok"] is True and first == again
    conn = agent.db()
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    conn.close()


def test_end_call_before_the_safety_lines_says_nothing_more_and_does_not_hang_up(agent, connect):
    """The caller's line already handed the worker the lines; the refusal doesn't say them again
    (Jackson: "make sure the safety thing is just also in general less repetitive")."""
    call_id = "call-1"
    agent.start(call_id=call_id)
    agent.turn(call_id, "I smell gas in the kitchen")
    hangups, voice = Hangups(), Voice()
    tools = tools_for(connect(call_id), hangups, voice)
    asyncio.run(tools["end_call"]({"reason": "resolved"}, "ctx"))
    assert voice.said == []
    assert hangups.calls == []


def test_a_safety_end_call_hangs_up_with_no_second_goodbye(agent, connect):
    """Jackson's gas test call stayed open after "I'm done"; Retell call
    call_f0846787038f654309219adb1e8 heard the sign-off after the agent's own goodbye."""
    call_id = "call-1"
    agent.start(call_id=call_id)
    agent.say(call_id, agent.turn(call_id, "I smell gas")["say"])
    hangups, voice = Hangups(), Voice()
    tools = tools_for(connect(call_id), hangups, voice)
    asyncio.run(tools["end_call"]({"reason": "resolved"}, "ctx"))
    assert voice.said == []
    assert hangups.calls != []


def test_a_safety_caller_saying_goodbye_hears_no_second_goodbye_and_the_line_drops(agent, connect):
    call_id = "call-1"
    agent.start(call_id=call_id)
    agent.say(call_id, agent.turn(call_id, "I smell gas")["say"])
    hangups, voice = Hangups(), Voice()
    tools = tools_for(connect(call_id), hangups, voice)
    asyncio.run(tools["end_call"]({"reason": "caller_goodbye"}, "ctx"))
    assert voice.said == []
    assert hangups.calls != []


def test_a_goodbye_after_the_sign_off_adds_no_second_goodbye(agent, connect):
    """Eval round 3: one ending on a safety call."""
    call_id = "call-1"
    agent.start(call_id=call_id)
    agent.say(call_id, agent.turn(call_id, "I smell gas")["say"])
    agent.say(call_id, " ".join(SAFETY["sign_off"].split()))
    agent.turn(call_id, "Okay, bye.")
    hangups, voice = Hangups(), Voice()
    tools = tools_for(connect(call_id), hangups, voice)
    assert no_reply(asyncio.run(tools["end_call"]({"reason": "caller_goodbye"}, "ctx")))
    assert voice.said == []
    assert hangups.calls == [("ctx", None)] and hangups.quiet is True


def test_an_end_call_after_the_sign_off_hangs_up_without_saying_it_again(agent, connect):
    call_id = "call-1"
    agent.start(call_id=call_id)
    agent.say(call_id, agent.turn(call_id, "I smell gas")["say"])
    agent.say(call_id, " ".join(SAFETY["sign_off"].split()))
    agent.turn(call_id, "Okay, bye.")
    hangups, voice = Hangups(), Voice()
    tools = tools_for(connect(call_id), hangups, voice)
    asyncio.run(tools["end_call"]({"reason": "resolved"}, "ctx"))
    assert voice.said == [] and hangups.calls != []


def test_end_call_that_the_server_allows_hangs_up_and_ends_the_turn(agent, connect):
    agent.start(call_id="call-1")
    hangups, voice = Hangups(), Voice()
    tools = tools_for(connect("call-1"), hangups, voice)
    assert no_reply(asyncio.run(tools["end_call"]({"reason": "wrong_number"}, "ctx")))
    assert hangups.calls == [("ctx", None)]
    assert voice.said == []


def test_a_booking_refused_for_safety_says_nothing_more(agent, connect):
    """The caller's line handed the worker the lines; the refusal doesn't say them again."""
    agent.start(call_id="call-1")
    agent.turn("call-1", "There's a gas leak, I think")
    voice = Voice()
    tools = tools_for(connect("call-1"), voice=voice)
    args = {
        "job_type": "safety_check",
        "slot": {"date": "2026-11-03", "window": "8 to 12"},
        "details": {
            "issue": "gas",
            "since": "now",
            "tried": "nothing",
            "who_home": "me",
            "approver": "me",
        },
    }
    asyncio.run(tools["book_appointment"](args, None))
    assert voice.said == []


def test_booking_is_refused_by_the_server_until_the_read_back(agent, connect):
    agent.start(call_id="call-1")
    agent.turn("call-1", "The heat's out.")
    tools = tools_for(connect("call-1"))
    slot = agent.tool("find_slots", "call-1", job_type="tune_up")["data"]["days"][0]
    args = {
        "job_type": "tune_up",
        "slot": {"date": slot["date"], "window": slot["windows"][0]},
        "details": {
            "issue": "tune-up",
            "since": "n/a",
            "tried": "nothing",
            "who_home": "caller",
            "approver": "caller",
        },
    }
    result = json.loads(asyncio.run(tools["book_appointment"](args, None)))
    assert result["refused"] == "gate_read_back"


def test_an_unreachable_server_comes_back_as_a_refusal_not_an_error():
    def down(request):
        raise httpx.ConnectError("refused")

    server = ToolServer("http://tool-server", "call-1", transport=httpx.MockTransport(down))
    tools = tools_for(server)
    result = json.loads(asyncio.run(tools["check_service_area"]({"zip": "11231"}, None)))
    assert result == {"ok": False, "refused": UNREACHABLE}


def test_the_idempotency_key_depends_on_call_tool_and_arguments():
    key = idempotency_key("call-1", "add_note", {"text": "a"})
    assert key == idempotency_key("call-1", "add_note", {"text": "a"})
    assert key != idempotency_key("call-2", "add_note", {"text": "a"})
    assert key != idempotency_key("call-1", "add_note", {"text": "b"})


def test_a_slow_tool_is_timed_for_the_turn_log(agent, connect):
    agent.start(call_id="call-1", dialed=DIALED)
    log = ToolLog()
    bridge = Bridge([], play=None, silent=lambda: True)
    specs = assemble.tool_specs(JOB_TYPES)
    built = build_tools(specs, connect("call-1"), bridge, log, Hangups(), Voice())
    tools = {t.info.name: t for t in built}
    asyncio.run(tools["read_back"]({"address": in_area_address(), "phone": "caller_id"}, None))
    ms, played = log.take()
    assert ms > 0 and played is False
    assert log.take() == (0.0, False)


def test_the_read_back_sentence_is_spoken_by_the_worker_and_the_model_says_nothing(agent, connect):
    """Eval round 3: the caller hears the server's read-back sentence word for word, once."""
    agent.start(call_id="call-1")
    agent.turn("call-1", "It's 101 Union Street, Brooklyn, one one two three one.")
    hangups, voice = Hangups(), Voice()
    tools = tools_for(connect("call-1"), hangups, voice)
    address = {"street": "101 Union St", "unit": "", "city": "Brooklyn", "zip": "11231"}
    result = asyncio.run(tools["read_back"]({"address": address, "phone": "caller_id"}, "ctx"))
    assert no_reply(result)
    assert len(voice.said) == 1 and "So that's one oh one Union St" in voice.said[0]


def test_a_move_is_spoken_once_by_the_worker_and_the_model_is_not_asked_to_reply(agent, connect,
                                                                                  scenarios):
    """A scripted test call after #135: the confirmation was spoken, then the model said "the
    reschedule ran without a confirmation message", because a tool returning None still asked
    livekit-agents for a reply."""
    maria = scenarios["repeatCallerOpenJob"]
    agent.start(caller_id=maria["phone"], call_id="call-1")
    agent.turn("call-1", "I need to move my visit to tomorrow.")
    day = agent.tool("find_slots", "call-1", job_type="no_heat")["data"]["days"][1]
    voice = Voice()
    tools = tools_for(connect("call-1"), voice=voice)
    args = {"booking_id": maria["jobId"], "slot": {"date": day["date"], "window": day["windows"][0]},
            "reason": "Can't be home today"}
    result = asyncio.run(tools["reschedule_appointment"](args, None))
    assert no_reply(result)
    assert len(voice.said) == 1 and voice.said[0].startswith("Done, I've moved it to")
    # The model still gets the result to read.
    assert json.loads(result.output)["ok"] is True


def test_the_worker_speaks_the_top_priority_line_first_and_the_model_still_replies(agent, connect):
    """PR #139: the line is spoken word for word, then the model offers the times."""
    agent.start(call_id="call-1")
    agent.turn("call-1", "No heat, and my 84-year-old mother lives with me.")
    voice = Voice()
    tools = tools_for(connect("call-1"), voice=voice)
    result = asyncio.run(tools["find_slots"]({"job_type": "no_heat"}, None))
    assert voice.said == ["I've marked this call top priority, so you get our soonest opening."]
    # A string result asks the session for the model's reply; its say no longer carries the line.
    assert isinstance(result, str)
    assert "top priority" not in json.loads(result).get("say", "")



def test_a_server_line_the_caller_just_heard_is_not_spoken_again(agent, connect):
    """Live call 2 (issue #149): the identity refusal was said by the model and then by the worker."""
    from prompts.assemble import SpokenReply

    call_id = "call-1"
    agent.start(call_id=call_id)
    lines = agent.turn(call_id, "I smell gas")["say"]
    voice = Voice()
    reply = SpokenReply()
    reply.heard(None, "I smell gas")
    reply.server_said(" ".join(SAFETY["sign_off"].split()))
    agent.say(call_id, lines)
    bridge = Bridge([], play=None, silent=lambda: True)
    built = build_tools(
        assemble.tool_specs(JOB_TYPES), connect(call_id), bridge, ToolLog(), Hangups(), voice,
        reply=reply,
    )
    tools = {tool.info.name: tool for tool in built}
    assert no_reply(asyncio.run(tools["end_call"]({"reason": "resolved"}, "ctx")))
    assert voice.said == []
