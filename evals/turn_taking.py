"""Speech-to-text and turn-taking numbers for an A/B of Retell agent settings (issue #175).

    uv run python -m evals.turn_taking <label>=<run_dir> [<label>=<run_dir> ...]

Reads each run's calls/<tag>.retell.json and .rows.json (voice/retell/side_by_side/collect.py)
and prints, per label: end-to-end and speech-to-text latency (the medians over calls of each
call's `latency.e2e` and `latency.asr` p50 and p90), cut-ins, and what was heard. A cut-in is a
caller line Retell split around an agent reply: the caller's text before the reply doesn't end a
sentence (no closing punctuation, or it ends on "um", "uh", "and" or a comma) and the caller speaks
again right after. "Heard" lists the name stored against the one intended, and the street and
unit the read-back used, so number and unit errors can be checked by eye against the persona.
"""

import json
import re
import statistics
import sys
from pathlib import Path

from evals.side_by_side import load_json, manifest

OPEN_END = re.compile(r"(\b(um|uh|and|so|like)\W*$|,\s*$|[^.?!]\s*$)", re.IGNORECASE)


def cut_ins(transcript: list[dict]) -> list[str]:
    lines = [
        x for x in transcript if x.get("role") in ("user", "agent") and x.get("content", "").strip()
    ]
    found = []
    for a, b, c in zip(lines, lines[1:], lines[2:], strict=False):
        roles = (a["role"], b["role"], c["role"])
        if roles == ("user", "agent", "user") and OPEN_END.search(a["content"].strip()):
            found.append(f"{a['content'].strip()[-40:]!r} / {b['content'].strip()[:40]!r}")
    return found


def heard(call: dict, rows: dict) -> dict:
    out = {}
    for c in rows.get("calls", []):
        names = [r["name"] for r in c.get("booking_names", []) + c.get("call_names", [])]
        if names:
            out["name"] = names[0]
    for x in call.get("transcript_with_tool_calls") or []:
        if x.get("role") == "tool_call_invocation" and x.get("name") == "read_back":
            args = json.loads(x.get("arguments") or "{}").get("address") or {}
            out["street"], out["unit"] = args.get("street"), args.get("unit")
    return out


def summary(run: Path) -> dict:
    calls = []
    for entry in manifest(run):
        call = load_json(run / entry["log"].replace(".log", ".retell.json"))
        if not call:
            continue
        lat = call.get("latency") or {}
        calls.append(
            {
                "persona": entry["persona"],
                "e2e": ((lat.get("e2e") or {}).get("p50"), (lat.get("e2e") or {}).get("p90")),
                "asr": ((lat.get("asr") or {}).get("p50"), (lat.get("asr") or {}).get("p90")),
                "cut_ins": cut_ins(call.get("transcript_with_tool_calls") or []),
                "heard": heard(call, load_json(run / entry["rows"])),
            }
        )

    def med(key: str, i: int):
        vals = [c[key][i] for c in calls if c[key][i] is not None]
        return round(statistics.median(vals)) if vals else None

    return {
        "calls": len(calls),
        "e2e_p50_ms": med("e2e", 0),
        "e2e_p90_ms": med("e2e", 1),
        "asr_p50_ms": med("asr", 0),
        "asr_p90_ms": med("asr", 1),
        "cut_ins": sum(len(c["cut_ins"]) for c in calls),
        "per_call": calls,
    }


def main() -> None:
    for arg in sys.argv[1:]:
        label, path = arg.split("=", 1)
        print(label, json.dumps(summary(Path(path)), indent=1))


if __name__ == "__main__":
    main()
