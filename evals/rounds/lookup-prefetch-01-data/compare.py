"""Compares two results files from the lookup-prefetch round (switch off, switch on), by turn.

    PYTHONPATH=. uv run python evals/rounds/lookup-prefetch-01-data/compare.py OFF.json ON.json

A lookup turn here is a caller line that gives a ZIP or a house number and street, read with the
prefetch's own rules (app/prefetch.py zip_in and street_in), so both arms count the same lines
whatever the model did with them. Every other turn with a timing is plain. Talked-over turns
(the agent made a sound before the caller finished) are counted, and left out of the medians.
"""

import json
import statistics
import sys
from pathlib import Path

from app import prefetch
from app.tenant import Tenants

ROOT = Path(__file__).resolve().parents[3]
TENANT = Tenants.load(ROOT / "tenants", "summit-air").default
AREA = TENANT.service_area
ZIP_BOROUGHS = (TENANT.address_lookup or {}).get("zip_boroughs") or {}


def is_lookup_line(text: str) -> bool:
    return bool(prefetch.zip_in(text, AREA, ZIP_BOROUGHS) or prefetch.street_in(text))


def turns(path: str) -> list[dict]:
    """One row per caller line that has a timing: its kind, delay, whether it was talked over."""
    run = json.loads(Path(path).read_text())
    rows = []
    for s in run["scenarios"]:
        callers = [t["text"] for t in s["transcript"] if t["role"] == "caller"]
        for text, timing in zip(callers, s["turn_timings"], strict=False):
            stages = timing.get("platform_stages") or {}
            rows.append({
                "scenario": s["scenario_id"],
                "kind": "lookup" if is_lookup_line(text) else "plain",
                "tool_kind": timing["kind"],
                "delay": timing["reply_delay_ms"],
                "over": timing.get("over_caller_ms") is not None,
                "requests": stages.get("llm_requests"),
                "cancelled": stages.get("llm_cancelled"),
                "text": text,
            })
    return rows


def p90(values: list[float]) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, round(0.9 * (len(values) - 1)))]


def summary(rows: list[dict], kind: str) -> dict:
    timed = [r["delay"] for r in rows if r["kind"] == kind and not r["over"] and r["delay"]]
    return {
        "n": len(timed),
        "p50": round(statistics.median(timed)) if timed else None,
        "p90": round(p90(timed)) if timed else None,
        "talked_over": sum(1 for r in rows if r["kind"] == kind and r["over"]),
        "requests": [r["requests"] for r in rows if r["kind"] == kind],
    }


def outcomes(path: str) -> dict:
    run = json.loads(Path(path).read_text())
    return {
        "calls": len(run["scenarios"]),
        "pass": sum(1 for s in run["scenarios"] if s["outcome"] == "pass"),
        "must_pass_failures": sum(len(s["must_pass_failures"]) for s in run["scenarios"]),
        "cost": round(sum(s["cost_usd"] for s in run["scenarios"]), 2),
    }


def main(off: str, on: str) -> None:
    for name, path in (("off", off), ("on", on)):
        rows = turns(path)
        print(name, Path(path).name, outcomes(path))
        for kind in ("lookup", "plain"):
            print(f"  {kind}", summary(rows, kind))


if __name__ == "__main__":
    main(*sys.argv[1:3])
