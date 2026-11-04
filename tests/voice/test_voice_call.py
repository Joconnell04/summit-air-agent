"""One call's lines, posted in order to the real tool server; safety lines spoken from the server's
text, never generated (spec S-17)."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import yaml
from livekit.agents import Agent, StopResponse
from livekit.agents.llm import ChatMessage

from app.tenant import load_agent
from app.trace import as_text, timeline
from prompts.assemble import greeting
from tests.tool_server.toolkit import TENANT_DIR
from voice.livekit.bridge import Bridge
from voice.livekit.call import Call, SummitAgent
from voice.livekit.server import ToolServer
from voice.livekit.settings import load_voice
from voice.livekit.tools import ToolLog
from voice.livekit.worker import leave_voicemail

SAFETY = load_agent(TENANT_DIR)[0]["safety"]
GAS_LINES = " ".join(SAFETY["kinds"]["gas"]["lines"].split())
ENDPOINTING = load_voice()["endpointing"]


class Session:
    """Stands in for the LiveKit session: records what the worker asked it to do."""

    def __init__(self):
        self.said: list[tuple[str, bool]] = []
        self.endpointing: list[dict] = []

    def say(self, text, allow_interruptions=True):
        self.said.append((text, allow_interruptions))

    def update_options(self, endpointing_opts):
        self.endpointing.append(endpointing_opts)


def make_call(connect, call_id="call-1") -> Call:
    bridge = Bridge([], play=None, silent=lambda: True)
    call = Call(connect(call_id), bridge, ToolLog(), ENDPOINTING)
    call.session = Session()
    return call


def caller(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=[text])


def agent_says(text: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=[text])


def test_a_gas_smell_makes_the_worker_say_the_servers_lines_word_for_word(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    with pytest.raises(StopResponse):
        asyncio.run(
            call.caller_line(caller("It kind of smells like gas in here, and the heat's out"))
        )
    assert call.session.said == [(GAS_LINES, False)]


def test_the_lines_count_as_said_once_the_agent_line_is_posted(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    with pytest.raises(StopResponse):
        asyncio.run(call.caller_line(caller("I smell gas")))

    async def spoken():
        call.agent_line(agent_says(call.session.said[0][0]))
        await call.flush()

    asyncio.run(spoken())
    conn = agent.db()
    state = conn.execute("SELECT safety_state FROM calls WHERE id = 'call-1'").fetchone()[0]
    conn.close()
    assert state == "lines_said"


def test_an_ordinary_line_goes_to_the_model(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    message = caller("My furnace is making a banging noise, and we have a gas furnace.")
    asyncio.run(call.caller_line(message))
    assert call.session.said == []
    assert message.text_content.startswith("My furnace")


def test_a_business_caller_gets_a_server_note_for_the_model(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    message = caller("Hi, I run a restaurant in Park Slope and our AC is out.")
    asyncio.run(call.caller_line(message))
    assert "[server]" in message.text_content and "commercial" in message.text_content


def test_lines_are_posted_in_the_order_they_happened(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)

    async def conversation():
        call.agent_line(agent_says("Thanks for calling, how can I help?"))
        await call.caller_line(caller("The heat's out."))
        call.agent_line(agent_says("Oh no. When did it stop?"))
        await call.caller_line(caller("Last night."))
        await call.flush()

    asyncio.run(conversation())
    conn = agent.db()
    rows = conn.execute(
        "SELECT speaker FROM turns WHERE call_id = 'call-1' ORDER BY seq"
    ).fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["agent", "caller", "agent", "caller"]


def test_the_wait_is_longer_right_after_asking_for_a_number(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)

    async def run():
        call.agent_line(agent_says("What's the best number to reach you?"))
        call.agent_line(agent_says("Got it. And what's going on with the heat?"))
        await call.flush()

    asyncio.run(run())
    assert call.session.endpointing == [ENDPOINTING["digits"], ENDPOINTING["normal"]]
    assert ENDPOINTING["digits"]["min_delay"] > ENDPOINTING["normal"]["min_delay"]


def test_every_agent_line_carries_its_stage_timings(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    call.caller_stopped_at = 100.0
    call.tool_log.ms = 42.0
    message = agent_says("I've got Tuesday morning open.")
    message.metrics = {"started_speaking_at": 101.1, "llm_node_ttft": 0.5, "tts_node_ttfb": 0.15}

    async def run():
        call.agent_line(message)
        await call.flush()

    asyncio.run(run())
    conn = agent.db()
    timings = conn.execute(
        "SELECT timings_json FROM turns WHERE call_id = 'call-1' AND speaker = 'agent'"
    ).fetchone()[0]
    conn.close()
    assert '"reply_ms": 1100.0' in timings and '"tool_ms": 42.0' in timings


def test_the_trace_shows_whether_the_prompt_cache_was_hit(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    call.llm_log.record(
        SimpleNamespace(
            type="llm_metrics",
            cancelled=False,
            prompt_tokens=9400,
            prompt_cached_tokens=9300,
            cache_creation_tokens=40,
            completion_tokens=30,
        )
    )

    async def run():
        call.agent_line(agent_says("I've got Tuesday morning open."))
        await call.flush()

    asyncio.run(run())
    conn = agent.db()
    timings = json.loads(
        conn.execute(
            "SELECT timings_json FROM turns WHERE call_id = 'call-1' AND speaker = 'agent'"
        ).fetchone()[0]
    )
    conn.close()
    assert timings["cache_read_tokens"] == 9300 and timings["cache_write_tokens"] == 40
    assert "cache_read_tokens" in as_text(timeline(agent.db(), "call-1"))


def test_the_greeting_names_a_single_matched_customer(scenarios, agent):
    started = agent.start(scenarios["repeatCallerOpenJob"]["phone"])["data"]
    lines = yaml.safe_load((TENANT_DIR / "agent.yaml").read_text())["voice"]
    first = started["customers"][0]["name"].split()[0]
    assert greeting(started, lines) == lines["greeting_known"].format(first_name=first)
    assert greeting(agent.start()["data"], lines) == lines["greeting_unknown"]


def test_a_voicemail_at_capacity_flags_a_person_to_call_back(agent, connect):
    agent.start(call_id="call-1")
    result = asyncio.run(
        leave_voicemail(connect("call-1"), "Hi it's Dana at 12 Union St, no heat, call me back", 60)
    )
    assert result["ok"] is True, result
    conn = agent.db()
    reason = conn.execute("SELECT reason FROM flags WHERE call_id = 'call-1'").fetchone()[0]
    conn.close()
    assert reason.startswith("Voicemail while every line was busy: Hi it's Dana")


def test_a_server_that_stops_answering_mid_call_ends_the_call_with_the_safety_line():
    def down(request):
        raise httpx.ConnectError("refused")

    server = ToolServer("http://tool-server", "call-1", transport=httpx.MockTransport(down))
    call = Call(server, Bridge([], play=None, silent=lambda: True), ToolLog(), ENDPOINTING)
    call.session = Session()
    ended = []
    call.server_down = lambda: ended.append(True)
    with pytest.raises(StopResponse):
        asyncio.run(call.caller_line(caller("I smell gas")))
    assert ended == [True]


def test_the_reply_audio_waits_for_a_bridge_clip_to_finish(monkeypatch):
    events = []

    async def clip(_path):
        events.append("clip starts")
        await asyncio.sleep(0.1)
        events.append("clip ends")

    async def fake_tts(_agent, _text, _settings):
        for n in range(2):
            yield f"frame {n}"

    monkeypatch.setattr(Agent.default, "tts_node", fake_tts)

    async def run():
        bridge = Bridge(["a"], clip, lambda: True, after_s=0.01)
        call = Call(None, bridge, ToolLog(), ENDPOINTING)
        agent = SummitAgent(call, "", [])

        async def slow_tool():
            await asyncio.sleep(0.03)

        await bridge.run(slow_tool())
        async for frame in agent.tts_node(None, None):
            events.append(frame)

    asyncio.run(run())
    assert events == ["clip starts", "clip ends", "frame 0", "frame 1"]


def test_the_worker_stops_the_recording_when_the_caller_objects(agent, connect):
    """Two-party consent on: the server reads the objection and the worker stops the recorder."""
    from app import owner_settings

    conn = agent.db()
    try:
        tenant = agent.app.state.services.tenants.default
        owner_settings.change(conn, tenant, {"two_party_consent": True}, "Pat", agent.clock())
    finally:
        conn.close()
    agent.start(call_id="call-1")
    call = make_call(connect)
    stopped = []

    async def stop() -> None:
        stopped.append(True)

    call.stop_recording = stop

    async def run(text: str) -> None:
        await call.caller_line(caller(text))
        await asyncio.sleep(0)

    asyncio.run(run("Please don't record this. The AC is out."))
    assert stopped == [True]
    assert call.session.said == [("Okay, I've turned the recording off.", True)]
    asyncio.run(run("The AC is still out."))
    assert stopped == [True], "once"
    assert len(call.session.said) == 1
