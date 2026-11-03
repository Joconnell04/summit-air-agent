"""The model's tools go through the tool server, and only the server decides (docs/tools.md)."""

import asyncio
import json

import httpx
from livekit.agents.beta.tools.end_call import EndCallTool

from app.tools import TOOLS
from prompts import assemble
from tests.tool_server.toolkit import DIALED, in_area_address
from voice.livekit.bridge import Bridge
from voice.livekit.server import UNREACHABLE, ToolServer, idempotency_key
from voice.livekit.tools import ToolLog, build_tools

JOB_TYPES = ["no_heat", "no_cooling", "tune_up", "safety_check", "estimate"]


class Hangups:
    def __init__(self):
        self.calls = []

    def __call__(self, context):
        self.calls.append(context)


def tools_for(server: ToolServer, hang_up=None) -> dict:
    bridge = Bridge([], play=None, silent=lambda: True)
    built = build_tools(
        assemble.tool_specs(JOB_TYPES), server, bridge, ToolLog(), hang_up or Hangups()
    )
    return {tool.info.name: tool for tool in built}


def test_the_model_gets_exactly_the_servers_tools_and_no_built_in_end_call(connect):
    tools = tools_for(connect("call-1"))
    assert set(tools) == set(TOOLS)
    assert not any(isinstance(t, EndCallTool) for t in tools.values())
    assert "transfer_call" not in tools


def test_a_tool_call_posts_to_the_server_with_the_call_id(agent, connect):
    agent.start(call_id="call-1")
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


def test_end_call_hangs_up_only_when_the_server_agrees(agent, connect):
    call_id = "call-1"
    agent.start(call_id=call_id)
    agent.turn(call_id, "I smell gas in the kitchen")
    hangups = Hangups()
    tools = tools_for(connect(call_id), hangups)
    refused = json.loads(asyncio.run(tools["end_call"]({"reason": "resolved"}, "ctx")))
    assert refused["ok"] is False and refused["refused"] == "gate_safety"
    assert hangups.calls == []


def test_end_call_on_a_safety_call_signs_off_without_hanging_up(agent, connect):
    call_id = "call-1"
    lines = agent.start(call_id=call_id) and agent.turn(call_id, "I smell gas")["say"]
    agent.say(call_id, lines)
    hangups = Hangups()
    tools = tools_for(connect(call_id), hangups)
    result = json.loads(asyncio.run(tools["end_call"]({"reason": "resolved"}, "ctx")))
    assert result["ok"] is True and "911" in result["say"]
    assert hangups.calls == []


def test_end_call_that_the_server_allows_hangs_up_and_ends_the_turn(agent, connect):
    agent.start(call_id="call-1")
    hangups = Hangups()
    tools = tools_for(connect("call-1"), hangups)
    assert asyncio.run(tools["end_call"]({"reason": "wrong_number"}, "ctx")) is None
    assert hangups.calls == ["ctx"]


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
    tools = {t.info.name: t for t in build_tools(specs, connect("call-1"), bridge, log, Hangups())}
    asyncio.run(tools["read_back"]({"address": in_area_address(), "phone": "caller_id"}, None))
    ms, played = log.take()
    assert ms > 0 and played is False
    assert log.take() == (0.0, False)
