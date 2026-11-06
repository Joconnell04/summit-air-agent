"""SONNET_HANDOFF=1: the reply after a lookup_hvac_info that found facts is written by the quality
model, at most twice a call, with the tenant's filler line first and a fallback to the live model
when the quality model's first word is slow. The models and the tool server are stand-ins."""

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from test_rt_llm_socket import (
    API_KEY,
    FakeBrain,
    FakeToolServer,
    ask,
    envelope,
    open_call,
    path,
    said,
)

from voice.archive.livekit.settings import ROOT
from voice.retell_llm import llm_socket

FILLER = "Good question, let me check on that."
LOOKUP = {"id": "t1", "name": "lookup_hvac_info", "input": {"question": "why does it bang"}}
FACTS = envelope(data={"facts": ["Steam pipes bang when water pools in them."],
                       "log": {"files": ["heating.md"], "search": "embedding"}})


@pytest.fixture
def handoff(monkeypatch, tmp_path):
    monkeypatch.setenv("RETELL_API_KEY", API_KEY)
    monkeypatch.setenv("SONNET_HANDOFF", "1")
    server = FakeToolServer()
    server.tools["lookup_hvac_info"] = FACTS

    def make(live, quality, timeout_s=None):
        if timeout_s is not None:
            config = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
            config["voice"]["sonnet_handoff"]["first_token_timeout_s"] = timeout_s
            changed = tmp_path / "models.yaml"
            changed.write_text(yaml.safe_dump(config))
            monkeypatch.setenv("MODELS_CONFIG", str(changed))
        fast, strong = FakeBrain(live), FakeBrain(quality[0], quality[1] if len(quality) > 1 else ())
        app = llm_socket.create_app(brain=fast, transport=httpx.MockTransport(server.handle),
                                    quality_brain=strong)
        return TestClient(app), fast, strong

    return server, make


def turn(ws, response_id, *lines):
    return ask(ws, response_id, ["Hi.", *lines])


def test_the_reply_after_a_lookup_with_facts_is_the_quality_models(handoff):
    _, make = handoff
    client, fast, strong = make([([], [LOOKUP])], ([(["Water pools in the pipes. "], [])],))
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = turn(ws, 1, "Why does my radiator bang?")
    assert said(events) == f"{FILLER} Water pools in the pipes."
    assert len(fast.requests) == 1 and len(strong.requests) == 1
    # The instruction rides on this one request, and the facts it answers from are in the result.
    last = strong.requests[0][-1]["content"]
    assert "never say where the facts came from" in last[-1]["text"].lower()
    assert "Steam pipes bang" in last[0]["content"]
    # The model is never shown the log's file names.
    assert "heating.md" not in last[0]["content"]


def test_nothing_changes_while_the_switch_is_off(handoff, monkeypatch):
    _, make = handoff
    monkeypatch.delenv("SONNET_HANDOFF")
    client, _fast, strong = make([([], [LOOKUP]), (["Water pools. "], [])], ([],))
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = turn(ws, 1, "Why does my radiator bang?")
    assert said(events) == "Water pools."
    assert not strong.requests


def test_a_lookup_with_no_facts_stays_with_the_live_model(handoff):
    server, make = handoff
    server.tools["lookup_hvac_info"] = envelope(data={"facts": []})
    client, _fast, strong = make([([], [LOOKUP]), (["I can send a technician. "], [])], ([],))
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = turn(ws, 1, "Why does my radiator bang?")
    assert said(events) == "I can send a technician."
    assert not strong.requests


def test_only_a_lookup_hands_the_reply_over(handoff):
    _server, make = handoff
    find = {"id": "t2", "name": "find_customer", "input": {}}
    client, _fast, strong = make([([], [find]), (["Got it. "], [])], ([],))
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        turn(ws, 1, "It's Sam.")
    assert not strong.requests


def test_two_handoffs_a_call_and_the_filler_never_twice_in_a_row(handoff):
    _, make = handoff
    live = [([], [LOOKUP]), ([], [LOOKUP]), ([], [LOOKUP]), (["Third by the live model. "], [])]
    quality = [(["First. "], []), (["Second. "], [])]
    client, _fast, strong = make(live, (quality,))
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        first = turn(ws, 1, "Why does it bang?")
        second = ask(ws, 2, ["Hi.", "Why does it bang?", "First.", "And why is it cold?"])
        third = ask(ws, 3, ["Hi.", "Why does it bang?", "First.", "And why is it cold?", "Second.",
                            "And the mini split?"])
    assert said(first) == f"{FILLER} First."
    assert said(second) == "Second."
    assert said(third) == "Third by the live model."
    assert len(strong.requests) == 2


def test_a_slow_first_word_falls_back_to_the_live_model(handoff):
    _, make = handoff
    client, fast, _strong = make(
        [([], [LOOKUP]), (["Water pools in the pipes. "], [])],
        ([(["Too late. "], [])], [1.0]),
        timeout_s=0.05,
    )
    with client.websocket_connect(path()) as ws:
        open_call(ws)
        events = turn(ws, 1, "Why does my radiator bang?")
    assert said(events) == f"{FILLER} Water pools in the pipes."
    assert len(fast.requests) == 2
    # The instruction is only on the quality request, never in the live model's history.
    assert all("never say where" not in str(m) for m in fast.requests[1])
