"""Small pure helpers for each turn: when to wait longer, the stage timings sent with every turn
(docs/latency.md), the model requests behind an agent line (prompt cache numbers), and the
wait after the goodbye before the line drops."""

import asyncio
import re
from collections.abc import Awaitable, Callable

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


class LlmLog:
    """The model requests made since the last agent line, from LiveKit's per-request metrics.

    A turn can make several requests: a preemptive one that is thrown away, the one that counts,
    and one more after each tool result. The numbers sent with the agent line come from the first
    request that was not thrown away, because that one decides how long the caller waits."""

    def __init__(self):
        self._requests: list = []

    def record(self, metrics) -> None:
        if getattr(metrics, "type", "") == "llm_metrics":
            self._requests.append(metrics)

    def take(self) -> dict[str, float]:
        requests, self._requests = self._requests, []
        if not requests:
            return {}
        kept = [r for r in requests if not r.cancelled]
        first = (kept or requests)[0]
        return {
            "llm_requests": len(kept),
            "llm_cancelled": len(requests) - len(kept),
            "prompt_tokens": first.prompt_tokens,
            "cache_read_tokens": first.prompt_cached_tokens,
            "cache_write_tokens": first.cache_creation_tokens,
            "llm_output_tokens": first.completion_tokens,
        }


def agent_timings(
    metrics: dict,
    caller_stopped_at: float | None,
    tool_ms: float,
    bridge: bool,
    llm: dict[str, float] | None = None,
    clip_started_at: float | None = None,
) -> dict[str, float]:
    """Stage timings for an agent line. `reply_ms` runs from the end of the caller's speech to the
    agent's first audio, the number spec S-36 sets targets for. LiveKit's `stopped_speaking_at` is
    the end of speech itself, so the silence wait is inside it (its own `end_of_turn_delay` is
    timed from the same moment). `caller_stopped_at` is None on every line after the first of a
    turn, which gets no `reply_ms`. A bridge clip that started first is the first audio. `llm` is
    `LlmLog.take()`: the first model request's prompt cache numbers, so `make trace` shows whether
    the cache was hit."""
    timings = {
        "llm_first_token_ms": _ms(metrics.get("llm_node_ttft")),
        "tts_first_byte_ms": _ms(metrics.get("tts_node_ttfb")),
        "tool_ms": round(tool_ms, 1),
        "bridge": 1.0 if bridge else 0.0,
    }
    timings.update(llm or {})
    started = metrics.get("started_speaking_at")
    if clip_started_at and (not started or clip_started_at < started):
        started = clip_started_at
    if started and caller_stopped_at:
        timings["reply_ms"] = _ms(started - caller_stopped_at)
    return {k: v for k, v in timings.items() if v is not None}


async def caller_quiet_after_goodbye(
    grace_s: float,
    heard: Callable[[], bool],
    speaking: Callable[[], bool],
    extra_s: float = 1.5,
    poll_s: float = 0.05,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """Waits `grace_s` after the goodbye has played. False as soon as the caller says words (the
    call goes on); True when they stayed quiet. Sound with no words yet at the end of the wait gets
    up to `extra_s` more for its words, so a caller who starts talking late isn't cut off, while a
    TV in the room can't hold the line open."""
    waited = 0.0
    while waited < grace_s or (speaking() and waited < grace_s + extra_s):
        if heard():
            return False
        await sleep(poll_s)
        waited += poll_s
    return not heard()
