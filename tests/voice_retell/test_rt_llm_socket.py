"""Retell's custom-LLM socket (voice/retell_llm): our brain writes every reply. The server's lines
go out word for word and uninterruptible, the model's text passes the reply rule, and only the
server's end_call ends the call. The tool server and the model are stand-ins here; the rig runs
the real ones."""

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice.retell_llm import llm_socket
from voice.retell_llm.call import caller_text, heard_after
from voice.retell_llm.model import Reply
from voice.retell_llm.rig_adapter import rig_agent_body, socket_url

API_KEY = "key_test"
SAFETY = "If you smell gas, leave the house now, and call 911 from outside."


def envelope(ok=True, say="", data=None, refused=None):
    return {"ok": ok, "say": say, "data": data or {}, "refused": refused}


class FakeToolServer:
    """Answers the socket's posts the way the tool server would for one scripted call."""

    def __init__(self):
        self.posts: list[tuple[str, dict]] = []
        self.turns: dict[str, dict] = {}
        self.tools: dict[str, dict] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        path = request.url.path
        self.posts.append((path, body))
        if path.endswith("/turn") and body["speaker"] == "caller":
            return httpx.Response(200, json=self.turns.get(body["text"], envelope()))
        if path.startswith("/tools/"):
            return httpx.Response(200, json=self.tools.get(path.removeprefix("/tools/"), envelope()))
        return httpx.Response(200, json=envelope())

    def lines(self, speaker: str) -> list[str]:
        return [b["text"] for p, b in self.posts if p.endswith("/turn") and b["speaker"] == speaker]


class FakeBrain:
    """Scripted model replies, one per request: (text chunks, tool calls)."""

    def __init__(self, replies, wait_s=()):
        self.replies = list(replies)
        # Seconds before each reply's first word, so a test can replace a turn mid-way.
        self.wait_s = list(wait_s)
        self.requests: list[list[dict]] = []
        self.systems: list[str] = []
        self.tools: list[list[dict]] = []

    async def stream(self, system, messages, tools, reply: Reply):
        self.requests.append(json.loads(json.dumps(messages)))
        self.systems.append(system)
        self.tools.append(tools)
        chunks, uses = self.replies.pop(0)
        if self.wait_s:
            await asyncio.sleep(self.wait_s.pop(0))
        for chunk in chunks:
            yield chunk
        if uses:
            # As the real stream does when the first tool call starts.
            reply.tool_started = uses[0]["name"]
            yield ""
        text = "".join(chunks)
        reply.content = ([{"type": "text", "text": text}] if text else []) + [
            {"type": "tool_use", **u} for u in uses
        ]
        reply.tool_uses = uses
        reply.first_token_ms = 300.0


@pytest.fixture
def socket(monkeypatch):
    monkeypatch.setenv("RETELL_API_KEY", API_KEY)
    server = FakeToolServer()

    def make(replies=(), wait_s=()):
        brain = FakeBrain(replies, wait_s)
        app = llm_socket.create_app(brain=brain, transport=httpx.MockTransport(server.handle))
        return TestClient(app), brain

    return server, make


DETAILS = {
    "interaction_type": "call_details",
    "call": {
        "call_type": "web_call",
        "metadata": {"summit_call_id": "rig-1"},
        "retell_llm_dynamic_variables": {
            "summit_call_id": "rig-1", "instructions": "You answer for Summit Air.",
            "greeting": "Thanks for calling Summit Air, this is Katie.",
        },
    },
}


def path() -> str:
    return f"/retell-llm/{llm_socket.socket_key(API_KEY)}/call_abc"


def open_call(ws) -> None:
    assert json.loads(ws.receive_text())["response_type"] == "config"
    ws.send_text(json.dumps(DETAILS))
    begin = json.loads(ws.receive_text())
    assert begin == {"response_type": "response", "response_id": 0,
                     "content": "Thanks for calling Summit Air, this is Katie.",
                     "content_complete": True}


def transcript(*lines):
    roles = ["agent", "user"]
    return [{"role": roles[i % 2], "content": text} for i, text in enumerate(lines)]


def ask(ws, response_id, lines) -> list[dict]:
    """One response_required, and every event of the response until it completes."""
    ws.send_text(json.dumps({"interaction_type": "response_required", "response_id": response_id,
                             "transcript": transcript(*lines)}))
    events = []
    while True:
        event = json.loads(ws.receive_text())
        events.append(event)
        if event.get("content_complete"):
            return events


def said(events) -> str:
    return "".join(e["content"] for e in events)


def test_a_socket_without_the_key_is_closed(socket):
    _, make = socket
    client, _ = make()
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/retell-llm/wrong/call_abc") as ws:
        ws.receive_text()


def test_the_safety_lines_are_the_servers_word_for_word_and_cannot_be_cut_off(socket):
    server, make = socket
    server.turns["I smell gas in the basement."] = envelope(
        say=SAFETY, data={"safety": {"action": "speak_lines", "text": SAFETY}})
    client, brain = make()
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Thanks for calling Summit Air, this is Katie.",
                             "I smell gas in the basement."])
    assert said(events) == SAFETY
    assert events[0]["no_interruption_allowed"] is True
    assert brain.requests == []
    # The greeting is posted before the caller's line, from Retell's transcript.
    assert server.lines("agent")[0] == "Thanks for calling Summit Air, this is Katie."
    assert server.lines("caller") == ["I smell gas in the basement."]


def test_the_models_text_streams_through_the_reply_rule(socket):
    _, make = socket
    client, _ = make([(["Sure, I can help with that. ", "Let me read that back to you. ",
                        "What's the address?"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "My furnace stopped working."])
    assert said(events) == "Sure, I can help with that. What's the address?"
    assert all("read that back" not in e["content"] for e in events)


def test_only_the_servers_end_call_ends_the_call(socket):
    server, make = socket
    end = {"id": "t1", "name": "end_call", "input": {"reason": "done"}}
    server.tools["end_call"] = envelope(data={"hang_up": True})
    client, _ = make([([], [end])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "That's all, bye."])
    # The model said nothing, so the tenant's goodbye goes before the hang-up.
    assert "Thanks for calling Summit Air" in said(events)
    assert events[-1]["end_call"] is True


def test_a_refused_end_call_keeps_the_call_open(socket):
    server, make = socket
    end = {"id": "t1", "name": "end_call", "input": {"reason": "done"}}
    server.tools["end_call"] = envelope(ok=False, say=SAFETY, refused="gate_safety")
    client, _ = make([(["Okay, bye now. "], [end])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "Bye."])
    assert SAFETY in said(events)
    assert "end_call" not in events[-1]


def test_a_reply_the_caller_never_heard_leaves_the_history(socket):
    _, make = socket
    client, brain = make([(["Got it. "], []), (["Okay, a no-heat call. "], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        ask(ws, 1, ["Hi.", "My heat"])
        # The caller kept talking: Retell's transcript has no agent line after theirs.
        ws.send_text(json.dumps({
            "interaction_type": "response_required", "response_id": 2,
            "transcript": [{"role": "agent", "content": "Hi."},
                           {"role": "user", "content": "My heat is out."}]}))
        while not json.loads(ws.receive_text()).get("content_complete"):
            pass
    second = brain.requests[1]
    assert [m["content"] for m in second if m["role"] == "assistant"][-1] != "Got it."
    assert second[-1] == {"role": "user", "content": "My heat is out."}


def test_a_newer_request_cancels_the_turn_in_progress(socket):
    server, make = socket
    slots = {"id": "t1", "name": "find_slots", "input": {"job_type": "repair"}}
    client, brain = make([(["Let me look. "], [slots]), (["I have Thursday morning. "], [])],
                         wait_s=[1.0, 0])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        for rid, line in ((1, "My heat"), (2, "My heat is out.")):
            ws.send_text(json.dumps({"interaction_type": "response_required", "response_id": rid,
                                     "transcript": transcript("Hi.", line)}))
            # The first turn is waiting on the model when the second request arrives.
            deadline = time.monotonic() + 5
            while len(brain.requests) < rid and time.monotonic() < deadline:
                time.sleep(0.01)
        events = []
        while not (event := json.loads(ws.receive_text())).get("content_complete"):
            events.append(event)
        events.append(event)
    assert {e["response_id"] for e in events} == {2}
    assert said(events) == "I have Thursday morning."
    # The replaced turn never reached its tool call, and the history the second request saw
    # holds no tool call without a result.
    assert not any(p.startswith("/tools/") for p, _ in server.posts)
    assert brain.requests[1][-1] == {"role": "user", "content": "My heat is out."}


def test_dead_air_checks_in_then_says_goodbye_and_hangs_up(socket):
    _, make = socket
    client, _ = make()
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = []
        for rid in range(1, 5):
            ws.send_text(json.dumps({"interaction_type": "reminder_required", "response_id": rid,
                                     "transcript": transcript("Hi.", "Hold on.")}))
            while not (event := json.loads(ws.receive_text())).get("content_complete"):
                events.append(event)
            events.append(event)
    assert said(events).count("I'm still here") == 3
    assert events[-1]["end_call"] is True


def test_a_closed_socket_posts_the_last_line_and_ends_the_call(socket):
    server, make = socket
    client, _ = make()
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        ws.send_text(json.dumps({"interaction_type": "update_only",
                                 "transcript": transcript("Thanks for calling.", "Bye.", "Bye now.")}))
        ws.close(1000)
    ends = [b for p, b in server.posts if p.endswith("/end")]
    assert ends == [{"reason": "caller_hangup"}]
    assert server.lines("agent")[-1] == "Bye now."


def test_caller_text_is_everything_since_the_agent_last_spoke():
    lines = [{"role": "agent", "content": "Hi."}, {"role": "user", "content": "My heat"},
             {"role": "user", "content": "is out."}]
    assert caller_text(lines) == "My heat is out."
    assert not heard_after(lines, 1)


def test_the_rig_agent_runs_on_the_socket_with_no_webhook():
    body = rig_agent_body("https://host/eval", API_KEY, {"voice_id": "v1", "voice_model": None})
    assert body["response_engine"] == {"type": "custom-llm",
                                       "llm_websocket_url": socket_url("https://host/eval", API_KEY)}
    assert socket_url("https://host/eval", API_KEY).startswith("wss://host/eval/retell-llm/")
    assert "webhook_url" not in body
    assert body["reminder_trigger_ms"] == 120_000 and body["reminder_max_count"] == 4


# ---- the server-led arm (issue #176): every sentence a tool result carries is spoken as written

READ_BACK = "So that's forty-five Forest Avenue, ending in zero one one eight. That's all correct, right?"
FLAGGED = "I've let our team know, and someone will call you tomorrow before 12 PM."


@pytest.fixture
def server_led(socket, monkeypatch):
    monkeypatch.setenv("RETELL_LLM_SERVER_LED", "1")
    return socket


def tool_results(request: list[dict]) -> list[dict]:
    return [json.loads(b["content"]) for m in request if m["role"] == "user"
            and isinstance(m["content"], list) for b in m["content"]]


def test_server_led_a_question_from_the_server_is_the_whole_turn(server_led):
    server, make = server_led
    read_back = {"id": "t1", "name": "read_back", "input": {"street": "45 Forest Ave"}}
    server.tools["read_back"] = envelope(say=READ_BACK, data={"confirmed": False})
    client, brain = make([([], [read_back])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "It's 718 555 0118."])
    # A tool that answers fast gets no hold phrase.
    assert said(events) == READ_BACK
    # Interruptible, so the caller can correct it.
    assert not any(e.get("no_interruption_allowed") for e in events)
    assert len(brain.requests) == 1


def test_server_led_a_statement_is_spoken_then_the_model_carries_on(server_led):
    server, make = server_led
    flag = {"id": "t1", "name": "flag_person", "input": {"reason": "billing"}}
    server.tools["flag_person"] = envelope(say=FLAGGED, data={"flag_id": 3})
    server.tools["flag_person"]["next"] = "If the sentence didn't ask already, ask 'Anything else?'."
    client, brain = make([([], [flag]), (["Anything else? "], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "Tomorrow morning is fine."])
    assert said(events) == f"{FLAGGED} Anything else?"
    # The model is told the caller heard the sentence, and isn't handed it to say again.
    result = tool_results(brain.requests[1])[0]
    assert result["said_to_caller"] == FLAGGED
    assert "say" not in result
    assert result["next"].startswith("If the sentence")


def test_server_led_a_refusals_sentence_is_the_whole_turn(server_led):
    server, make = server_led
    book = {"id": "t1", "name": "book_appointment", "input": {}}
    question = "Will someone be home to let the technician in?"
    server.tools["book_appointment"] = envelope(ok=False, say=question, refused="visit_questions_first")
    client, brain = make([([], [book])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "Yes, that works."])
    assert said(events) == question
    assert len(brain.requests) == 1


def test_server_led_a_safety_refusal_cannot_be_cut_off(server_led):
    server, make = server_led
    end = {"id": "t1", "name": "end_call", "input": {"reason": "done"}}
    server.tools["end_call"] = envelope(ok=False, say=SAFETY, refused="gate_safety")
    client, _ = make([([], [end])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "Bye."])
    assert said(events) == SAFETY
    assert events[0]["no_interruption_allowed"] is True


def test_server_led_a_lead_in_held_before_a_server_sentence_is_never_said(server_led):
    server, make = server_led
    find = {"id": "t1", "name": "find_address", "input": {"street": "45 Forest Ave"}}
    lookup = {"id": "t2", "name": "find_customer", "input": {}}
    history = {"id": "t3", "name": "get_customer_history", "input": {}}
    server.tools["find_address"] = envelope(data={"zip": "11385"})
    server.tools["find_customer"] = envelope(say="Should I put this under your account?")
    client, _ = make([
        ([], [find]), (["What's the best number? "], []),
        (["I'm checking that now. "], [lookup]),
        (["Thanks. "], [history]), (["What's the best number? "], []),
    ])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        ask(ws, 1, ["Hi.", "45 Forest Avenue."])
        events = ask(ws, 2, ["Hi.", "45 Forest Avenue.", "What's the best number?", "My account."])
        assert said(events) == "Should I put this under your account?"
        # A tool with nothing to say: the model's next request writes the reply, and the text that
        # came with the call is dropped all the same.
        events = ask(ws, 3, ["Hi.", "45 Forest Avenue.", "What's the best number?", "My account.",
                             "Should I put this under your account?", "Yes."])
    # Before the read-back the whole reply is held, so its lead-in goes with the tool call.
    assert said(events) == "What's the best number?"


def test_server_led_the_arm_brings_its_own_prompt_and_tools(server_led, monkeypatch, tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("You are Brooke.\n\n## Before this call\n\n{{call_context}}")
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps({"general_tools": [{
        "name": "book_appointment", "description": "Book it.",
        "parameters": {"type": "object", "properties": {}}, "url": "https://x/retell/tools/b"}]}))
    monkeypatch.setenv("RETELL_LLM_PROMPT", str(prompt))
    monkeypatch.setenv("RETELL_LLM_TOOLS", str(tools))
    _, make = server_led
    client, brain = make([(["What's going on? "], [])])
    details = json.loads(json.dumps(DETAILS))
    details["call"]["retell_llm_dynamic_variables"]["call_context"] = "Caller is new."
    with client.websocket_connect(path()) as ws:
        assert json.loads(ws.receive_text())["response_type"] == "config"
        ws.send_text(json.dumps(details))
        ws.receive_text()
        ask(ws, 1, ["Hi.", "My heat is out."])
    assert brain.systems[0] == "You are Brooke.\n\n## Before this call\n\nCaller is new."
    assert [t["name"] for t in brain.tools[0]] == ["book_appointment"]
    assert brain.tools[0][0]["description"] == "Book it."


def test_an_arm_tool_the_tool_server_lacks_is_refused(tmp_path):
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps({"general_tools": [{
        "name": "order_pizza", "description": "", "parameters": {}}]}))
    with pytest.raises(ValueError, match="order_pizza"):
        llm_socket.arm_tools(tools, [{"name": "book_appointment", "write": True}])


def test_an_arm_tool_writes_as_our_spec_says():
    tools = llm_socket.arm_tools(
        llm_socket.ROOT / "retell" / "arms" / "server-led" / "tools.json",
        llm_socket.tool_specs(llm_socket.job_types(llm_socket.ROOT / "tenants" / "summit-air")),
    )
    writes = {t["name"]: t["write"] for t in tools}
    assert writes["book_appointment"] is True
    assert writes["find_slots"] is False


def test_server_led_the_sentence_before_a_spoken_result_is_never_said(server_led):
    server, make = server_led
    book = {"id": "t1", "name": "book_appointment", "input": {}}
    booked = "Okay, I've booked it: the technician will arrive today between 11:30 and 12. Anything else?"
    server.tools["book_appointment"] = envelope(say=booked)
    client, _ = make([(["Thanks. ", "I'm booking that now. "], [book])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "No gate code, no pets."])
    assert said(events) == f"Thanks. {booked}"


def test_server_led_an_offer_without_a_question_still_waits_for_the_caller(server_led):
    server, make = server_led
    slots = {"id": "t1", "name": "find_slots", "input": {"job_type": "no_heat"}}
    offer = "Today I have 11:30 to 12 or 12 to 12:30."
    server.tools["find_slots"] = envelope(say=offer, data={"windows": [{"window": "1130-1200"}]})
    client, brain = make([([], [slots])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "Yes, that's all correct."])
    assert said(events) == offer
    assert len(brain.requests) == 1


def test_server_led_the_model_never_names_its_step(server_led):
    _, make = server_led
    client, _ = make([(["Thanks. ", "Next, what's the best number to reach you?"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "It's 45 Forest Avenue."])
    assert said(events) == "Thanks. What's the best number to reach you?"


def test_server_led_a_slow_tool_gets_one_holding_line(monkeypatch):
    monkeypatch.setenv("RETELL_API_KEY", API_KEY)
    monkeypatch.setenv("RETELL_LLM_SERVER_LED", "1")
    monkeypatch.setattr(llm_socket, "hold_phrase_after_s", lambda path: 0.05)
    server = FakeToolServer()
    offer = "The soonest I have is today, 12 to 12:30. Does that work?"
    server.tools["find_slots"] = envelope(say=offer, data={"offer": {"window": "1200-1230"}})

    async def slow(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/find_slots":
            await asyncio.sleep(0.3)
        return server.handle(request)

    slots = {"id": "t1", "name": "find_slots", "input": {"job_type": "no_heat"}}
    brain = FakeBrain([([], [slots])])
    app = llm_socket.create_app(brain=brain, transport=httpx.MockTransport(slow))
    with TestClient(app).websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "Yes, that's all correct."])
    # One of the tenant's hold phrases, then the offer as written.
    assert said(events) == f"One sec. {offer}"


def test_server_led_the_models_first_sentence_streams_at_once(server_led):
    server, make = server_led
    slots = {"id": "t2", "name": "find_slots", "input": {"job_type": "no_heat"}}
    server.tools["find_slots"] = envelope(ok=False, refused="read_back_first")
    client, _ = make([(["Sure. ", "What's the address? "], [slots]), ([], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "Can I book a visit?"])
    assert said(events) == "Sure. What's the address?"
    assert events[0]["content"] == "Sure."


def slow_socket(monkeypatch, delays: dict, replies, after_s=0.05):
    """A socket whose tools answer after `delays` seconds, with the hold phrase after `after_s`."""
    monkeypatch.setenv("RETELL_API_KEY", API_KEY)
    monkeypatch.setenv("RETELL_LLM_SERVER_LED", "1")
    monkeypatch.setattr(llm_socket, "hold_phrase_after_s", lambda path: after_s)
    server = FakeToolServer()

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delays.get(request.url.path.removeprefix("/tools/"), 0))
        return server.handle(request)

    brain = FakeBrain(replies)
    app = llm_socket.create_app(brain=brain, transport=httpx.MockTransport(slow))
    return server, TestClient(app), brain


def test_server_led_a_fast_tool_gets_no_hold_phrase(monkeypatch):
    slots = {"id": "t1", "name": "find_slots", "input": {"job_type": "no_heat"}}
    offer = "The soonest I have is today, 12 to 12:30. Does that work?"
    server, client, _ = slow_socket(monkeypatch, {}, [([], [slots])], after_s=0.5)
    server.tools["find_slots"] = envelope(say=offer, data={"offer": {"window": "1200-1230"}})
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "Yes."])) == offer


def test_server_led_one_hold_phrase_a_wait_and_never_the_same_twice_running(monkeypatch):
    address = {"id": "t1", "name": "find_address", "input": {}}
    slots = {"id": "t2", "name": "find_slots", "input": {"job_type": "no_heat"}}
    offer = "The soonest I have is today, 12 to 12:30. Does that work?"
    server, client, _ = slow_socket(
        monkeypatch, {"find_address": 0.2, "find_slots": 0.2},
        [([], [address]), ([], [slots]), ([], [slots])],
    )
    server.tools["find_address"] = envelope(say="What's the ZIP code there?", ok=False,
                                            refused="zip_asked")
    server.tools["find_slots"] = envelope(say=offer, data={"offer": {"window": "1200-1230"}})
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        first = said(ask(ws, 1, ["Hi.", "45 Forest Avenue."]))
        second = said(ask(ws, 2, ["Hi.", "45 Forest Avenue.", first, "Yes."]))
        third = said(ask(ws, 3, ["Hi.", "45 Forest Avenue.", first, "Yes.", second, "Again?"]))
    phrases = [t.split(" ")[0] + " " + t.split(" ")[1] for t in (first, second, third)]
    assert first == "One sec. What's the ZIP code there?"
    assert second.startswith("Okay, one moment. ") and third.startswith("Just a moment. ")
    assert len(set(phrases)) == 3


def test_server_led_the_goodbye_is_jacksons_line_and_gets_no_hold(monkeypatch):
    end = {"id": "t1", "name": "end_call", "input": {"reason": "done"}}
    server, client, _ = slow_socket(monkeypatch, {"end_call": 0.2},
                                    [(["Thanks for calling, take care!"], [end])])
    server.tools["end_call"] = envelope(data={"hang_up": True})
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "No, that's all."])
    assert said(events) == "Thanks so much for calling Summit Air today. Have a great day!"
    assert events[-1]["end_call"] is True


def test_server_led_a_narrating_sentence_is_never_said(server_led):
    _, make = server_led
    client, _ = make([(["I'm booking that now. ", "What's the best number?"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "Sure."])) == "What's the best number?"


def test_server_led_silence_gets_are_you_still_there_and_the_call_stays_open(server_led):
    _, make = server_led
    client, _ = make()
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        for rid in (1, 2):
            ws.send_text(json.dumps({"interaction_type": "reminder_required", "response_id": rid,
                                     "transcript": transcript("Hi.")}))
            events = []
            while not (event := json.loads(ws.receive_text())).get("content_complete"):
                events.append(event)
            events.append(event)
            assert said(events) == "Are you still there?"
            assert "end_call" not in events[-1]


def test_server_led_a_name_at_the_end_of_a_sentence_is_never_said(server_led):
    _, make = server_led
    client, _ = make([(["You're welcome, Evelyn. ", "Anything else?"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "It's Evelyn Marsh. Thanks!"])) == "You're welcome. Anything else?"


def test_server_led_no_second_opener_right_after_a_hold_phrase(monkeypatch):
    book = {"id": "t1", "name": "book_appointment", "input": {}}
    server, client, _ = slow_socket(monkeypatch, {"book_appointment": 0.2}, [([], [book])])
    question = "Will you be able to approve any repair work the technician recommends?"
    server.tools["book_appointment"] = envelope(ok=False, say=f"Got it. {question}",
                                                refused="visit_questions_first")
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "Yes, that works."])) == f"One sec. {question}"
