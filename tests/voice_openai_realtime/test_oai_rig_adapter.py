"""The OpenAI Realtime entrant's wiring, against the real tool server: the server reads each
caller line before a reply is asked for, function calls go to the tool server, safety text is
asked for word for word, and the line drops only when the server's end_call says so."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from evals.models import load_config
from tests.rig_kit import (
    FakeListener,
    FakeSocket,
    seeded,
    tool_app,
    tools_called,
    turns,
    world_for,
)
from voice.openai_realtime import rig_adapter as oai

CALLER = "(212) 555-0999"


@pytest.fixture(scope="module")
def seed_path(tmp_path_factory):
    return seeded(tmp_path_factory)


@pytest.fixture
def app(seed_path, tmp_path):
    return tool_app(seed_path, tmp_path)


def opened_line(app, call_id="rt-1"):
    entrant = oai.Entrant(SimpleNamespace(config=load_config(), env={"OPENAI_API_KEY": "k"}))
    sock = FakeSocket()

    async def connect(url, **kwargs):
        assert "gpt-realtime" in url and kwargs["additional_headers"]["Authorization"] == "Bearer k"
        return sock

    line = oai.Line(entrant, connect=connect)
    listener = FakeListener()

    async def go():
        await line.open(call_id, CALLER, world_for(app), listener)
        line.reader.cancel()

    asyncio.run(go())
    return line, sock, listener


def greeted(app, call_id="rt-1"):
    """A line whose greeting has finished, so nothing is under way."""
    line, sock, listener = opened_line(app, call_id)
    asyncio.run(line.handle({"type": "response.done", "response": {"output": [], "usage": {}}}))
    listener.done_count = 0
    return line, sock, listener


def test_the_session_has_our_prompt_and_tools_and_never_answers_on_its_own(app):
    _, sock, _ = opened_line(app)
    session = sock.of_type("session.update")[0]["session"]
    assert "Summit Air" in session["instructions"]
    assert {t["name"] for t in session["tools"]} >= {"check_service_area", "end_call"}
    detection = session["audio"]["input"]["turn_detection"]
    assert detection["create_response"] is False and detection["interrupt_response"] is False


def test_the_greeting_is_asked_for_word_for_word(app):
    _, sock, _ = opened_line(app)
    response = sock.of_type("response.create")[0]["response"]
    assert "thanks so much for calling Summit Air today!" in response["instructions"]
    assert response["tool_choice"] == "none"


def test_a_caller_line_reaches_the_server_before_a_reply_is_asked_for(app):
    line, sock, _ = greeted(app)
    sock.sent.clear()
    event = {"type": "conversation.item.input_audio_transcription.completed",
             "transcript": "My heat is out."}
    asyncio.run(line.handle(event))
    assert ("caller", "My heat is out.") in turns(app, "rt-1")
    assert sock.sent[-1] == {"type": "response.create", "response": {}}


def test_a_gas_smell_asks_for_the_signed_off_lines_exactly_with_no_tools(app):
    line, sock, _ = greeted(app)
    sock.sent.clear()
    event = {"type": "conversation.item.input_audio_transcription.completed",
             "transcript": "I smell gas in the kitchen."}
    asyncio.run(line.handle(event))
    response = sock.sent[-1]["response"]
    assert response["tool_choice"] == "none"
    assert "call 9-1-1" in response["instructions"].lower()
    assert response["instructions"].startswith("Say exactly")


def test_function_calls_go_to_the_tool_server_and_the_reply_continues(app):
    line, sock, listener = greeted(app)
    # The server checks only a ZIP the caller said (app/tools.py _zip_known).
    world_for(app).server.turn("rt-1", "caller", "I'm in 11231.")
    sock.sent.clear()
    done = {"type": "response.done", "response": {"output": [
        {"type": "function_call", "name": "check_service_area", "call_id": "fc_1",
         "arguments": json.dumps({"zip": "11231"})}], "usage": {}}}
    asyncio.run(line.handle(done))
    assert tools_called(app, "rt-1") == ["check_service_area"]
    output = sock.of_type("conversation.item.create")[0]["item"]
    assert output["type"] == "function_call_output" and output["call_id"] == "fc_1"
    assert json.loads(output["output"])["ok"] is True
    assert sock.sent[-1]["type"] == "response.create"
    assert listener.done_count == 0  # the agent hasn't finished its turn yet


def test_a_finished_reply_posts_the_agent_line_and_hands_the_turn_back(app):
    line, _, listener = greeted(app)
    asyncio.run(line.handle({"type": "response.output_audio_transcript.done",
                             "transcript": "What's your ZIP code?"}))
    asyncio.run(line.handle({"type": "response.done", "response": {"output": [], "usage": {}}}))
    assert ("agent", "What's your ZIP code?") in turns(app, "rt-1")
    assert listener.said_lines == ["What's your ZIP code?"] and listener.done_count == 1


def test_end_call_on_a_safety_call_is_refused_without_asking_for_the_lines_again(app):
    line, sock, _ = greeted(app)
    asyncio.run(line.handle({"type": "conversation.item.input_audio_transcription.completed",
                             "transcript": "I smell gas."}))
    sock.sent.clear()
    done = {"type": "response.done", "response": {"output": [
        {"type": "function_call", "name": "end_call", "call_id": "fc_2",
         "arguments": json.dumps({"reason": "resolved"})}], "usage": {}}}
    asyncio.run(line.handle(done))
    assert line.hang_up_after is False
    # The lines were asked for when the caller spoke; the refusal doesn't ask for them again.
    assert not str(sock.sent).count("Say exactly")


def test_a_caller_line_during_a_reply_cuts_it_short_and_waits_for_it(app):
    line, sock, _ = opened_line(app)
    asyncio.run(line.handle({"type": "response.done", "response": {"output": [], "usage": {}}}))
    asyncio.run(line.caller_line("My heat is out."))  # a reply is now under way
    sock.sent.clear()
    asyncio.run(line.caller_line("Actually, it's the boiler."))
    assert [m["type"] for m in sock.sent] == ["response.cancel"]
    cancelled = {"type": "response.done", "response": {"status": "cancelled", "usage": {},
                                                       "output": []}}
    asyncio.run(line.handle(cancelled))
    assert sock.sent[-1] == {"type": "response.create", "response": {}}


def test_fixed_text_is_not_cut_short_by_a_caller_line(app):
    line, sock, _ = opened_line(app)  # the greeting, fixed text, is under way
    sock.sent.clear()
    asyncio.run(line.caller_line("Hello?"))
    assert sock.sent == []
    asyncio.run(line.handle({"type": "response.done", "response": {"output": [], "usage": {}}}))
    assert sock.sent[-1] == {"type": "response.create", "response": {}}


def test_writes_carry_an_idempotency_key(app):
    line, _, _ = opened_line(app)
    calls = []
    line.server = SimpleNamespace(tool=lambda *args: calls.append(args) or {"ok": True})
    asyncio.run(line.tool("add_note", {"text": "Gate code 12", "attach_to": "customer"}))
    asyncio.run(line.tool("check_service_area", {"zip": "11231"}))
    assert calls[0][3] and calls[1][3] is None


def test_an_unknown_tool_is_refused_without_reaching_the_server(app):
    line, _, _ = opened_line(app)
    assert asyncio.run(line.tool("place_call", {}))["refused"] == "unknown_tool"


def test_cost_counts_cached_input_at_the_cached_rate():
    price = load_config()["realtime"]["openai"]["price"]
    usage = {"input_token_details": {"text_tokens": 1000, "audio_tokens": 100, "cached_tokens": 800,
                                     "cached_tokens_details": {"text_tokens": 800}},
             "output_token_details": {"audio_tokens": 200, "text_tokens": 50}}
    expected = (200 * price["text_input"] + 100 * price["audio_input"] + 800 * price["cached_input"]
                + 50 * price["text_output"] + 200 * price["audio_output"]) / 1e6
    assert oai.usage_cost(usage, price) == pytest.approx(expected)
