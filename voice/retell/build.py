"""The Retell agent as code: the request bodies push.py sends. Pure functions of their inputs, so a
test can check them without Retell.

Everything that shapes the conversation comes from the same place as on the LiveKit stack: the
prompt and tool list from prompts/assemble.py, the model and voice from config/models.yaml. The
values chosen here are Retell's own dials; voice/retell/README.md says why each is set the way
it is.
"""

# Our model ids to Retell's names for the same models (create-retell-llm, model enum).
RETELL_MODELS = {
    "claude-haiku-5-5": "claude-5.5-haiku",
    "claude-haiku-4-5-20251001": "claude-4.5-haiku",
    "claude-sonnet-5-5": "claude-5.5-sonnet",
}
# The LiveKit worker's temperature for every provider (voice/livekit/worker.py).
TEMPERATURE = 0.3

AGENT_NAME = "Summit Air (Retell baseline)"
CHAT_AGENT_NAME = "Summit Air chat (Retell baseline, evals)"

# Turn-taking for phone audio and digit answers. Starting points from Retell's guidance,
# revisited after real calls (README, "Settings").
SPEECH = {
    "language": "en-US",
    "stt_mode": "fast",
    "denoising_mode": "noise-cancellation",
    "interruption_sensitivity": 0.8,
    "responsiveness": 0.9,
    "enable_dynamic_responsiveness": True,
    "enable_backchannel": True,
    "backchannel_frequency": 0.4,
    "backchannel_words": ["mm-hmm", "okay", "uh-huh"],
    "reminder_trigger_ms": 10000,
    "reminder_max_count": 2,
    "end_call_after_silence_ms": 45000,
    "max_call_duration_ms": 1200000,
    "voice_speed": 1.0,
    "boosted_keywords": [
        "Summit Air",
        "HVAC",
        "furnace",
        "boiler",
        "heat pump",
        "thermostat",
        "carbon monoxide",
        "condenser",
        "radiator",
    ],
}
CALL_EVENTS = ["call_started", "call_ended", "call_analyzed", "transcript_updated"]
CHAT_EVENTS = ["chat_started", "chat_ended", "transcript_updated"]


def retell_model(model_id: str) -> str:
    if model_id not in RETELL_MODELS:
        raise ValueError(f"Retell offers no match for {model_id}; add it to RETELL_MODELS")
    return RETELL_MODELS[model_id]


# Keywords Retell drops from a tool's parameters when it stores them (MEASURED on the first push:
# get-retell-llm returned the schemas without them). The model on Retell never sees them; the
# tool server still enforces every one.
DROPPED_BY_RETELL = ("default", "minimum", "maximum", "format")


def retell_schema(schema: dict) -> dict:
    """A parameter schema as Retell stores it, so a push compares like with like."""
    out = {}
    for key, value in schema.items():
        if key in DROPPED_BY_RETELL or (key == "required" and not value):
            continue
        if key == "properties":
            out[key] = {name: retell_schema(sub) for name, sub in value.items()}
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            out[key] = retell_schema(value)
        elif key in ("anyOf", "oneOf", "allOf"):
            out[key] = [retell_schema(sub) for sub in value]
        else:
            out[key] = value
    return out


def custom_tools(specs: list[dict], base_url: str) -> list[dict]:
    """Every model tool as a Retell custom function, posting to the bridge, which adds call_id
    and an Idempotency-Key and forwards it to the tool server."""
    return [
        {
            "type": "custom",
            "name": spec["name"],
            "description": spec["description"],
            "url": f"{base_url}/retell/tools/{spec['name']}",
            "method": "POST",
            "parameters": retell_schema(spec["parameters"]),
            "args_at_root": False,
            "speak_during_execution": False,
            "speak_after_execution": True,
            "timeout_ms": 10000,
            # Safe to retry: every write carries an Idempotency-Key from call, tool and arguments.
            "max_retry": 1,
        }
        for spec in specs
    ]


def llm_body(specs: list[dict], base_url: str, model_id: str) -> dict:
    """The prompt is one dynamic variable, filled per call by the bridge from
    prompts/assemble.py, so Retell reads exactly what the LiveKit worker reads."""
    return {
        "model_config": {
            "type": "cascading",
            "primary": {"model": retell_model(model_id), "tier": "standard"},
            "temperature": TEMPERATURE,
        },
        "general_prompt": "{{instructions}}",
        "general_tools": custom_tools(specs, base_url),
        "start_speaker": "agent",
        "begin_message": "{{greeting}}",
    }


def agent_body(llm_id: str, base_url: str, voice_id: str, voice_model: str) -> dict:
    return {
        "agent_name": AGENT_NAME,
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "voice_id": voice_id,
        "voice_model": voice_model,
        "webhook_url": f"{base_url}/retell/webhook",
        "webhook_events": CALL_EVENTS,
        **SPEECH,
    }


def chat_agent_body(llm_id: str, base_url: str) -> dict:
    return {
        "agent_name": CHAT_AGENT_NAME,
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "webhook_url": f"{base_url}/retell/webhook",
        "webhook_events": CHAT_EVENTS,
    }


def phone_body(agent_id: str, base_url: str) -> dict:
    """The number rings the latest version of the agent, so a push is live on the next call, and
    Retell asks the bridge before every call (the lookup before hello)."""
    return {
        "inbound_agents": [{"agent_id": agent_id, "agent_version": "latest", "weight": 1}],
        "inbound_webhook_url": f"{base_url}/retell/inbound",
    }


def contains(current, desired) -> bool:
    """Does what Retell holds already include everything we want? Retell fills in defaults we
    didn't send, so only the keys we send are compared."""
    if isinstance(desired, dict):
        return isinstance(current, dict) and all(
            contains(current.get(k), v) for k, v in desired.items()
        )
    if isinstance(desired, list):
        return (
            isinstance(current, list)
            and len(current) == len(desired)
            and all(contains(c, d) for c, d in zip(current, desired, strict=True))
        )
    return current == desired


def changes(current: dict, desired: dict) -> dict:
    """The top-level fields to send so Retell matches desired; empty when nothing differs."""
    return {k: v for k, v in desired.items() if not contains(current.get(k), v)}
