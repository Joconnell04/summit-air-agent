"""Small pure helpers for each turn: when to wait longer, the stage timings sent with every turn
(docs/latency.md), and the notes the server's turn result adds for the model."""

import re

# Right after the agent asks for these, the caller reads out digits with pauses between groups.
DIGITS = re.compile(
    r"\b(phone number|number to reach|best number|callback number|zip|zip code|last four|"
    r"digits|apartment number|unit number)\b",
    re.IGNORECASE,
)


def asked_for_digits(agent_text: str) -> bool:
    """Did the agent's line ask for a number? Only a question counts, and a line that already
    carries the digits is a read-back, answered with a yes or no."""
    if "?" not in agent_text or re.search(r"\d{3}", agent_text):
        return False
    return bool(DIGITS.search(agent_text))


def _ms(value: float | None) -> float | None:
    return None if value is None else round(value * 1000, 1)


def caller_timings(metrics: dict) -> dict[str, float]:
    """Stage timings for a caller line, from LiveKit's per-message metrics (seconds to ms)."""
    keys = {
        "transcript_ms": "transcription_delay",
        "end_of_turn_ms": "end_of_turn_delay",
    }
    timings = {name: _ms(metrics.get(key)) for name, key in keys.items()}
    return {k: v for k, v in timings.items() if v is not None}


def agent_timings(
    metrics: dict, caller_stopped_at: float | None, tool_ms: float, bridge: bool
) -> dict[str, float]:
    """Stage timings for an agent line. `reply_ms` runs from the caller going quiet to the agent's
    first audio, the number spec S-36 sets targets for."""
    timings = {
        "llm_first_token_ms": _ms(metrics.get("llm_node_ttft")),
        "tts_first_byte_ms": _ms(metrics.get("tts_node_ttfb")),
        "tool_ms": round(tool_ms, 1),
        "bridge": 1.0 if bridge else 0.0,
    }
    started = metrics.get("started_speaking_at")
    if started and caller_stopped_at:
        timings["reply_ms"] = _ms(started - caller_stopped_at)
    return {k: v for k, v in timings.items() if v is not None}
