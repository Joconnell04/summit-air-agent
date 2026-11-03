"""Turn helpers: the longer wait after asking for digits (C-48), stage timings (docs/latency.md)."""

import pytest

from voice.livekit.settings import load_profile
from voice.livekit.turns import (
    agent_timings,
    asked_for_digits,
    caller_timings,
)


@pytest.mark.parametrize(
    "line",
    [
        "What's the best number to reach you?",
        "And the ZIP code there?",
        "Which one, the one ending in what? Can you give me the last four?",
    ],
)
def test_asking_for_digits_is_noticed(line):
    assert asked_for_digits(line)


@pytest.mark.parametrize(
    "line",
    [
        "So that's 646, 624, 0418.",  # a read-back is not a question for digits
        (
            "Let me read that back: 142 Union Street, Brooklyn, 11231, and the best number is "
            "646, 624, 0418. Is that right?"
        ),
        "What's going on with the heat?",
        "Got it, thanks.",
    ],
)
def test_other_lines_keep_the_normal_wait(line):
    assert not asked_for_digits(line)


def test_caller_timings_are_in_ms_and_skip_what_is_missing():
    assert caller_timings({"transcription_delay": 0.25}) == {"transcript_ms": 250.0}


def test_reply_delay_runs_from_the_caller_going_quiet_to_the_first_audio():
    timings = agent_timings({"started_speaking_at": 12.0}, 10.8, tool_ms=0.0, bridge=False)
    assert timings["reply_ms"] == 1200.0
    assert agent_timings({}, None, 5.0, True) == {"tool_ms": 5.0, "bridge": 1.0}


def test_the_profile_is_switched_by_one_setting(monkeypatch):
    assert load_profile().provider == "anthropic"
    monkeypatch.setenv("MODEL_PROFILE", "openai-fast")
    assert load_profile().provider == "openai"
    assert load_profile().thinking is False
