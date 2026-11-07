"""Our stack on the audio rig: the rig joins a room the way the phone line does, and the call's
Claude tokens are read from what the worker logged."""

import jwt
import pytest

from voice.livekit import settings
from voice.livekit.rig_adapter import (
    DIALED,
    caller_token,
    merged,
    token_counts,
    trial_config,
)


def test_the_rig_joins_as_the_phone_line_with_the_callers_number():
    token = caller_token("rig-call-1", "(212) 555-0999", "key", "secret-" + "x" * 32)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["kind"] == "sip"
    assert claims["attributes"] == {"sip.phoneNumber": "(212) 555-0999",
                                    "sip.trunkPhoneNumber": DIALED}
    assert claims["video"]["room"] == "rig-call-1" and claims["video"]["roomJoin"] is True


def test_tokens_are_summed_from_the_agent_lines_only():
    trace = {"events": [
        {"type": "turn", "speaker": "agent", "timings": {"prompt_tokens": 9000,
                                                         "cache_read_tokens": 8800,
                                                         "llm_output_tokens": 40}},
        {"type": "turn", "speaker": "caller", "timings": {"prompt_tokens": 5}},
        {"type": "turn", "speaker": "agent", "timings": {}},
        {"type": "tool", "tool": "check_service_area"},
    ]}
    assert token_counts(trace) == {"prompt": 9000, "read": 8800, "write": 0, "out": 40}


@pytest.mark.parametrize("name", ["rig-a", "rig-b"])
def test_each_call_gets_its_own_room(name):
    claims = jwt.decode(caller_token(name, "1", "k", "s" * 40), options={"verify_signature": False})
    assert claims["video"]["room"] == name


def test_a_trial_config_changes_only_the_named_voice_values(tmp_path, monkeypatch):
    changes = {"vad": {"min_silence_s": 0.2}, "endpointing": {"normal": {"min_delay": 0.1}}}
    path = trial_config(changes, tmp_path)
    monkeypatch.setenv("MODELS_CONFIG", str(path))
    voice = settings.load_voice()
    assert voice["vad"] == {"min_silence_s": 0.2, "activation": 0.5}
    assert voice["endpointing"]["normal"] == {"min_delay": 0.1, "max_delay": 1.5}
    assert voice["endpointing"]["digits"] == {"min_delay": 0.9, "max_delay": 3.0}
    assert settings.load_profile("fast").model == "claude-haiku-5-5"


def test_without_models_config_the_worker_reads_the_repo_file(monkeypatch):
    monkeypatch.delenv("MODELS_CONFIG", raising=False)
    assert settings.config_path() == settings.ROOT / "config/models.yaml"


def test_merge_lays_changes_over_every_level():
    assert merged({"a": {"b": 1, "c": 2}, "d": 3}, {"a": {"b": 9}}) == {"a": {"b": 9, "c": 2}, "d": 3}
