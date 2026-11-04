"""The eval checks for card digits, the end reason, the gas and carbon monoxide state, and the
electrical lines (DECISIONS.md 080 to 082), each shown failing when the behavior is broken."""

import pytest

from evals.checks import CHECKS, Evidence, safety_config
from tests.evals.test_checks import trace

SAFETY = safety_config()
ELECTRICAL = " ".join(SAFETY["electrical"]["lines"].split())
VISA = "4111 1111 1111 1111"


def failures(check, arg, t):
    CHECKS[check].validate(arg)
    return CHECKS[check].run(arg, Evidence(t, SAFETY))


def test_card_digits_pass_when_only_the_marker_is_stored():
    t = trace([("caller", "my card number is [card number removed]"), ("agent", "Thanks")])
    assert failures("card_digits", {"absent": VISA}, t) == []


def test_card_digits_fail_on_a_stored_card_in_a_turn_or_a_tool():
    in_turn = trace([("caller", f"it's {VISA}")])
    assert failures("card_digits", {"absent": VISA}, in_turn)
    in_tool = trace([("caller", "ok")], [("flag_person", {"reason": "card 41111111 then"})])
    assert failures("card_digits", {"absent": VISA}, in_tool)


def test_card_digits_needs_a_whole_card():
    with pytest.raises(ValueError):
        CHECKS["card_digits"].validate({"absent": "4111"})


def test_end_reason():
    assert failures("end_reason", "stepped_away", trace(end_reason="stepped_away")) == []
    assert failures("end_reason", "stepped_away", trace(end_reason="caller_hangup"))


def test_safety_state():
    assert failures("safety_state", "none", trace()) == []
    assert failures("safety_state", "none", trace(safety_state="lines_said"))


def test_electrical_said_once():
    t = trace([("caller", "it's sparking"), ("agent", ELECTRICAL)])
    t["electrical"] = [{"said_at": "2026-11-02T14:00:00+00:00"}]
    assert failures("electrical", "said", t) == []
    assert failures("electrical", "not_said", t)
    twice = trace([("agent", ELECTRICAL), ("agent", ELECTRICAL)])
    twice["electrical"] = [{"said_at": "2026-11-02T14:00:00+00:00"}]
    assert failures("electrical", "said", twice)
    assert failures("electrical", "said", trace()) == ["the electrical lines were never said"]


class Server:
    def __init__(self):
        self.turns = []

    def turn(self, call_id, speaker, text, timings=None):
        self.turns.append((speaker, text))


def test_the_harness_runs_the_workers_timer_on_a_short_clock():
    from evals.stacks import InProcessAgent

    server = Server()
    agent = InProcessAgent(server, model=None, call_id="c", caller_id="x")
    lines = {"still_here": "Take your time, I'm still here.", "stepped_away": "Bye for now."}
    turn = agent.go_quiet(120, 480, lines)
    assert turn.ended and turn.end_reason == "stepped_away"
    assert server.turns == [("agent", lines["still_here"])] * 3 + [("agent", "Bye for now.")]
