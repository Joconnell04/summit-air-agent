"""The side model for the vulnerable-person read (app/side_model.py, DECISIONS.md 006): asked on
each caller line, read by the priority gate, and never able to lower priority or hold up a call."""

import json
import threading

import httpx
import pytest
from toolkit import UNKNOWN, Agent, make_server

from app import side_model
from app.settings import Settings

QUESTION = {"instructions": "Is someone at home at risk?", "system": "Answer with JSON."}


def asker(provider: str, reply: dict, key: str = "test-key"):
    seen = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=reply)

    cfg = {"provider": provider, "model": "m", "key_env": "X", "price": {"input": 1, "output": 2}}
    http = httpx.Client(transport=httpx.MockTransport(handle))
    return side_model.Asker(cfg, QUESTION, http=http, key=key), seen


def test_each_backend_reads_the_probability_of_yes_and_counts_tokens():
    claude, seen = asker("anthropic", {
        "content": [{"type": "text", "text": '{"probability_yes": 85}'}],
        "usage": {"input_tokens": 400, "output_tokens": 10},
    })
    assert claude("my mom is 90") == 0.85
    assert seen["body"]["thinking"] == {"type": "disabled"}
    assert claude.tokens == [400, 10] and claude.cost_usd() == pytest.approx(0.00042)

    openai, seen = asker("openai", {
        "answers": [{"name": "answer", "type": "predicate", "probability": 0.2}],
        "usage": {"input_tokens": 200},
    })
    assert openai("no one's home") == 0.2
    assert seen["url"].endswith("/v1/decisions")
    assert seen["body"]["questions"][0]["instructions"] == QUESTION["instructions"]

    jev, seen = asker("typesafe", {"answers": {"answer": {"noul": 0.97}}, "usage": {}})
    assert jev("she's on dialysis") == 0.97
    assert seen["url"].endswith("/v1/systemone")
    assert seen["body"]["questions"]["answer"]["type"] == "noul"


def test_a_refused_answer_or_a_missing_key_raises():
    refused, _ = asker("openai", {"answers": [{"name": "answer", "type": "refusal"}]})
    with pytest.raises(ValueError):
        refused("text")
    keyless, _ = asker("typesafe", {}, key="")
    with pytest.raises(RuntimeError):
        keyless("text")


def test_an_answer_asked_early_is_reused_by_the_gate():
    asked = []
    model = side_model.SideModel(lambda q, text: asked.append((q, text)) or 0.9, wait_s=1)
    model.prefetch("q", "my dad is on oxygen")
    assert model.score("q", "my dad is on oxygen") == 0.9
    assert asked == [("q", "my dad is on oxygen")]


def test_a_late_or_failed_answer_is_no_answer():
    release = threading.Event()
    slow = side_model.SideModel(lambda q, text: release.wait(5) and 0.9, wait_s=0.05)
    assert slow.score("q", "text") is None
    release.set()

    def broken(q, text):
        raise httpx.ConnectError("down")

    assert side_model.SideModel(broken).score("q", "text") is None
    assert side_model.SideModel(broken).score("q", "  ") is None


def test_side_model_is_off_unless_named():
    assert Settings().side_model == ""
    assert side_model.from_settings("") is None
    with pytest.raises(ValueError):
        side_model.load_profile("no-such-profile")
    assert side_model.load_profile("openai-decisions")["key_env"] == "OPENAI_API_KEY"
    assert side_model.load_question("vulnerable_person")["instructions"]


class FakeSideModel:
    def __init__(self, value):
        self.value = value
        self.prefetched = []
        self.scored = []

    def prefetch(self, question, text):
        self.prefetched.append((question, text))

    def score(self, question, text):
        self.scored.append(text)
        return self.value


@pytest.fixture
def server(tmp_path, clock, mock):
    def build(value):
        fake = FakeSideModel(value)
        return Agent(make_server(tmp_path, clock, mock[0], side_model=fake), clock), fake

    return build


def test_a_caller_line_is_sent_to_the_side_model_as_it_arrives(server):
    agent, fake = server(0.1)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The boiler quit overnight.")
    agent.turn(call, "My mother-in-law moved in after her stroke.")
    agent.say(call, "Thanks, let me look.")
    assert fake.prefetched == [
        ("vulnerable_person", "The boiler quit overnight."),
        ("vulnerable_person", "The boiler quit overnight.\nMy mother-in-law moved in after her stroke."),
    ]


def test_a_yes_from_the_side_model_raises_a_no_heat_call_the_rules_left_at_high(server):
    agent, _ = server(0.95)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Our boiler quit, and my wife is on dialysis three days a week.")
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 61})
    assert found["data"]["priority"]["level"] == "Urgent"
    assert (
        "someone at home at risk from the heat or cold, from the side model"
        in found["data"]["priority"]["reasons"]
    )


def test_the_after_wait_comparison_never_asks_the_side_model_again(server):
    # PR #126 review: a second ask on the shorter text has nothing prefetched and could stall.
    agent, fake = server(0.95)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Our boiler quit, it's 61 in here.")
    first = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 61})
    agent.say(call, first["say"])
    agent.turn(call, "Oh, and my wife is on dialysis three days a week.")
    fake.scored.clear()
    agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 61})
    assert len(fake.scored) == 1


def test_the_side_model_only_applies_to_no_heat_and_no_cooling(server):
    agent, _ = server(0.95)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "I need my yearly tune-up. My dad lives with us, he's 91.")
    found = agent.tool("find_slots", call, job_type="tune_up")
    assert found["data"]["priority"]["level"] == "Normal"


@pytest.mark.parametrize("value", [None, 0.1])
def test_no_answer_or_a_no_leaves_the_rules_to_decide(server, value):
    agent, _ = server(value)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The heat's out and my grandmother is staying with us.")
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 61})
    assert found["data"]["priority"]["level"] == "Urgent", "a rule's raise stands"
    other = agent.start("(212) 555-0901")["data"]["call_id"]
    agent.turn(other, "The heat's out.")
    found = agent.tool("find_slots", other, job_type="no_heat", facts={"inside_temp_f": 61})
    assert found["data"]["priority"]["level"] == "High"


def test_find_slots_doesnt_wait_on_a_slow_side_model_and_the_booking_does(tmp_path, clock, mock):
    """find_slots was the slowest tool (MEASURED, 528 ms average on the live world); it no longer
    waits for the side model's answer, and book_appointment, which does, raises the priority when
    the answer comes in time for it."""
    import time

    from toolkit import book, first_open, ready_to_book

    release = threading.Event()
    slow = side_model.SideModel(lambda q, text: release.wait(5) and 0.95, wait_s=2)
    agent = Agent(make_server(tmp_path, clock, mock[0], side_model=slow), clock)
    call = ready_to_book(agent)
    agent.turn(call, "Our boiler quit, and my wife is on dialysis three days a week.")
    started = time.perf_counter()
    found = agent.tool("find_slots", call, job_type="no_heat", facts={"inside_temp_f": 61})
    assert time.perf_counter() - started < 1.0, "find_slots waited on the side model"
    assert "from the side model" not in " ".join(found["data"]["priority"]["reasons"])
    release.set()
    booked = book(agent, call, first_open(agent, call, "no_heat"), job_type="no_heat",
                  inside_temp_f=61)
    assert booked["ok"], booked
    assert booked["data"]["priority"]["level"] == "Urgent"
