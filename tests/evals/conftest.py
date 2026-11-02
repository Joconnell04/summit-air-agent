import copy

import pytest

from evals.results import Run

SCENARIO = {
    "scenario_id": "no-heat-elderly-wrong-zip",
    "persona": "Adult child calling for their 84-year-old father; anxious, talks fast.",
    "flow": ["booking", "priority"],
    "difficulty": "rigorous",
    "traits": ["stutter", "late-correction"],
    "passed": True,
    "must_pass_failures": [],
    "grader_score": 4,
    "reply_delay_p50_ms": 820.0,
    "reply_delay_p90_ms": 1450.5,
    "cost_usd": 0.12,
    "transcript": [
        {"role": "caller", "text": "Hi, um, my dad's heat is out"},
        {"role": "agent", "text": "I'm sorry to hear that. What's the address?"},
    ],
    "tool_calls": [{"name": "lookup_customer", "arguments": {"phone": "7185550147"}}],
    "call_id": "call-0001",
}

RUN = {
    "run_id": "20261102-1",
    "created": "2026-11-02T15:30:00Z",
    "suite": "smoke",
    "tags": [],
    "stack": "livekit",
    "profile": "fast",
    "hash": "abc123",
    "scenarios": [SCENARIO],
}


@pytest.fixture
def scenario_data():
    def build(**overrides) -> dict:
        return {**copy.deepcopy(SCENARIO), **overrides}

    return build


@pytest.fixture
def run_data():
    def build(**overrides) -> dict:
        return {**copy.deepcopy(RUN), **overrides}

    return build


@pytest.fixture
def make_run(run_data, scenario_data):
    """A valid Run with one scenario per id given."""

    def build(scenario_ids=("s1",), **overrides) -> Run:
        scenarios = [
            scenario_data(scenario_id=sid, call_id=f"call-{sid}") for sid in scenario_ids
        ]
        return Run.model_validate(run_data(scenarios=scenarios, **overrides))

    return build
