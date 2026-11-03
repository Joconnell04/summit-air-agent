"""Retell's timing data mapped into our trace format (docs/latency.md), so `make trace` and the
bake-off report read both stacks the same way.

Per turn, Retell gives word timestamps from its own recording of the call; the reply delay a caller
hears is the gap between the caller's last word and the agent's first. Per call, it gives latency
percentiles by stage (e2e, asr, llm, tts) and the cost, only after the call ends.
"""

from .transcript import Line

# The per-turn key both stacks write; the bake-off reads it.
REPLY_DELAY = "reply_ms"


def turn_timings(previous: list[Line], words: list[dict]) -> dict[str, float]:
    """Timings for an agent turn: words are the ones this turn added; previous is what came
    before it in the call. Only an agent line that answers a caller line has a reply delay; the
    rest of a reply Retell split into more lines (around a tool call or an interruption) has none.
    Empty too when the words haven't been spoken yet."""
    caller = previous[-1] if previous else None
    if not words or caller is None or caller.speaker != "caller" or not caller.words:
        return {}
    first = words[0].get("start")
    caller_end = caller.words[-1].get("end")
    if first is None or caller_end is None or first < caller_end:
        return {}
    return {REPLY_DELAY: round((first - caller_end) * 1000, 1)}


def new_words(line: Line, posted_chars: int) -> list[dict]:
    """The words of a line after its first posted_chars characters, for a line that grew."""
    seen, out = 0, []
    for word in line.words:
        if seen >= posted_chars:
            out.append(word)
        seen += len(word.get("word", "")) + 1
    return out


def call_summary(call: dict) -> dict:
    """Stage latencies (median and slowest 10%, ms) and cost for one call, from Retell's call
    object after it ends."""
    out: dict = {}
    for stage, data in (call.get("latency") or {}).items():
        if isinstance(data, dict) and data.get("p50") is not None:
            out[f"{stage}_p50_ms"] = data["p50"]
            out[f"{stage}_p90_ms"] = data.get("p90")
    cost = call.get("call_cost") or {}
    if cost.get("combined_cost") is not None:
        out["cost_usd"] = round(cost["combined_cost"] / 100, 4)
        out["duration_s"] = cost.get("total_duration_seconds")
    return out
