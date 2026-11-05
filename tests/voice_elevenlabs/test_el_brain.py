"""Our brain behind ElevenLabs' custom-LLM mode, against the real tool server over HTTP and a
scripted model: every caller line goes through the turn check, safety text is streamed as given,
tools are forwarded, and ElevenLabs is told to hang up only after the server agreed."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.rig_kit import seeded, tool_app, tools_called, turns
from voice.elevenlabs import rig_adapter as el
from voice.elevenlabs.agent import agent_body
from voice.elevenlabs.brain import Brain, build_app, call_id_of, caller_words
from voice.elevenlabs.server import ToolServer

CALLER = "(212) 555-0999"
TOKEN = "path-token"


class Block(SimpleNamespace):
    def model_dump(self, exclude_none=True):
        return {k: v for k, v in vars(self).items() if v is not None}


def text(t: str):
    return {"text": t}


def tool(name: str, **args):
    return {"tool": (name, args)}


class FakeStream:
    def __init__(self, reply: list[dict]):
        self.reply = reply

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def events():
            for part in self.reply:
                if "text" in part:
                    delta = SimpleNamespace(type="text_delta", text=part["text"])
                    yield SimpleNamespace(type="content_block_delta", delta=delta)

        return events()

    async def get_final_message(self):
        content = []
        for i, part in enumerate(self.reply):
            if "text" in part:
                content.append(Block(type="text", text=part["text"]))
            else:
                name, args = part["tool"]
                content.append(Block(type="tool_use", id=f"tu_{i}", name=name, input=args))
        usage = SimpleNamespace(input_tokens=100, output_tokens=10, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(content=content, usage=usage)


class FakeClaude:
    """Replies in order, one list of parts per model request."""

    def __init__(self, replies: list[list[dict]]):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.messages = self

    def stream(self, **params):
        self.requests.append({**params, "messages": list(params["messages"])})
        return FakeStream(self.replies.pop(0) if self.replies else [text("Okay.")])


@pytest.fixture(scope="module")
def seed_path(tmp_path_factory):
    return seeded(tmp_path_factory)


@pytest.fixture
def app(seed_path, tmp_path):
    return tool_app(seed_path, tmp_path)


def brain_for(app, replies):
    server = ToolServer("http://tool-server", transport=httpx.ASGITransport(app=app))
    claude = FakeClaude(replies)
    return Brain(server, client=claude), claude


def completions(brain, call_id: str, said: str) -> list[dict]:
    """Post one request as ElevenLabs does and return the parsed chunks."""
    client = TestClient(build_app(brain, TOKEN))
    body = {"model": "x", "stream": True, "elevenlabs_extra_body": {"summit_call_id": call_id},
            "messages": [{"role": "system", "content": "prompt"},
                         {"role": "assistant", "content": "Hi."},
                         {"role": "user", "content": said}]}
    response = client.post(f"/{TOKEN}/v1/chat/completions", json=body)
    assert response.status_code == 200
    events = [line.removeprefix("data: ") for line in response.text.split("\n\n") if line]
    assert events[-1] == "[DONE]"
    return [json.loads(e) for e in events[:-1]]


def spoken(chunks) -> str:
    return "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)


def end_calls(chunks) -> list[dict]:
    return [t for c in chunks for t in c["choices"][0]["delta"].get("tool_calls", [])]


def start(brain, call_id="el-1") -> str:
    client = TestClient(build_app(brain, TOKEN))
    response = client.post(f"/{TOKEN}/calls", json={"call_id": call_id, "caller_id": CALLER})
    assert response.status_code == 200
    return response.json()["first_message"]


def test_the_first_message_is_the_tenants_greeting_and_is_on_the_record(app):
    brain, _ = brain_for(app, [])
    first = start(brain)
    assert first.startswith("Hi there, thanks so much for calling Summit Air today!")
    assert turns(app, "el-1") == [("agent", first)]


def test_a_plain_reply_streams_the_model_text_and_both_lines_are_recorded(app):
    brain, claude = brain_for(app, [[text("Sorry to hear that. "), text("What's your ZIP?")]])
    start(brain)
    chunks = completions(brain, "el-1", "My heat is out.")
    assert spoken(chunks) == "Sorry to hear that. What's your ZIP?"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop" and not end_calls(chunks)
    assert turns(app, "el-1")[-2:] == [("caller", "My heat is out."),
                                       ("agent", "Sorry to hear that. What's your ZIP?")]
    assert claude.requests[0]["thinking"] == {"type": "disabled"}


def test_the_voice_hears_the_read_back_spelled_out_and_the_server_keeps_it_as_written(app):
    written = "Your home at 801 Fenimore St, Brooklyn, 11206. Is that right?"
    brain, _ = brain_for(app, [[text(written[:20]), text(written[20:])]])
    start(brain)
    chunks = completions(brain, "el-1", "Yes, book it.")
    assert spoken(chunks) == "Your home at 801 Fenimore Street, Brooklyn, 1 1 2 0 6. Is that right?"
    assert turns(app, "el-1")[-1] == ("agent", written)


def test_the_safety_lines_are_streamed_as_given_without_the_model(app):
    brain, claude = brain_for(app, [])
    start(brain)
    chunks = completions(brain, "el-1", "I smell gas in the kitchen.")
    said = spoken(chunks)
    assert "9-1-1" in said and claude.requests == []
    assert turns(app, "el-1")[-1] == ("agent", said)


def test_a_tool_call_goes_to_the_server_and_its_result_follows_it_in_the_history(app):
    brain, claude = brain_for(app, [[tool("check_service_area", zip="11231")],
                                    [text("We cover your area.")]])
    start(brain)
    chunks = completions(brain, "el-1", "I'm in 11231.")
    assert spoken(chunks).strip() == "We cover your area."
    assert tools_called(app, "el-1") == ["check_service_area"]
    history = claude.requests[1]["messages"]
    assert history[-2]["content"][0]["type"] == "tool_use"
    assert history[-1]["content"][0]["type"] == "tool_result"


def test_elevenlabs_is_told_to_hang_up_only_after_the_server_agreed(app):
    brain, _ = brain_for(app, [[text("Thanks, bye."), tool("end_call", reason="caller_goodbye")]])
    start(brain)
    chunks = completions(brain, "el-1", "That's all, bye.")
    (call,) = end_calls(chunks)
    assert call["function"]["name"] == "end_call"
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_on_a_safety_call_end_call_is_refused_and_no_hang_up_is_sent(app):
    brain, _ = brain_for(app, [[tool("end_call", reason="resolved")]])
    start(brain)
    asyncio.run(brain.server.turn("el-1", "caller", "I smell gas."))
    chunks = completions(brain, "el-1", "Okay, bye then.")
    assert not end_calls(chunks)
    assert "9-1-1" in spoken(chunks)


def test_a_dropped_request_still_finishes_its_turn(app):
    brain, claude = brain_for(app, [[text("Let me check. "), tool("check_service_area", zip="11231")],
                                    [text("You're covered.")]])
    start(brain)

    async def drop_after_first_piece():
        pieces = brain.reply("el-1", "I'm in 11231.")
        await pieces.__anext__()
        await pieces.aclose()
        await asyncio.gather(*brain._running)

    asyncio.run(drop_after_first_piece())
    assert tools_called(app, "el-1") == ["check_service_area"]
    assert claude.requests[1]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_requests_for_another_path_or_call_are_refused(app):
    brain, _ = brain_for(app, [])
    client = TestClient(build_app(brain, TOKEN))
    assert client.post("/wrong/v1/chat/completions", json={}).status_code == 404
    body = {"messages": [], "elevenlabs_extra_body": {"summit_call_id": "nobody"}}
    assert client.post(f"/{TOKEN}/v1/chat/completions", json=body).status_code == 404


def test_the_callers_words_are_the_user_lines_since_the_agent_spoke():
    messages = [{"role": "assistant", "content": "Hi."}, {"role": "user", "content": "My heat"},
                {"role": "user", "content": [{"type": "text", "text": "is out."}]}]
    assert caller_words(messages) == "My heat is out."
    assert caller_words(messages[:1]) == ""


def test_the_call_id_comes_from_the_extra_body_or_the_agent_prompt():
    assert call_id_of({"elevenlabs_extra_body": {"summit_call_id": "a"}}) == "a"
    prompt = {"messages": [{"role": "system", "content": "Summit Air call. summit_call_id=b"}]}
    assert call_id_of(prompt) == "b"


def test_the_test_agent_runs_our_endpoint_with_only_the_end_call_tool():
    cfg = {"tts_model": "eleven_flash_v2", "voice_id": "v"}
    body = agent_body("https://x/t/v1", "claude-haiku-5-5", cfg)
    prompt = body["conversation_config"]["agent"]["prompt"]
    assert prompt["llm"] == "custom-llm" and prompt["custom_llm"]["url"] == "https://x/t/v1"
    assert list(prompt["built_in_tools"]) == ["end_call"]
    assert "temporary" in body["name"]


def test_the_socket_answers_pings_and_passes_audio_and_text_on():
    from tests.rig_kit import FakeListener

    listener, state = FakeListener(), {}
    ping = {"type": "ping", "ping_event": {"event_id": 7, "ping_ms": 40}}
    assert el.handle(ping, listener, state) == {"type": "pong", "event_id": 7}
    meta = {"type": "conversation_initiation_metadata",
            "conversation_initiation_metadata_event": {"conversation_id": "conv_1"}}
    el.handle(meta, listener, state)
    audio = {"type": "audio", "audio_event": {"audio_base_64": "AAAA"}}
    assert el.handle(audio, listener, state) is None
    el.handle({"type": "agent_response", "agent_response_event": {"agent_response": "Hi."}},
              listener, state)
    assert state["conversation_id"] == "conv_1"
    assert listener.audio_bytes == 3 and listener.said_lines == ["Hi."]
    first = el.initiation("call-9", "Hello.")
    assert first["custom_llm_extra_body"] == {"summit_call_id": "call-9"}
    assert first["conversation_config_override"]["agent"]["first_message"] == "Hello."
