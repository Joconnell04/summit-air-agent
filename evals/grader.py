"""The grader: a strong model with thinking on (profile offline.grader) scores how the call felt.

It runs after the code checks and can fail a call the code passed, never pass one the code failed.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from evals.models import Model, ModelError, Usage
from evals.results import Turn

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "evals" / "grader.md"
SCHEMA = {
    "type": "object",
    "properties": {
        # Structured outputs take no integer bounds; the range is checked after parsing.
        "score": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "notes": {"type": "string"},
    },
    "required": ["score", "verdict", "notes"],
    "additionalProperties": False,
}


@dataclass
class Grade:
    score: int
    failed: bool
    notes: str
    usage: Usage


# Tools whose sentence tells the caller what was set up: a visit, a change, a callback, a lead.
SETS_UP = (
    "book_appointment", "reschedule_appointment", "cancel_appointment", "flag_person",
    "record_lead", "mark_do_not_call",
)


def transcript_text(turns: list[Turn]) -> str:
    return "\n".join(f"{t.role.upper()}: {t.text}" for t in turns)


def set_up(trace: dict, safety: dict) -> list[str]:
    """What the server set up on the call, from its record. The grader only sees the transcript,
    so without this a callback time a tool returned reads as one the agent made up, and the
    signed-off safety sign-off reads as a promise nobody arranged."""
    found = [f"A person was flagged ({f['kind'].replace('_', ' ')})." for f in trace["flags"]]
    call = trace["call"]
    if call["safety_signal_at"] and call["safety_state"] != "cleared":
        kind = safety["kinds"][call["safety_kind"]]
        found.append(f"Safety lines (fixed wording): {' '.join(kind['lines'].split())}")
        found.append(f"Safety sign-off (fixed wording): {' '.join(safety['sign_off'].split())}")
    for e in trace["events"]:
        if e["type"] == "tool" and e["tool"] in SETS_UP and e["ok"] and e["say"]:
            found.append(f"{e['tool']} returned: {e['say']}")
    return found


def grade(model: Model, turns: list[Turn], should: list[str], done: list[str] | None = None
          ) -> Grade:
    system = PROMPT.read_text().format(
        should="\n".join(f"- {s}" for s in should) or "- Nothing beyond the house rules.",
        set_up="\n".join(f"- {d}" for d in done or []) or "- Nothing.",
    )
    messages = [{"role": "user", "content": transcript_text(turns)}]
    reply = model.complete(system, messages, max_tokens=8000, json_schema=SCHEMA)
    try:
        data = json.loads(reply.text)
        score = int(data["score"])
        if not 1 <= score <= 5 or data["verdict"] not in ("pass", "fail"):
            raise ValueError("score or verdict out of range")
    except (ValueError, KeyError, TypeError) as err:
        raise ModelError(f"grader reply was not valid JSON: {reply.text[:120]!r}") from err
    failed = data["verdict"] == "fail" or score == 1
    return Grade(score, failed, str(data["notes"]).strip(), reply.usage)
