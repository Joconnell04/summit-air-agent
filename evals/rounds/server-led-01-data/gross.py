"""The gross-error checks from docs/ai-process/briefs/server-led-flow.md, counted over the eval
results in this folder and the tool server's record of each call (traces/).

    uv run python evals/rounds/server-led-01-data/gross.py evals/rounds/server-led-01-data/2026*.json

A rough first pass: patterns, not a model's judgement. Each line it flags is printed, so a count
can be checked by eye.
"""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NARRATION = re.compile(
    r"\blet me\b|\bi'?ll (check|look|book|move|note|pass|set|get)\b|\bthe caller\b"
    r"|\bmoving on\b|\bnext,? i'?ll\b|\bbefore i (check|book)",
    re.IGNORECASE,
)
ECHO = re.compile(r"\bis that right\b|\bi have (the address|it) as\b|\bso that'?s\b", re.IGNORECASE)
MENTIONS = re.compile(
    r"\b(notes?|tool|system|looked? up|lookup|reference|on file|records?)\b", re.IGNORECASE
)
SAFETY_ADVICE = re.compile(
    r"\b(leave the (home|house|building|area)|go outside|get out|stay out|call 9-?1-?1"
    r"|call (the|your) gas)",
    re.IGNORECASE,
)


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", text.lower())).strip()


def check(scenario: dict) -> tuple[dict[str, list[str]], list[str]]:
    trace = json.loads((HERE / "traces" / f"{scenario['call_id']}.json").read_text())
    tools = [e for e in trace["events"] if not e["tool"].startswith("calls.")]
    says = [e["say"] for e in tools if e.get("say")]
    first = scenario["persona"].split(",")[0].split()[:2]
    names = {w.strip(",.") for w in first if len(w) > 2}
    # The greeting is the bridge's, not the model's.
    agent = [t["text"] or "" for t in scenario["transcript"] if t.get("role") == "agent"][1:]
    said = lambda s: any(norm(s) in norm(a) for a in agent)
    found = {
        "1 says the caller's name": [
            a for a in agent if any(re.search(rf"\b{re.escape(n)}\b", a) for n in names)
        ],
        "2 narrates": [a for a in agent if NARRATION.search(a)],
        "3 echoes outside the read-back": [
            a for a in agent if ECHO.search(a) and not any(norm(s)[:25] in norm(a) for s in says)
        ],
        # Jackson's residential question names its own examples and counts as one.
        "4 two questions in a turn": [
            a for a in agent
            if a.replace("right? Like", "right, like").count("?") > 1
            and not any(norm(s) in norm(a) for s in says if "?" in s)
        ],
        "7 mentions tools or notes": [a for a in agent if MENTIONS.search(a)],
        "S gives its own safety advice": [a for a in agent if SAFETY_ADVICE.search(a)],
    }
    return found, [s for s in says if not said(s)]


def main(paths: list[str]) -> None:
    calls, counts = 0, {}
    lines, reworded = 0, 0
    for path in paths:
        for scenario in json.loads(Path(path).read_text())["scenarios"]:
            calls += 1
            found, unsaid = check(scenario)
            trace = json.loads((HERE / "traces" / f"{scenario['call_id']}.json").read_text())
            says = [e["say"] for e in trace["events"] if e.get("say")]
            lines, reworded = lines + len(says), reworded + len(unsaid)
            print(f"== {scenario['scenario_id']} ({Path(path).name[:15]})")
            for key, hits in found.items():
                counts[key] = counts.get(key, 0) + bool(hits)
                for hit in hits[:2]:
                    print(f"   {key}: {hit[:150]!r}")
            for line in unsaid[:3]:
                print(f"   5 not said as written: {line[:150]!r}")
    print(f"\n{calls} calls; calls with each error:")
    for key, n in sorted(counts.items()):
        print(f"  {key}: {n}/{calls}")
    print(f"  5 server sentences not said as written: {reworded}/{lines}")


if __name__ == "__main__":
    main(sys.argv[1:])
