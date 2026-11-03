"""Our stack on the audio rig: the rig joins a room the way the phone line does, and the call's
Claude tokens are read from what the worker logged."""

import jwt
import pytest

from voice.livekit.rig_adapter import DIALED, caller_token, token_counts


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
