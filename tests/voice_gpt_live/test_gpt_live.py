"""GPT-Live's wiring, on the rig adapter and the phone path, against the real tool server: the
session carries the one-page prompts and our tools on the backend, caller and agent lines reach
the turn check, the backend's function calls go to the tool server and resume the backend, the
safety lines are asked for word for word, and the call ends only when end_call allows it."""

import asyncio
import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from evals.models import load_config
from tests.rig_kit import FakeListener, FakeSocket, seeded, tool_app, tools_called, turns, world_for
from voice.gpt_live import rig_adapter, sip
from voice.gpt_live import session as live

CALLER = "(212) 555-0999"
STARTED = {"type": "session.started", "session": {"id": "live_test"}}


@pytest.fixture(scope="module")
def seed_path(tmp_path_factory):
    return seeded(tmp_path_factory)


@pytest.fixture
def app(seed_path, tmp_path):
    return tool_app(seed_path, tmp_path)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(live, "LINE_GAP_S", 0.01)
    monkeypatch.setattr(live, "GOODBYE_WAIT_S", 0.05)
    monkeypatch.setattr(live, "GOODBYE_QUIET_S", 0.02)
    monkeypatch.setattr(live, "GOODBYE_TAIL_S", 0.0)
    monkeypatch.setattr(live, "POLL_S", 0.01)
    monkeypatch.setattr(rig_adapter, "CLOSE_WAIT_S", 0.05)


def entrant():
    return rig_adapter.Entrant(SimpleNamespace(config=load_config(), env={"OPENAI_API_KEY": "k"}))


async def opened(app, call_id="gl-1"):
    sock = FakeSocket([STARTED])

    async def connect(url, **kwargs):
        assert url == rig_adapter.WS_URL
        assert kwargs["additional_headers"]["Authorization"] == "Bearer k"
        return sock

    line = rig_adapter.Line(entrant(), connect=connect)
    listener = FakeListener()
    await line.open(call_id, CALLER, world_for(app), listener)
    return line, sock, listener


async def feed(line, *events):
    for event in events:
        await line.conversation.handle(event)


def caller(text):
    return {"type": "session.input_transcript.delta", "delta": text}


def agent(text):
    return {"type": "session.output_transcript.delta", "delta": text}


def backend(kind):
    return {"type": "response.event", "delegation_id": "d1", "event": {"type": kind,
                                                                       "response": {}}}


def function_call(name, args, call_id="call_1"):
    return {"type": "response.event", "delegation_id": "d1", "event": {
        "type": "response.output_item.done",
        "item": {"type": "function_call", "call_id": call_id, "name": name,
                 "arguments": json.dumps(args)}}}


def test_the_session_has_both_one_page_prompts_and_our_tools_on_the_backend(app):
    async def go():
        line, sock, _ = await opened(app)
        await line.close("test")
        return sock

    sock = asyncio.run(go())
    session = sock.of_type("session.start")[0]["session"]
    assert session["model"] == "gpt-live-1"
    assert "Summit Air" in session["instructions"]
    assert session["audio"]["format"] == {"type": "audio/pcm", "rate": 24000}
    backend = session["delegation"]["responses"]
    assert session["delegation"]["type"] == "responses"
    assert backend["parallel_tool_calls"] is False
    names = {t["name"] for t in backend["tools"]}
    assert {"check_service_area", "book_appointment", "end_call"} <= names
    assert all("call_id" not in t["parameters"]["properties"] for t in backend["tools"])
    assert "Company knowledge" in backend["instructions"]
    # One page each: the stock-build test is about a short prompt.
    for name in ("voice.md", "backend.md"):
        assert len((live.PROMPTS / name).read_text().split()) < 500


def test_the_greeting_is_set_at_start_and_asked_for_once_audio_runs(app):
    async def go():
        line, sock, _ = await opened(app)
        assert not sock.of_type("session.commentary.append")
        await line.send(b"\0\0")
        await line.send(b"\0\0")
        await line.close("test")
        return sock

    sock = asyncio.run(go())
    start = sock.of_type("session.start")[0]["session"]
    seeded_text = start["input"][0]["content"][0]["text"]
    assert "Thanks for calling Summit Air" in seeded_text
    greet = sock.of_type("session.commentary.append")
    assert len(greet) == 1 and greet[0]["content"] == seeded_text
    assert greet[0]["delegation_id"] is None


def test_caller_and_agent_lines_reach_the_turn_check_in_order(app):
    async def go():
        line, _, listener = await opened(app)
        await feed(line, caller("My heat "), caller("is out."), agent("Oh no. "),
                   agent("What's the address?"))
        await asyncio.sleep(0.05)
        await line.close("caller_hangup")
        return listener

    listener = asyncio.run(go())
    assert turns(app, "gl-1")[-2:] == [("caller", "My heat is out."),
                                       ("agent", "Oh no. What's the address?")]
    assert listener.said_lines == ["Oh no. What's the address?"]


def test_a_gas_smell_asks_for_the_signed_off_lines_word_for_word(app):
    async def go():
        line, sock, _ = await opened(app)
        await feed(line, caller("I smell gas in the kitchen."))
        await asyncio.sleep(0.05)
        await line.close("caller_hangup")
        return sock

    sock = asyncio.run(go())
    asked = sock.of_type("session.instructions.append")
    assert len(asked) == 1
    assert asked[0]["content"].startswith("Immediately say the following exactly and in full")
    assert "gas" in asked[0]["content"].lower()


def test_a_function_call_runs_on_the_tool_server_and_resumes_the_backend(app):
    async def go():
        line, sock, _ = await opened(app)
        await feed(line, caller("I'm in 11201."), function_call("check_service_area",
                                                               {"zip": "11201"}))
        await asyncio.sleep(0.1)
        await line.close("caller_hangup")
        return sock

    sock = asyncio.run(go())
    assert "check_service_area" in tools_called(app, "gl-1")
    output = sock.of_type("response.item.create")
    assert output[0]["item"]["type"] == "function_call_output"
    assert output[0]["item"]["call_id"] == "call_1"
    assert json.loads(output[0]["item"]["output"])["ok"] is True
    kinds = [m["type"] for m in sock.sent]
    assert kinds.index("response.create") > kinds.index("response.item.create")
    # The caller's line was posted before the tool ran, so the server checked it first.
    assert ("caller", "I'm in 11201.") in turns(app, "gl-1")


def test_a_tool_sentence_is_asked_for_word_for_word_and_kept_from_the_backend(app):
    async def go():
        line, sock, _ = await opened(app)
        await feed(line, backend("response.created"), function_call("read_back", {
            "address": {"street": "248 Duffield Street", "city": "Brooklyn", "zip": "11201"},
            "phone": "+12125550999"}), backend("response.completed"))
        await asyncio.sleep(0.1)
        # Nothing is said while the backend's run is still going.
        assert not sock.of_type("session.instructions.append")
        await feed(line, backend("response.created"), backend("response.completed"))
        await line.close("caller_hangup")
        return sock

    sock = asyncio.run(go())
    asked = sock.of_type("session.instructions.append")
    assert len(asked) == 1
    assert asked[0]["content"].startswith("Immediately say the following exactly and in full")
    output = json.loads(sock.of_type("response.item.create")[0]["item"]["output"])
    assert "say" not in output
    assert output["said_to_caller"] and output["said_to_caller"] in asked[0]["content"]


def test_only_the_last_tool_sentence_of_a_run_is_said(app):
    async def go():
        line, sock, _ = await opened(app)
        await feed(line, backend("response.created"),
                   function_call("check_service_area", {"zip": "11201"}, "call_a"),
                   backend("response.completed"))
        await asyncio.sleep(0.1)
        await feed(line, backend("response.created"),
                   function_call("find_slots", {"job_type": "tune_up"}, "call_b"),
                   backend("response.completed"))
        await asyncio.sleep(0.1)
        await feed(line, backend("response.created"), backend("response.completed"))
        await line.close("caller_hangup")
        return sock

    sock = asyncio.run(go())
    asked = sock.of_type("session.instructions.append")
    outputs = [json.loads(m["item"]["output"]) for m in sock.of_type("response.item.create")]
    assert len(asked) == 1 and outputs[-1]["said_to_caller"] in asked[0]["content"]


def test_a_repeated_function_call_runs_once(app):
    async def go():
        line, sock, _ = await opened(app)
        call = function_call("check_service_area", {"zip": "11201"})
        await feed(line, call, call)
        await asyncio.sleep(0.1)
        await line.close("caller_hangup")
        return sock

    sock = asyncio.run(go())
    assert len(sock.of_type("response.item.create")) == 1


def test_end_call_after_a_goodbye_drops_the_line(app):
    async def go():
        line, sock, listener = await opened(app)
        await feed(line, caller("Okay, bye."),
                   function_call("end_call", {"reason": "caller_goodbye"}))
        await asyncio.sleep(0.05)
        await feed(line, agent("Thanks for calling Summit Air."))
        await asyncio.sleep(0.2)
        return sock, listener

    sock, listener = asyncio.run(go())
    assert sock.of_type("session.close")
    assert sock.closed and listener.hung


def test_a_tool_the_server_does_not_have_is_refused_without_a_request(app):
    async def go():
        line, sock, _ = await opened(app)
        await feed(line, function_call("transfer_money", {}))
        await asyncio.sleep(0.05)
        await line.close("caller_hangup")
        return sock

    sock = asyncio.run(go())
    assert json.loads(sock.of_type("response.item.create")[0]["item"]["output"]) == {
        "ok": False, "refused": "unknown_tool"}


def test_cost_is_voice_seconds_plus_backend_tokens(app):
    async def go():
        line, _, _ = await opened(app)
        await feed(line, {"type": "session.usage.updated", "usage": {"seconds": 60}},
                   {"type": "response.event", "event": {"type": "response.completed",
                    "response": {"usage": {"input_tokens": 1_000_000, "output_tokens": 0,
                                           "input_tokens_details": {"cached_tokens": 0}}}}})
        cost = await line.cost()
        await line.close("caller_hangup")
        return cost

    cost = asyncio.run(go())
    assert cost == pytest.approx({"platform": 0.05, "brain_model": 0.20})


# ---- the phone path --------------------------------------------------------------------------

SECRET = "whsec_" + base64.b64encode(b"test-secret-bytes").decode()


def signed(body: bytes, wid="wh_1", stamp=None) -> dict:
    stamp = str(int(time.time()) if stamp is None else stamp)
    key = base64.b64decode(SECRET.removeprefix("whsec_"))
    mac = hmac.new(key, f"{wid}.{stamp}.".encode() + body, hashlib.sha256).digest()
    return {"webhook-id": wid, "webhook-timestamp": stamp,
            "webhook-signature": "v1," + base64.b64encode(mac).decode()}


def test_webhook_signatures_are_checked():
    body = b'{"type": "live.transport.incoming"}'
    assert sip.verify(SECRET, signed(body), body)
    assert not sip.verify(SECRET, signed(body), body + b" ")
    assert not sip.verify(SECRET, signed(body, stamp=int(time.time()) - 3600), body)
    assert not sip.verify("", signed(body), body)


def test_the_caller_number_comes_from_the_sip_from_header():
    headers = [{"name": "From", "value": '"Pat" <sip:+12125550999@pstn.twilio.com>;tag=1'},
               {"name": "To", "value": "sip:+16466240418@sip.api.openai.com"}]
    assert sip.number(headers, "From") == "+12125550999"
    assert sip.number(headers, "to") == "+16466240418"
    assert sip.number([], "From") == ""


def test_a_hangup_we_asked_for_is_the_agents_even_if_reported_as_remote():
    closed = {"type": "session.closed", "reason": "remote_hangup"}
    assert sip.end_reason(closed, hung_up=True) == "agent_end"
    assert sip.end_reason(closed) == "caller_hangup"
    assert sip.end_reason(None) == "dropped"


class FakeApi:
    """OpenAI's Live call-control endpoints."""

    def __init__(self):
        self.posts: list[tuple[str, dict | None]] = []

    async def post(self, url, headers=None, json=None):
        self.posts.append((url, json))
        return httpx.Response(200)


def phone_for(app, sock):
    async def connect(url, **kwargs):
        assert url == sip.ATTACH.format(id="live_sip1")
        return sock

    server = world_for(app).server
    cfg = load_config()["realtime"]["gpt_live"]
    return sip.Phone("k", SECRET, server, cfg, FakeApi(), connect=connect)


def incoming(session_id="live_sip1") -> dict:
    return {"type": "live.transport.incoming", "data": {
        "type": "sip", "session_id": session_id,
        "sip_headers": [{"name": "From", "value": "sip:+12125550999@pstn.twilio.com"},
                        {"name": "To", "value": "sip:+16466240418@sip.api.openai.com"}]}}


def test_an_incoming_call_is_started_accepted_and_attached(app):
    sock = FakeSocket([{"type": "session.closed", "reason": "remote_hangup",
                        "usage": {"seconds": 30}}])
    phone = phone_for(app, sock)

    async def go():
        await phone.answer("live_sip1", incoming()["data"])

    asyncio.run(go())
    url, body = phone.api.posts[0]
    assert url.endswith("/live_sip1/accept")
    assert body["session"]["type"] == "live"
    assert "format" not in body["session"]["audio"]
    assert sock.of_type("session.commentary.append")
    trace = world_for(app).server.trace("gptlive_live_sip1")
    assert trace["call"]["end_reason"] == "caller_hangup"


def test_the_webhook_refuses_a_bad_signature_and_skips_a_repeat(app):
    phone = phone_for(app, FakeSocket())
    seen = []
    phone.incoming = seen.append
    client = TestClient(sip.build_app(phone))
    body = json.dumps(incoming()).encode()
    assert client.post("/gpt-live/webhook", content=body,
                       headers={**signed(body), "webhook-signature": "v1,bad"}).status_code == 401
    assert client.post("/gpt-live/webhook", content=body, headers=signed(body)).status_code == 200
    assert client.post("/gpt-live/webhook", content=body, headers=signed(body)).status_code == 200
    assert len(seen) == 1
