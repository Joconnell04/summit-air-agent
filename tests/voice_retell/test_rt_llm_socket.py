"""Retell's custom-LLM socket (voice/retell_llm): our brain writes every reply. The server's lines
go out word for word and uninterruptible, the model's text passes the reply rule, and only the
server's end_call ends the call. The tool server and the model are stand-ins here; the rig runs
the real ones."""

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice.retell_llm import llm_socket
from voice.retell_llm.call import caller_text, heard_after, last_word_at
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
        self.transfer = envelope(data={"transfer": None})

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        path = request.url.path
        self.posts.append((path, body))
        if path.endswith("/turn") and body["speaker"] == "caller":
            return httpx.Response(200, json=self.turns.get(body["text"], envelope()))
        if path.endswith("/transfer"):
            return httpx.Response(200, json=self.transfer)
        if path.startswith("/tools/"):
            got = self.tools.get(path.removeprefix("/tools/"), envelope())
            if isinstance(got, list):
                # One result per call, in order; the last one repeats.
                got = got.pop(0) if len(got) > 1 else got[0]
            return httpx.Response(200, json=got)
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


def open_call(ws, details: dict = DETAILS) -> None:
    assert json.loads(ws.receive_text())["response_type"] == "config"
    ws.send_text(json.dumps(details))
    begin = json.loads(ws.receive_text())
    assert begin == {"response_type": "response", "response_id": 0,
                     "content": "Thanks for calling Summit Air, this is Katie.",
                     "content_complete": True, "no_interruption_allowed": True}


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


def test_an_agent_line_is_posted_when_retell_merges_an_earlier_split_line(socket):
    """Call retell_call_40414280dccd1cbd3eafdab3b62: Retell first had the greeting as two entries,
    then one, so the gas advice after it was never posted, the server thought it unsaid, and said
    it again over the caller taking it back."""
    server, make = socket
    server.turns["I smell gas."] = envelope(
        say=SAFETY, data={"safety": {"action": "speak_lines", "text": SAFETY}})
    client, _ = make([(["Got it. Anything else?"], [])])
    greeting = "Thanks for calling Summit Air, this is Katie."
    split = [{"role": "agent", "content": "Thanks for calling Summit Air, this is"},
             {"role": "agent", "content": "Katie."}, {"role": "user", "content": "I smell gas."}]
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        ws.send_text(json.dumps({"interaction_type": "response_required", "response_id": 1,
                                 "transcript": split}))
        while not json.loads(ws.receive_text()).get("content_complete"):
            pass
        ask(ws, 2, [greeting, "I smell gas.", SAFETY, "Oh, actually no, my roommate was cooking."])
    assert server.lines("agent")[-1] == SAFETY
    assert server.lines("caller") == ["I smell gas.", "Oh, actually no, my roommate was cooking."]


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


def _said(role: str, text: str, start: float, end: float) -> dict:
    return {"role": role, "content": text, "words": [{"word": text, "start": start, "end": end}]}


def test_words_said_over_the_end_of_the_agents_line_are_kept():
    """Phone check t3, 6 November (call_293d1b2c08595953ac3aebcbadf): Retell split "What's the
    street address?" around the caller's "40", and the server heard only "Fulton Street"."""
    lines = [_said("agent", "What's the street ", 34.59, 38.19), _said("user", "40 ", 38.44, 38.65),
             _said("agent", "address?", 38.19, 38.75),
             _said("user", "Fulton Street, Manhattan. It's a house. ", 38.67, 42.4)]
    assert caller_text(lines) == "40 Fulton Street, Manhattan. It's a house."


def test_a_reply_after_the_caller_still_ends_their_line():
    lines = [_said("user", "My heat is out.", 5.0, 7.0), _said("agent", "Sorry to hear.", 8.0, 9.0),
             _said("user", "Since last night.", 9.5, 10.5)]
    assert caller_text(lines) == "Since last night."


def test_the_rig_agent_runs_on_the_socket_with_no_webhook():
    body = rig_agent_body("https://host/eval", API_KEY, {"voice_id": "v1", "voice_model": None})
    assert body["response_engine"] == {"type": "custom-llm",
                                       "llm_websocket_url": socket_url("https://host/eval", API_KEY)}
    assert socket_url("https://host/eval", API_KEY).startswith("wss://host/eval/retell-llm/")
    assert "webhook_url" not in body
    assert body["reminder_trigger_ms"] == 120_000 and body["reminder_max_count"] == 4


# ---- the server-led arm (issue #176): every sentence a tool result carries is spoken as written

READ_BACK = "So that's forty-five Forest Avenue, ending in zero one one eight. That's all correct, right?"
FLAGGED = "I've let our team know, and someone will do their best to call you back tomorrow before 12 PM."


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
    tools = tmp_path / "tools.yaml"
    tools.write_text("book_appointment: Book it.\n")
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
    # Every tool the server has, with the arm's description where it gives one.
    by_name = {t["name"]: t for t in brain.tools[0]}
    assert by_name["book_appointment"]["description"] == "Book it."
    assert "find_slots" in by_name and by_name["find_slots"]["description"] != "Book it."


def test_an_arm_tool_the_tool_server_lacks_is_refused(tmp_path):
    tools = tmp_path / "tools.yaml"
    tools.write_text("order_pizza: Order a pizza.\n")
    with pytest.raises(ValueError, match="order_pizza"):
        llm_socket.arm_tools(tools, [{"name": "book_appointment", "write": True}])


def test_an_arm_tool_writes_as_our_spec_says():
    tools = llm_socket.arm_tools(
        llm_socket.ROOT / "retell" / "socket" / "tools.yaml",
        llm_socket.tool_specs(llm_socket.job_types(llm_socket.ROOT / "tenants" / "summit-air")),
    )
    writes = {t["name"]: t["write"] for t in tools}
    assert writes["book_appointment"] is True
    assert writes["find_slots"] is False
    # The server's own parameters: read_back takes the job type, which dispatch needs.
    read_back = next(t for t in tools if t["name"] == "read_back")
    assert "job_type" in read_back["parameters"]["properties"]


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
    assert first == "One sec. What's the ZIP code there?"
    # Never on two turns in a row, and the next one is a different phrase.
    assert second == offer
    assert third == f"Okay, one moment. {offer}"


def test_server_led_the_goodbye_is_the_sockets_alone_and_gets_no_hold(monkeypatch):
    end = {"id": "t1", "name": "end_call", "input": {"reason": "done"}}
    server, client, _ = slow_socket(monkeypatch, {"end_call": 0.2},
                                    [(["Thanks for calling, take care!"], [end])])
    server.tools["end_call"] = envelope(data={"hang_up": True})
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "No, that's all."])
    # One closing, with one nicety for the time of day; the model's own goodbye is never said.
    text = said(events)
    niceties = llm_socket.tenant_lines(llm_socket.ROOT / "tenants", "summit-air")["closing_niceties"]
    assert text.startswith("Thanks for calling Summit Air today. ")
    assert text.removeprefix("Thanks for calling Summit Air today. ") in niceties
    assert "take care" not in text.lower()
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


def test_server_led_counted_from_the_tool_a_slow_model_alone_gets_no_hold_phrase(monkeypatch):
    monkeypatch.setenv("RETELL_LLM_HOLD_FROM", "tool")
    slots = {"id": "t1", "name": "find_slots", "input": {"job_type": "no_heat"}}
    offer = "The soonest I have is today, 12 to 12:30. Does that work?"
    server, client, brain = slow_socket(monkeypatch, {}, [([], [slots])], after_s=0.1)
    brain.wait_s = [0.3]  # the model takes longer than the threshold; the tool is instant
    server.tools["find_slots"] = envelope(say=offer, data={"offer": {"window": "1200-1230"}})
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "Yes."])) == offer


def test_server_led_today_and_tomorrow_are_said_alone(server_led):
    _, make = server_led
    client, _ = make([(["Tomorrow, Saturday, November 7 works for the visit. ", "Anything else?"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "When is it?"])) == (
            "Tomorrow works for the visit. Anything else?")


def test_server_led_never_thanks_the_caller_for_waiting(server_led):
    _, make = server_led
    client, _ = make([(["Thanks for waiting. ", "What's the street address?"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "Okay, I'm back."])) == "What's the street address?"


def test_server_led_a_question_is_never_cut(server_led):
    _, make = server_led
    client, _ = make([(["Let me ask, is it completely out?"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "The heat's off."])) == "Let me ask, is it completely out?"


def test_server_led_a_reply_the_filter_would_empty_is_said_whole(server_led):
    _, make = server_led
    client, _ = make([(["I'm checking on that for you now."], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "Is the tech coming?"])) == "I'm checking on that for you now."


def test_server_led_a_goodbye_without_end_call_is_said(server_led):
    _, make = server_led
    client, _ = make([(["You too, have a great day!"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "Have a good one."])) == "You too, have a great day!"


def test_the_closing_is_always_a_pinned_falling_line():
    """Jackson heard "Thanks for calling Summit Air." rise like a question; only niceties measured
    to fall in Brian's voice are pinned (retell/socket/closing-pitch-01.json)."""
    lines = llm_socket.tenant_lines(llm_socket.ROOT / "tenants", "summit-air")
    measured = json.loads((llm_socket.ROOT / "retell" / "socket"
                           / "closing-pitch-01.json").read_text())
    falling = {m["sentence"].strip() for m in measured if m.get("falls")}
    assert lines["closing"] == "Thanks for calling Summit Air today."
    assert lines["closing_niceties"]
    for nicety in lines["closing_niceties"]:
        assert nicety in falling, f"{nicety} has no falling measurement"


def test_server_led_no_server_connector_after_the_models_own(server_led):
    server, make = server_led
    read_back = {"id": "t1", "name": "read_back", "input": {}}
    server.tools["read_back"] = envelope(say=f"Perfect. {READ_BACK}", data={"confirmed": False})
    client, _ = make([(["Okay, thanks. "], [read_back])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "This number."])) == f"Okay, thanks. {READ_BACK}"


def test_server_led_nothing_of_the_models_before_the_closing_when_the_caller_is_done(monkeypatch):
    end = {"id": "t1", "name": "end_call", "input": {"reason": "done"}}
    server, client, _ = slow_socket(monkeypatch, {}, [(
        ["You're all set. ", "The technician will arrive today between 8 and 8:30. "], [end])])
    server.tools["end_call"] = envelope(data={"hang_up": True})
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        text = said(ask(ws, 1, ["Hi.", "That's all."]))
    assert text.startswith("Thanks for calling Summit Air today. ") and "all set" not in text


def test_server_led_a_done_caller_still_hears_a_reply_that_keeps_the_call_open(server_led):
    _, make = server_led
    client, _ = make([(["Okay. ", "Anything else?"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "Nope, not that window."])) == "Okay. Anything else?"


def test_server_led_lets_get_you_booked_is_narration(server_led):
    _, make = server_led
    client, _ = make([(["Actually, let's get you booked. ", "Can I get your name?"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "My AC is leaking."])) == "Can I get your name?"


def test_server_led_nothing_follows_the_models_question(server_led):
    _, make = server_led
    client, _ = make([(["Oh no, sorry it's leaking. ",
                        "Is the leak coming from an indoor unit? ",
                        "Actually, I don't see an account under your number. ",
                        "Can I get your name?"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        text = said(ask(ws, 1, ["Hi.", "Air conditioning is leaking."]))
    assert text == "Oh no, sorry it's leaking. Is the leak coming from an indoor unit?"


def test_the_wait_counts_from_the_callers_last_word():
    transcript = [{"role": "agent", "content": "Hi.", "words": [{"start": 0.5, "end": 1.0}]},
                  {"role": "user", "content": "My heat.", "words": [{"start": 2.0, "end": 3.5}]}]
    assert last_word_at(transcript, 1_000_000) == 1_000 + 3.5
    assert last_word_at(transcript, None) is None


def test_server_led_the_read_back_never_gets_a_hold_phrase(monkeypatch):
    read_back = {"id": "t1", "name": "read_back", "input": {}}
    server, client, _ = slow_socket(monkeypatch, {"read_back": 0.2}, [([], [read_back])])
    server.tools["read_back"] = envelope(say=READ_BACK, data={"confirmed": False})
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "This number."])) == READ_BACK


def test_server_led_a_held_reply_stops_at_its_first_question(server_led):
    """Before the read-back the whole reply is held; it still ends at its first question (the
    smoke call on 516: "...or a heat pump? Actually, mentioned a boiler. So next. ...")."""
    server, make = server_led
    find = {"id": "t1", "name": "find_address", "input": {"street": "1700 Bedford Avenue"}}
    server.tools["find_address"] = envelope(data={"zip": "11225"})
    client, _ = make([
        ([], [find]), (["What's the best number? "], []),
        (["Is it a boiler, a furnace, or a heat pump? ", "Actually, you mentioned a boiler. ",
          "I'll set it up as a tune-up."], []),
    ])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        ask(ws, 1, ["Hi.", "1700 Bedford Avenue."])
        events = ask(ws, 2, ["Hi.", "1700 Bedford Avenue.", "What's the best number?", "Mine."])
    assert said(events) == "Is it a boiler, a furnace, or a heat pump?"


def test_server_led_no_lone_got_it_after_a_hold_phrase(monkeypatch):
    slots = {"id": "t1", "name": "find_slots", "input": {"job_type": "no_heat"}}
    server, client, _ = slow_socket(monkeypatch, {"find_slots": 0.2},
                                    [([], [slots]), (["Got it. ", "Which day works for you?"], [])])
    server.tools["find_slots"] = envelope(ok=True, data={})
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "Yes."])) == "One sec. Which day works for you?"


CONNECTING = "Got it, transferring you now."
FALLBACK = (
    "I couldn't reach anyone just now, so I've let the team know and someone will do their best to call you back by "
    "9:30 AM."
)
ALLOWED = envelope(say=CONNECTING, data={
    "transfer": {"id": 7, "role": "office", "number": "+12125550100"}, "fallback": FALLBACK,
})
WANTS = {"id": "t1", "name": "request_transfer",
         "input": {"reason": "person", "summary": "Wants a person"}}


def test_an_allowed_transfer_on_a_phone_call_says_the_line_then_transfers(socket):
    server, make = socket
    server.tools["request_transfer"] = ALLOWED
    phone = {**DETAILS, "call": {**DETAILS["call"], "call_type": "phone_call"}}
    client, _ = make([([], [WANTS])])
    with client.websocket_connect(path()) as ws:
        open_call(ws, phone)
        events = ask(ws, 1, ["Hi.", "Can I talk to a real person?"])
    assert said(events) == CONNECTING
    assert events[-1]["transfer_number"] == "+12125550100"


def test_on_a_web_call_the_transfer_is_reported_failed_and_the_callback_said(socket):
    server, make = socket
    server.tools["request_transfer"] = ALLOWED
    client, _ = make([([], [WANTS])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "Can I talk to a real person?"])
    assert said(events) == FALLBACK
    assert "transfer_number" not in events[-1]
    assert ("/calls/rig-1/transfer/7/done", {"outcome": "failed"}) in server.posts


@pytest.mark.parametrize("phone", [True, False], ids=["phone", "web"])
def test_server_led_a_transfer_says_its_sentence_once(server_led, phone):
    server, make = server_led
    server.tools["request_transfer"] = ALLOWED
    details = {**DETAILS, "call": {**DETAILS["call"], "call_type": "phone_call"}} if phone else DETAILS
    client, _ = make([([], [WANTS])])
    with client.websocket_connect(path()) as ws:
        open_call(ws, details)
        events = ask(ws, 1, ["Hi.", "Can I talk to a real person?"])
    assert said(events) == (CONNECTING if phone else FALLBACK)


CALLBACK = "I've let our team know, and someone will do their best to call you back by 10:20 PM."
FLAG = {"id": "f1", "name": "flag_person", "input": {"reason": "Wants a person"}}


@pytest.mark.parametrize("put_through", [True, False], ids=["put_through", "callback"])
def test_server_led_a_flag_says_the_handoff_or_the_callback_never_both(server_led, put_through):
    """Call call_63eff21840ce12f1518561da90f said the callback time, then "I'm connecting you"."""
    server, make = server_led
    server.tools["flag_person"] = envelope(say=CALLBACK, data={"flag_id": 3, "callback_id": 4})
    if put_through:
        server.transfer = envelope(say=CONNECTING, data={"transfer": {
            "id": 9, "role": "on_call", "phone": "+12125550111"}})
    phone = {**DETAILS, "call": {**DETAILS["call"], "call_type": "phone_call"}}
    client, _ = make([([], [FLAG]), (["Anything else?"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws, phone)
        events = ask(ws, 1, ["Hi.", "Can someone call me?"])
    if put_through:
        assert said(events) == CONNECTING
        assert events[-1]["transfer_number"] == "+12125550111"
    else:
        assert said(events).startswith(CALLBACK) and CONNECTING not in said(events)


@pytest.mark.parametrize("line", [
    "The booking needs one more detail.",
    "To book the safety check, need your name first.",
    "Got it, to book this I need a few details.",
    "I need one more detail for the technician.",
])
def test_server_led_a_line_about_what_the_booking_needs_is_cut(server_led, line):
    _, make = server_led
    client, _ = make([([f"{line} What's your name?"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "I want a tune-up."])) == "What's your name?"


OFFER = "The soonest I have is today, 8 to 8:30. Does that work?"
DISPATCH = ("Okay. Our on-call technician will call you within twenty minutes and head out to "
            "you at 199 East Fourth Street.")


def test_server_led_a_dispatch_after_an_offer_is_the_servers_line_alone(server_led):
    """Call call_63eff21840ce12f1518561da90f said the model's slot offer, then the dispatch."""
    server, make = server_led
    server.tools["find_slots"] = [envelope(say=OFFER), envelope(say=DISPATCH,
                                                                data={"dispatched": True})]
    slots = {"id": "s1", "name": "find_slots", "input": {"job_type": "no_heat"}}
    again = {"id": "s2", "name": "find_slots", "input": {"job_type": "no_heat",
                                                          "facts": {"who_home": "mom, 85"}}}
    client, _ = make([([], [slots]),
                      (["Thanks for telling me. ", "I can offer today, 8 to 8:30, or other times. "],
                       [again])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        assert said(ask(ws, 1, ["Hi.", "Yes, that's right."])) == OFFER
        second = said(ask(ws, 2, ["Hi.", "Yes, that's right.", OFFER,
                                  "My mom's 85 and she's staying with me."]))
    assert second == DISPATCH



def test_an_empty_reply_after_the_caller_speaks_is_asked_again_once(socket):
    """Phone check b2r, 6 November: the model wrote nothing after the caller answered, and the
    caller sat in silence until "are you still there?"."""
    _, make = socket
    client, brain = make([([], []), (["Is it completely out, or just not keeping up?"], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "It's 88 Atlantic Avenue."])
    assert said(events) == "Is it completely out, or just not keeping up?"
    nudge = json.dumps(brain.requests[1][-1])
    assert "The caller is waiting for you" in nudge


def test_the_prompt_says_a_problem_that_still_runs_is_not_a_tune_up():
    """Verification call after the final fixes, 6 November: a banging, leaking boiler with the
    heat still on was booked as a tune-up (Low, no dispatch question); the A/B runs did the same
    4 times in 96."""
    text = (Path(__file__).resolve().parents[2] / "retell" / "socket" / "prompt.md").read_text()
    assert "even when it still runs" in text
    assert "`tune_up` is routine maintenance with nothing wrong" in text
    assert "Never say the job type or how you chose it." in text


def test_the_prompt_thanks_the_caller_after_the_name_and_asks_to_spell_a_garbled_one():
    """Jackson's call call_55b467b41ae58e725fa142d29b7: "Okay, none" for "O'Connell", then the next
    question with no thanks."""
    text = (Path(__file__).resolve().parents[2] / "retell" / "socket" / "prompt.md").read_text()
    assert 'say "Thanks." before your next question; never move on in silence' in text
    assert "Could you spell your last name for me?" in text


def test_server_led_a_server_sentence_the_model_already_said_is_not_said_again(server_led):
    """Jackson's call call_23b95cb04fd6cc9ea0cc084380e: the model worked out "by 11:17 PM" itself
    and said the callback line before the tool answered, then the server's identical line came
    after it, so the caller heard it twice."""
    server, make = server_led
    flag = {"id": "t1", "name": "flag_person", "input": {"reason": "callback"}}
    server.tools["flag_person"] = envelope(say=FLAGGED, data={"flag_id": 3})
    server.tools["flag_person"]["next"] = "If the sentence didn't ask already, ask 'Anything else?'."
    client, _brain = make([([FLAGGED + " "], [flag]), (["Anything else? "], [])])
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = ask(ws, 1, ["Hi.", "Call me back tomorrow morning."])
    assert said(events).count("someone will do their best to call you back") == 1, said(events)


def test_the_greeting_cannot_be_cut_off_by_a_stray_word(socket):
    """Call retell_call_44f21506eca3346bd3547be9de8: "there." three seconds in stopped the greeting at
    "Hi there, thanks for". The greeting is sent as one that can't be talked over."""
    _, make = socket
    client, _ = make()
    with client.websocket_connect(path()) as ws:
        assert json.loads(ws.receive_text())["response_type"] == "config"
        ws.send_text(json.dumps(DETAILS))
        begin = json.loads(ws.receive_text())
    assert begin["response_id"] == 0 and begin["no_interruption_allowed"] is True
