"""The worker speaks at the pace the server sets (app/pace.py) and decides none itself: the call's
speed comes from start and turn results, a slower read-back is spoken in the server's parts, and
each agent line reports the speed it was spoken at."""

import asyncio
import json

from livekit.agents import Agent
from livekit.agents.llm import ChatMessage

from prompts import assemble
from tests.tool_server.toolkit import in_area_address
from tests.voice.test_voice_call import make_call
from tests.voice.test_voice_tools import JOB_TYPES, Hangups, Voice
from voice.livekit.bridge import Bridge
from voice.livekit.call import SummitAgent
from voice.livekit.tools import ToolLog, build_tools


def caller(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=[text])


def spoken(text: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=[text])


def agent_timings(agent, call_id: str) -> list[tuple[str, dict]]:
    conn = agent.db()
    rows = conn.execute(
        "SELECT text, timings_json FROM turns WHERE call_id = ? AND speaker = 'agent' ORDER BY seq",
        (call_id,),
    ).fetchall()
    conn.close()
    return [(r["text"], json.loads(r["timings_json"])) for r in rows]


def test_the_call_takes_its_speed_from_the_server_and_none_without_one(agent, connect):
    started = agent.start(call_id="call-1")
    call = make_call(connect)
    assert call.speed is None
    call.apply_pace(started["data"])
    assert call.speed == 1.0
    asyncio.run(call.caller_line(caller("Could you slow down a little?")))
    assert call.speed == 0.85
    call.apply_pace({})
    assert call.speed == 0.85


def test_each_agent_line_reports_the_speed_it_was_spoken_at(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    call.speed = 1.0
    assert call.speed_for("Sure, I'll") == 1.0
    asyncio.run(call.caller_line(caller("Slow down please.")))
    assert call.speed_for("And what's") == 0.85

    async def lines():
        call.agent_line(spoken("Sure, I'll slow down."))
        call.agent_line(spoken("And what's the ZIP code?"))
        await call.flush()

    asyncio.run(lines())
    assert [t["speed"] for _, t in agent_timings(agent, "call-1")] == [1.0, 0.85]


def test_a_slower_read_back_is_spoken_in_the_servers_parts_at_their_speeds(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    call.speed = 1.0
    parts = [
        {"text": "Let me read that back: your home at", "speed": 1.0},
        {"text": "101 Union St, Brooklyn, 11231,", "speed": 0.85},
        {"text": "and the best number is 212, 555, 0999. Is that right?", "speed": 1.0},
    ]
    call.say_parts(parts)
    assert [text for text, _ in call.session.said] == [p["text"] for p in parts]
    # Synthesis asks for each part's speed in turn; anything else gets the call's.
    assert [call.speed_for(p["text"]) for p in parts] == [1.0, 0.85, 1.0]
    assert call.speed_for("Great, booking that now.") == 1.0


def test_the_read_back_tool_hands_the_parts_to_the_worker_and_the_model_says_nothing(
    agent, connect
):
    agent.start(call_id="call-1")
    agent.turn("call-1", "It's 101 Union St, 11231.")
    server = connect("call-1")
    address = in_area_address()
    asked = agent.tool("read_back", "call-1", address=address, phone="caller_id")
    agent.say("call-1", asked["say"])
    agent.turn("call-1", "Sorry, can you repeat that slower?")
    heard = []
    tools = {
        t.info.name: t
        for t in build_tools(
            assemble.tool_specs(JOB_TYPES),
            server,
            Bridge([], play=None, silent=lambda: True),
            ToolLog(),
            Hangups(),
            Voice(),
            say_parts=lambda parts: heard.append(parts) or "handle",
        )
    }
    args = {"address": address, "phone": "caller_id"}
    assert asyncio.run(tools["read_back"](args, None)) is None
    assert [p["speed"] for p in heard[0]] == [1.0, 0.85, 1.0]
    # The next read-back is whole again, spoken by the worker as it is (eval round 4).
    assert asyncio.run(tools["read_back"](args, None)) is None
    assert len(heard) == 1


def test_the_speed_is_set_on_the_voice_before_each_speech_is_synthesized(monkeypatch):
    class Tts:
        def __init__(self):
            self.speeds = []

        def update_options(self, speed):
            self.speeds.append(speed)

    class Session:
        tts = Tts()

    seen = []

    async def default_tts_node(agent, text, settings):
        async for chunk in text:
            seen.append((chunk, list(Session.tts.speeds)))
        yield "frame"

    class Call:
        bridge = Bridge([], play=None, silent=lambda: True)

        def speed_for(self, first):
            return {"101 Union St,": 0.85}.get(first.strip(), 1.0)

    monkeypatch.setattr(Agent.default, "tts_node", default_tts_node)
    monkeypatch.setattr(SummitAgent, "session", property(lambda self: Session()))
    agent = SummitAgent(Call(), "", [])

    async def text(*chunks):
        for chunk in chunks:
            yield chunk

    async def run(*chunks):
        return [f async for f in agent.tts_node(text(*chunks), None)]

    assert asyncio.run(run("", "Sure", ", I'll slow down.")) == ["frame"]
    assert asyncio.run(run("101 Union St,")) == ["frame"]
    assert Session.tts.speeds == [1.0, 0.85]
    # The speed was set before the first words reached the voice, and no words were lost.
    assert seen == [("Sure", [1.0]), (", I'll slow down.", [1.0]), ("101 Union St,", [1.0, 0.85])]


def test_the_worker_keeps_any_speed_inside_what_the_voice_accepts(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    call.apply_pace({"pace": {"speed": 3.0}})
    assert call.speed == 2.0
    call.apply_pace({"pace": {"speed": 0.2}})
    assert call.speed == 0.6
    call.say_parts([{"text": "101 Union St,", "speed": 0.1}])
    assert call.speed_for("101 Union St,") == 0.6


def test_read_back_parts_never_spoken_are_logged_and_forgotten(agent, connect, caplog):
    agent.start(call_id="call-1")
    call = make_call(connect)
    call.speed = 1.0
    call.say_parts([{"text": "Your home at", "speed": 1.0}, {"text": "101 Union St,", "speed": 0.85}])
    assert call.speed_for("Your home at") == 1.0
    # The caller cut in before the address was synthesized.
    asyncio.run(call.caller_line(caller("Wait, it's 103.")))
    assert "read-back parts never spoken" in caplog.text
    assert call.planned == {}
    assert call.speed_for("101 Union St,") == 1.0
