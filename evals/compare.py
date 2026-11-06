"""The run report: pass rate by flow and difficulty, reply delay and cost, compared with the previous
run of the same suite, stack and profile. A delay change over 100 ms or 10% is flagged (ASSUMED
threshold, evals/README.md), so a change that only affects speed is still caught."""

import math
from collections import defaultdict
from pathlib import Path

from evals.results import ResultsError, Run, load

FLAG_MS = 100
FLAG_SHARE = 0.10


def percentile(values: list[float], share: float) -> float:
    """Nearest-rank percentile; 0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def delays(run: Run) -> dict[str, tuple[float, float]]:
    """Median and slowest-10% reply delay per turn kind, over runs that produced a verdict."""
    by_kind: dict[str, list[float]] = defaultdict(list)
    for s in run.scenarios:
        if s.outcome != "error":
            for t in s.turn_timings:
                by_kind[t.kind].append(t.reply_delay_ms)
    return {k: (percentile(v, 0.5), percentile(v, 0.9)) for k, v in sorted(by_kind.items())}


def rates(run: Run, label) -> dict[str, str]:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for s in run.scenarios:
        if s.outcome == "error":
            continue
        for key in label(s):
            counts[key][0] += s.outcome == "pass"
            counts[key][1] += 1
    return {k: f"{p}/{n}" for k, (p, n) in sorted(counts.items())}


def flagged(old_ms: float, new_ms: float) -> bool:
    change = abs(new_ms - old_ms)
    return change > FLAG_MS or (old_ms > 0 and change / old_ms > FLAG_SHARE)


def previous(run: Run, directory: Path) -> Run | None:
    """The latest earlier run with the same suite, stack and profile."""
    best = None
    for path in directory.glob("*.json"):
        try:
            other = load(path)
        except ResultsError:
            continue
        same = (other.suite, other.stack, other.profile) == (run.suite, run.stack, run.profile)
        earlier = other.run_id != run.run_id and other.created < run.created
        if same and earlier and (best is None or other.created > best.created):
            best = other
    return best


def _pct(rate: float | None) -> str:
    return "n/a" if rate is None else f"{rate:.0%}"


def report(run: Run, prev: Run | None = None) -> list[str]:
    errors = sum(s.outcome == "error" for s in run.scenarios)
    cost = sum(s.cost_usd for s in run.scenarios)
    lines = [
        (
            f"run {run.run_id}: {run.stack} / {run.profile} / {run.suite}, "
            f"{len(run.scenarios)} scenarios, {errors} error(s), cost ${cost:.2f}"
        ),
        f"pass rate {_pct(run.pass_rate())}"
        + (f" (previous {_pct(prev.pass_rate())})" if prev else ""),
        *(
            ["audio rig: delay is end of caller speech to the agent's first sound"]
            if run.transport == "audio"
            else []
        ),
        "by flow:       " + "  ".join(f"{k} {v}" for k, v in rates(run, lambda s: s.flow).items()),
        "by difficulty: "
        + "  ".join(f"{k} {v}" for k, v in rates(run, lambda s: [s.difficulty]).items()),
    ]
    old = delays(prev) if prev else {}
    for kind, (p50, p90) in delays(run).items():
        line = f"reply delay {kind}: p50 {p50:.0f} ms, p90 {p90:.0f} ms"
        if kind in old:
            o50, o90 = old[kind]
            line += f" (previous p50 {o50:.0f}, p90 {o90:.0f})"
            if flagged(o50, p50) or flagged(o90, p90):
                line += "  FLAG: delay changed"
        lines.append(line)
    silent = sum(s.silent_turns for s in run.scenarios)
    if silent:
        lines.append(f"silent agent turns (no sound before the timeout): {silent}")
    if prev:
        old_cost = sum(s.cost_usd for s in prev.scenarios)
        lines.append(f"cost ${cost:.2f} (previous ${old_cost:.2f})")
    for s in run.scenarios:
        if s.outcome != "pass":
            why = s.error or "; ".join(s.check_failures) or s.grader_notes
            lines.append(f"  {s.outcome.upper():5} {s.key}: {why}")
    return lines
