"""Retell on the audio rig: a temporary voice agent on the eval LLM and bridge, reached through
web calls, using only endpoints the project allows."""

from voice.retell.api import allowed
from voice.retell.rig_adapter import TEST_AGENT_NAME, rig_agent_body


def test_the_test_agent_uses_the_eval_bridge_and_the_production_voice():
    voice = {"voice_id": "custom_voice_1", "voice_model": None}
    body = rig_agent_body("llm_eval", "https://host/eval", voice)
    assert body["agent_name"] == TEST_AGENT_NAME
    assert body["response_engine"] == {"type": "retell-llm", "llm_id": "llm_eval"}
    assert body["webhook_url"] == "https://host/eval/retell/webhook"
    assert body["voice_id"] == "custom_voice_1" and "voice_model" not in body


def test_web_calls_are_allowed_and_nothing_is_deleted():
    assert allowed("POST", "/v3/create-web-call")
    assert not allowed("POST", "/v2/create-web-call")  # retired by Retell
    assert not allowed("DELETE", "/delete-agent/agent_123")
