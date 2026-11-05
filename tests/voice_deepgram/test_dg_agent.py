"""The Deepgram Voice Agent entry (voice/deepgram_agent): functions run on the tool server, the
server's safety lines are injected word for word, and only the server's end_call drops the line."""

import asyncio
import json

from voice.deepgram_agent import agent, server

SAFETY = "If you smell gas, leave the house now, and call 911 from outside."


class FakeServer:
    def __init__(self, turns=None, tools=None):
        self.turns = turns or {}
        self.tools = tools or {}
        self.posts: list[tuple] = []

    async def turn(self, speaker, text):
        self.posts.append(("turn", speaker, text))
        return self.turns.get(text, {"ok": True, "say": "", "data": {}})

    async def tool(self, name, args, key):
        self.posts.append(("tool", name, args, key))
        return self.tools.get(name, {"ok": True, "say": "", "data": {}})

    async def end(self, reason):
        self.posts.append(("end", reason))
        return {"ok": True}


class FakeSocket:
    def __init__(self):
        self.sent: list = []

    async def send(self, message):
        self.sent.append(json.loads(message) if isinstance(message, str) else message)


def make_call(fake_server, **kwargs):
    events = {"hung_up": 0, "played": []}

    async def play(audio):
        events["played"].append(audio)

    async def clear():
        return None

    async def hang_up():
        events["hung_up"] += 1

    call = agent.Call(fake_server, {"book_appointment"}, play, clear, hang_up, "key", **kwargs)
    call.ws = FakeSocket()
    call.call_id = "dg-1"
    call.goodbye = "Thanks for calling Summit Air."
    return call, events


def run(coro):
    return asyncio.run(coro)


def test_a_function_runs_on_the_tool_server_and_its_envelope_goes_back():
    fake = FakeServer(tools={"book_appointment": {"ok": False, "say": "", "data": {},
                                                  "refused": "gate_read_back"}})
    call, _ = make_call(fake)
    run(call.handle({"type": "FunctionCallRequest", "functions": [
        {"id": "fc_1", "name": "book_appointment", "arguments": '{"slot_id": "s1"}',
         "client_side": True}]}))
    _, name, args, key = fake.posts[0]
    assert (name, args) == ("book_appointment", {"slot_id": "s1"}) and key
    response = call.ws.sent[-1]
    assert response["type"] == "FunctionCallResponse" and response["id"] == "fc_1"
    assert json.loads(response["content"]) == {"ok": False, "refused": "gate_read_back"}


def test_the_servers_safety_lines_are_injected_word_for_word():
    fake = FakeServer(turns={"I smell gas.": {"ok": True, "say": SAFETY,
                                              "data": {"safety": {"action": "speak_lines"}}}})
    call, _ = make_call(fake)
    run(call.handle({"type": "ConversationText", "role": "user", "content": "I smell gas."}))
    assert call.ws.sent == [{"type": "InjectAgentMessage", "message": SAFETY,
                             "behavior": "interrupt"}]


def test_a_refused_end_call_keeps_the_line_open():
    fake = FakeServer(tools={"end_call": {"ok": False, "say": SAFETY, "data": {},
                                          "refused": "gate_safety"}})
    call, events = make_call(fake)

    async def scenario():
        await call.handle({"type": "ConversationText", "role": "assistant", "content": "Bye now."})
        await call.handle({"type": "FunctionCallRequest", "functions": [
            {"id": "fc_2", "name": "end_call", "arguments": "{}", "client_side": True}]})
        await call.handle({"type": "AgentAudioDone"})

    run(scenario())
    assert events["hung_up"] == 0 and not call.hanging_up


def test_the_goodbye_said_with_end_call_drops_the_line_once_it_has_played(monkeypatch):
    monkeypatch.setattr(agent, "HANG_UP_GRACE_S", 0)
    fake = FakeServer(tools={"end_call": {"ok": True, "say": "Thanks for calling Summit Air.",
                                          "data": {"hang_up": True}}})
    call, events = make_call(fake)

    async def scenario():
        await call.handle({"type": "ConversationText", "role": "user", "content": "That's all."})
        await call.handle({"type": "ConversationText", "role": "assistant",
                           "content": "Thanks for calling. Bye."})
        await call.handle({"type": "FunctionCallRequest", "functions": [
            {"id": "fc_3", "name": "end_call", "arguments": "{}", "client_side": True}]})
        await call.handle({"type": "AgentAudioDone"})

    run(scenario())
    assert events["hung_up"] == 1
    end = next(m for m in call.ws.sent if m.get("type") == "FunctionCallResponse")
    assert "say" not in json.loads(end["content"])
    # No second goodbye on top of the model's.
    assert not any(m.get("type") == "InjectAgentMessage" for m in call.ws.sent)


def test_an_end_call_with_nothing_said_gets_the_tenants_goodbye(monkeypatch):
    monkeypatch.setattr(agent, "GOODBYE_WAIT_S", 0)
    fake = FakeServer(tools={"end_call": {"ok": True, "say": "", "data": {"hang_up": True}}})
    call, _ = make_call(fake)

    async def scenario():
        await call.handle({"type": "ConversationText", "role": "user", "content": "Bye."})
        await call.handle({"type": "FunctionCallRequest", "functions": [
            {"id": "fc_4", "name": "end_call", "arguments": "{}", "client_side": True}]})
        await asyncio.sleep(0.01)

    run(scenario())
    assert call.ws.sent[-1] == {"type": "InjectAgentMessage",
                                "message": "Thanks for calling Summit Air.", "behavior": "interrupt"}


def test_the_settings_send_haiku_through_the_custom_endpoint_without_temperature():
    message = agent.settings(instructions="Be brief.", specs=[{"name": "end_call",
                             "description": "End.", "parameters": {"type": "object"}, "write": False}],
                             greeting="Hi.", cfg={"listen_model": "flux-general-en",
                                                  "voice": "aura-2-arcas-en"},
                             model="claude-haiku-5-5", anthropic_key="k", keyterms=["Summit Air"],
                             audio=server.PHONE_AUDIO)
    think = message["agent"]["think"]
    assert think["provider"] == {"type": "anthropic", "model": "claude-haiku-5-5"}
    assert think["endpoint"]["headers"] == {"x-api-key": "k"}
    assert message["agent"]["listen"]["provider"]["model"] == "flux-general-en"
    assert message["audio"]["input"] == {"encoding": "mulaw", "sample_rate": 8000}


def test_twilio_signatures_and_stream_keys():
    params = {"CallSid": "CA1", "From": "+15551112222"}
    signature = server.twilio_signature("token", "https://host/deepgram/twiml", params)
    assert signature == server.twilio_signature("token", "https://host/deepgram/twiml",
                                                dict(reversed(params.items())))
    assert signature != server.twilio_signature("other", "https://host/deepgram/twiml", params)
    body = server.twiml("wss://host/deepgram/stream", "CA1", "+1555", "+1646", "k1")
    assert '<Stream url="wss://host/deepgram/stream">' in body and 'value="k1"' in body


def test_the_read_back_is_said_by_the_bridge_word_for_word_not_rewritten():
    """Call 1: the model rewrote the read-back with numerals, which Aura read as numbers."""
    line = "I have your home at 199 East 4th Street, ZIP code one zero zero zero nine. Is that right?"
    fake = FakeServer(tools={"read_back": {"ok": True, "say": line, "data": {}}})
    call, _ = make_call(fake)
    call.said_this_turn = 1
    run(call.handle({"type": "FunctionCallRequest", "functions": [
        {"id": "fc_5", "name": "read_back", "arguments": "{}", "client_side": True}]}))
    assert call.ws.sent[0] == {"type": "InjectAgentMessage", "message": line, "behavior": "queue"}
    result = json.loads(call.ws.sent[-1]["content"])
    assert "say" not in result and result["said"] == line


def test_a_lookup_starts_with_one_short_line_when_the_agent_has_said_nothing():
    fake = FakeServer()
    call, _ = make_call(fake)

    async def scenario():
        await call.handle({"type": "ConversationText", "role": "user", "content": "10009."})
        for i in (1, 2):
            await call.handle({"type": "FunctionCallRequest", "functions": [
                {"id": f"fc_{i}", "name": "check_service_area", "arguments": '{"zip": "10009"}',
                 "client_side": True}]})

    run(scenario())
    injected = [m for m in call.ws.sent if m.get("type") == "InjectAgentMessage"]
    assert injected == [{"type": "InjectAgentMessage", "message": agent.LOOKUP_LINE,
                         "behavior": "queue"}]
