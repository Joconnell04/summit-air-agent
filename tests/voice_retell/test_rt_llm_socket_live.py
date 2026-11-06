"""The socket against the real tool server and mock ServiceTitan, in process (reviewer gap 1): only
the model's replies are scripted. Every tool result, gate, sentence and record is the server's own,
so a socket that drops a field the server needs, or misreads a result, fails here (the read_back
job_type bug passed every test that used a hand-written tool server)."""

import json
import shutil
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from test_rt_llm_socket import API_KEY, FakeBrain, path
from toolkit import NOW, START, TENANT_DIR, Clock, in_area_address, make_server

from app.tenant import load_agent
from servicetitan_mock.app import create_app as create_mock
from servicetitan_mock.seed import build_world
from voice.retell_llm import llm_socket

AGENT_CFG = load_agent(TENANT_DIR)[0]
GREETING = "Hi there, thanks for calling Summit Air! I'm Brian, the virtual assistant. How can I help?"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    path = tmp_path_factory.mktemp("st") / "world.db"
    build_world(path, TENANT_DIR, START)
    return path


class Live:
    """One socket call on a real tool server: the transcript Retell would send, the socket's
    replies, and the server's records."""

    def __init__(self, tmp_path, world, monkeypatch, replies, clock=None, geocoder=None):
        db = tmp_path / "world.db"
        shutil.copy(world, db)
        self.clock = clock or Clock()
        mock = create_mock(db, TENANT_DIR, tmp_path / "world.log", clock=self.clock)
        settings = {"geocoder": geocoder} if geocoder else {}
        self.server = make_server(tmp_path, self.clock, mock, **settings)
        self.http = TestClient(self.server)
        monkeypatch.setenv("RETELL_API_KEY", API_KEY)
        monkeypatch.setenv("RETELL_LLM_SERVER_LED", "1")
        self.brain = FakeBrain(replies)
        app = llm_socket.create_app(brain=self.brain,
                                    transport=httpx.ASGITransport(app=self.server))
        self.client = TestClient(app)
        self.call_id = "retell_live1"
        started = self.http.post("/calls/start", json={
            "call_id": self.call_id, "caller_id": "+12125550999", "dialed_number": "+15162129051",
            "flow": "server_led"}).json()
        assert started["ok"], started
        self.transcript = [{"role": "agent", "content": GREETING}]
        self.rid = 0

    def __enter__(self):
        self.ws = self.client.websocket_connect(path()).__enter__()
        assert json.loads(self.ws.receive_text())["response_type"] == "config"
        self.ws.send_text(json.dumps({"interaction_type": "call_details", "call": {
            "call_type": "phone_call", "metadata": {"summit_call_id": self.call_id},
            "retell_llm_dynamic_variables": {"summit_call_id": self.call_id,
                                             "instructions": "x", "greeting": GREETING}}}))
        self.ws.receive_text()
        return self

    def __exit__(self, *exc):
        self.ws.__exit__(*exc)

    def caller(self, text: str, heard: str | None = None) -> tuple[str, list[dict]]:
        """The caller's line; the agent's reply as said, and its events. `heard` stands in for
        what Retell reports the caller heard of the reply (a cut-off one)."""
        self.transcript.append({"role": "user", "content": text})
        self.rid += 1
        self.ws.send_text(json.dumps({"interaction_type": "response_required",
                                      "response_id": self.rid, "transcript": self.transcript}))
        events = []
        while True:
            event = json.loads(self.ws.receive_text())
            events.append(event)
            if event.get("content_complete"):
                break
        said = "".join(e["content"] for e in events).strip()
        if said:
            self.transcript.append({"role": "agent", "content": heard or said})
        return said, events

    def rows(self, sql: str, *params) -> list[dict]:
        conn = self.server.state.services.connect()
        try:
            return [dict(r) for r in conn.execute(sql, params)]
        finally:
            conn.close()


def results(brain: FakeBrain, request: int) -> list[dict]:
    return [json.loads(b["content"]) for m in brain.requests[request]
            if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"]]


def read_back(job_type="no_heat"):
    return {"id": "rb", "name": "read_back",
            "input": {"address": in_area_address(), "phone": "caller_id", "job_type": job_type}}


@pytest.mark.parametrize("hours_later", [0, 13], ids=["daytime", "after_hours"])
def test_an_at_risk_no_heat_call_is_dispatched_at_the_read_back(tmp_path, world, monkeypatch,
                                                                hours_later):
    clock = Clock(NOW + timedelta(hours=hours_later))
    live = Live(tmp_path, world, monkeypatch, [
        (["Oh no, sorry to hear that. What's the street address?"], []),
        ([], [read_back()]),
        (["Is anyone home?"], []),
    ], clock=clock)
    address = in_area_address()
    with live:
        live.caller("Hi, my heat's out and my mother is 82, she's here with me.")
        said, _ = live.caller(f"It's {address['street']}, {address['zip']}, and this number.")
        live.caller("Yes, that's right.")
    sent = live.rows("SELECT say FROM tool_calls WHERE tool = 'read_back' ORDER BY id")[0]["say"]
    # The dispatch sentence is spoken word for word, connector included.
    assert said == sent and said.endswith("Is that the right address?")
    assert live.rows("SELECT COUNT(*) AS n FROM dispatches")[0]["n"] == 1
    # The model is told the dispatch's next step, not the flow's "call find_slots" (reviewer gap 2).
    told = results(live.brain, 2)[0]
    assert "Will someone be home" in told["next"] and "find_slots" not in told["next"]
    assert told["said_to_caller"] == sent


def test_an_address_the_city_doesnt_have_books_unverified_and_goes_on(tmp_path, world,
                                                                      monkeypatch):
    from test_ts_address_lookup import Book, lookup

    # A numbered street with no East or West that the city has neither way (Jackson's
    # "$199", "4th Street"): the borough is asked once, never spelled, never the ZIP.
    address = {"id": "fa", "name": "find_address", "input": {"street": "199 4th Street"}}
    in_borough = {"id": "fb", "name": "find_address",
                  "input": {"street": "199 4th Street", "borough": "Brooklyn"}}
    live = Live(tmp_path, world, monkeypatch, [
        (["Oh no. What's the street address?"], []),
        ([], [address]),
        ([], [in_borough]), (["What's the best phone number to reach you?"], []),
    ], geocoder=lookup([("book", Book({}))]))
    with live:
        live.caller("My heat's out.")
        asked, _ = live.caller("It's 199 4th Street.")
        booked, _ = live.caller("Brooklyn.")
    assert asked == AGENT_CFG["lines"]["address_ask_borough"]
    # The unverified line, then straight on to the next question (reviewer gap 4).
    assert booked.startswith("I can't find that one in the city's records")
    assert booked.endswith("What's the best phone number to reach you?")
    assert "ZIP" not in asked + booked


def test_do_not_call_is_confirmed_then_undone(tmp_path, world, monkeypatch):
    mark = {"id": "d1", "name": "mark_do_not_call", "input": {}}
    yes = {"id": "d2", "name": "mark_do_not_call", "input": {"confirmed": True}}
    undo = {"id": "d3", "name": "mark_do_not_call", "input": {"undo": True}}
    live = Live(tmp_path, world, monkeypatch, [
        (["What can I help with today?"], []), ([], [mark]),
        # A statement from the server leaves the model to carry on, as its next says.
        ([], [yes]), (["Anything else?"], []),
        ([], [undo]), (["Anything else?"], [])])
    with live:
        live.caller("Hi.")
        asked, _ = live.caller("Honestly, don't call me anymore.")
        marked, _ = live.caller("Yeah.")
        listed = live.rows("SELECT COUNT(*) AS n FROM do_not_call")[0]["n"]
        undone, _ = live.caller("Actually wait, you can still call me.")
    assert asked.endswith("?") and marked and undone
    assert listed == 1
    assert live.rows("SELECT COUNT(*) AS n FROM do_not_call")[0]["n"] == 0


def test_a_callback_goes_on_the_weekday_the_caller_named(tmp_path, world, monkeypatch):
    flag = {"id": "f1", "name": "flag_person", "input": {
        "reason": "Billing question", "callback_day": "tomorrow", "callback_before": "12 PM"}}
    live = Live(tmp_path, world, monkeypatch, [
        (["What can I help with today?"], []), ([], [flag]), (["Anything else?"], [])])
    with live:
        live.caller("Hi.")
        said, _ = live.caller("Can someone call me back Thursday morning about a bill?")
    assert "Thursday" in said and "tomorrow" not in said


def test_gas_advice_cut_off_is_said_again_once_and_cant_be_cut(tmp_path, world, monkeypatch):
    co = " ".join(AGENT_CFG["safety"]["kinds"]["co"]["lines"].split())
    live = Live(tmp_path, world, monkeypatch, [(["Can someone come out today?"], [])])
    with live:
        first, events = live.caller("Hi, my carbon monoxide alarm is going off.", heard="If a CO")
        again, again_events = live.caller("Sorry, what do I do about the furnace though?")
    assert first == co and events[0]["no_interruption_allowed"] is True
    assert again == co and again_events[0]["no_interruption_allowed"] is True


def test_the_call_gets_its_label_when_the_socket_closes(tmp_path, world, monkeypatch):
    live = Live(tmp_path, world, monkeypatch, [(["What are you calling about today?"], [])])
    with live:
        live.caller("Hi.")
    # Retell closing the socket ends the call on the tool server, which labels it.
    for _ in range(50):
        if live.rows("SELECT label FROM call_labels WHERE call_id = ?", live.call_id):
            break
        import time
        time.sleep(0.05)
    assert live.rows("SELECT label FROM call_labels WHERE call_id = ?", live.call_id)
