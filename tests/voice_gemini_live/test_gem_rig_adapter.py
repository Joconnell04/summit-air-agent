"""The Gemini Live entrant's wiring, against the real tool server: the caller's words reach the
turn check when the reply starts, function calls go to the tool server and back as tool responses,
safety text interrupts with the exact words, and a finished turn posts the agent's line."""

import asyncio
import base64
from types import SimpleNamespace

import pytest

from evals.models import load_config
from tests.rig_kit import FakeListener, FakeSocket, seeded, tool_app, tools_called, turns, world_for
from voice.archive.gemini_live import rig_adapter as gem

CALLER = "(212) 555-0999"
AUDIO = {"serverContent": {"modelTurn": {"parts": [{"inlineData": {
    "mimeType": "audio/pcm;rate=24000", "data": base64.b64encode(b"\1\0" * 480).decode()}}]}}}


@pytest.fixture(scope="module")
def seed_path(tmp_path_factory):
    return seeded(tmp_path_factory)


@pytest.fixture
def app(seed_path, tmp_path):
    return tool_app(seed_path, tmp_path)


def opened_line(app, call_id="gl-1"):
    entrant = gem.Entrant(SimpleNamespace(config=load_config(), env={"GEMINI_API_KEY": "k"}))
    sock = FakeSocket([{"setupComplete": {}}])

    async def connect(url, **kwargs):
        assert url.endswith("key=k")
        return sock

    line = gem.Line(entrant, connect=connect)
    listener = FakeListener()

    async def go():
        await line.open(call_id, CALLER, world_for(app), listener)
        line.reader.cancel()

    asyncio.run(go())
    return line, sock, listener


def texts(sock) -> list[str]:
    return [m["clientContent"]["turns"][0]["parts"][0]["text"] for m in sock.sent
            if "clientContent" in m]


def test_setup_carries_our_prompt_tools_and_transcription(app):
    _, sock, _ = opened_line(app)
    setup = sock.sent[0]["setup"]
    assert setup["model"] == f"models/{load_config()['realtime']['gemini']['model']}"
    assert "Summit Air" in setup["systemInstruction"]["parts"][0]["text"]
    names = {f["name"] for f in setup["tools"][0]["functionDeclarations"]}
    assert {"check_service_area", "end_call"} <= names
    assert "inputAudioTranscription" in setup and "outputAudioTranscription" in setup


def test_the_greeting_is_asked_for_word_for_word(app):
    _, sock, _ = opened_line(app)
    assert texts(sock)[0].startswith("Say exactly") and "Summit Air" in texts(sock)[0]


def test_the_callers_words_are_posted_once_the_reply_starts(app):
    line, _, listener = opened_line(app)
    asyncio.run(line.handle({"serverContent": {"inputTranscription": {"text": "My heat "}}}))
    asyncio.run(line.handle({"serverContent": {"inputTranscription": {"text": "is out."}}}))
    assert ("caller", "My heat is out.") not in turns(app, "gl-1")
    asyncio.run(line.handle(AUDIO))
    assert ("caller", "My heat is out.") in turns(app, "gl-1")
    assert listener.audio_bytes == 960


def test_a_gas_smell_interrupts_with_the_signed_off_lines(app):
    line, sock, _ = opened_line(app)
    asyncio.run(line.handle({"serverContent": {"inputTranscription": {"text": "I smell gas."}}}))
    asyncio.run(line.handle(AUDIO))
    assert texts(sock)[-1].startswith("Say exactly") and "9-1-1" in texts(sock)[-1]


def test_function_calls_go_to_the_tool_server_and_back(app):
    line, sock, _ = opened_line(app)
    # The server checks only a ZIP the caller said (app/tools.py _zip_known).
    world_for(app).server.turn("gl-1", "caller", "I'm in 11231.")
    call = {"toolCall": {"functionCalls": [
        {"id": "f1", "name": "check_service_area", "args": {"zip": "11231"}}]}}
    asyncio.run(line.handle(call))
    assert tools_called(app, "gl-1") == ["check_service_area"]
    (response,) = sock.sent[-1]["toolResponse"]["functionResponses"]
    assert response["id"] == "f1" and response["response"]["ok"] is True


def test_end_call_hangs_up_only_when_the_server_says_so(app):
    line, sock, _ = opened_line(app)
    asyncio.run(line.handle({"serverContent": {"inputTranscription": {"text": "I smell gas."}}}))
    asyncio.run(line.handle(AUDIO))
    end = {"toolCall": {"functionCalls": [{"id": "f2", "name": "end_call",
                                           "args": {"reason": "resolved"}}]}}
    asyncio.run(line.handle(end))
    assert line.hang_up_after is False
    assert texts(sock)[-1].startswith("Say exactly")


def test_a_finished_turn_posts_the_agent_line_and_hands_the_turn_back(app):
    line, _, listener = opened_line(app)
    asyncio.run(line.handle({"serverContent": {"outputTranscription": {"text": "What's your "}}}))
    asyncio.run(line.handle({"serverContent": {"outputTranscription": {"text": "ZIP code?"}}}))
    asyncio.run(line.handle({"serverContent": {"turnComplete": True}}))
    assert ("agent", "What's your ZIP code?") in turns(app, "gl-1")
    assert listener.done_count == 1


def test_cost_reads_tokens_by_modality():
    price = load_config()["realtime"]["gemini"]["price"]
    usage = {"promptTokensDetails": [{"modality": "TEXT", "tokenCount": 1000},
                                     {"modality": "AUDIO", "tokenCount": 500}],
             "responseTokensDetails": [{"modality": "AUDIO", "tokenCount": 200}]}
    expected = (1000 * price["text_input"] + 500 * price["audio_input"]
                + 200 * price["audio_output"]) / 1e6
    assert gem.usage_cost(usage, price) == pytest.approx(expected)
