"""Re-run a recorded call's caller turns against the current prompts and tools, and diff the
tool-call sequence. Exits 1 when it differs, so a prompt change that changes behavior shows up.

Usage: python -m evals.replay <call id> [--results-dir evals/results] [--stack livekit]
"""
import argparse
import sys
import tempfile
from pathlib import Path

from evals import results
from evals.results import RESULTS_DIR, ResultsError, ScenarioResult
from evals.run import Harness, default_models
from evals.scenario import SCENARIOS_DIR, ScenarioError, load, scenario_path


def find_call(call_id: str, directory: Path) -> tuple[ScenarioResult, str] | None:
    """The recorded scenario result for this call id, and the profile it ran on."""
    for path in sorted(directory.glob("*.json")):
        try:
            run = results.load(path)
        except ResultsError:
            continue
        for result in run.scenarios:
            if result.call_id == call_id:
                return result, run.profile
    return None


def tool_sequence(result: ScenarioResult) -> list[str]:
    return [c.name for c in result.tool_calls]


def diff(before: list[str], after: list[str]) -> list[str]:
    """Line-by-line differences between two tool-call sequences, or nothing when they match."""
    lines = []
    for i in range(max(len(before), len(after))):
        old = before[i] if i < len(before) else "-"
        new = after[i] if i < len(after) else "-"
        if old != new:
            lines.append(f"  step {i + 1}: was {old}, now {new}")
    return lines


def replay(result: ScenarioResult, harness: Harness, scenarios_dir: Path = SCENARIOS_DIR
           ) -> ScenarioResult:
    scenario = load(scenario_path(result.scenario_id, scenarios_dir))
    lines = [t.text for t in result.transcript if t.role == "caller"]
    return harness.run(scenario, result.sample, caller_turns=lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a recorded call.")
    parser.add_argument("call_id")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    parser.add_argument("--stack", default="livekit")
    args = parser.parse_args(argv)
    found = find_call(args.call_id, Path(args.results_dir))
    if found is None:
        print(f"no call {args.call_id} in {args.results_dir}", file=sys.stderr)
        return 2
    recorded, profile = found
    with tempfile.TemporaryDirectory(prefix="summit-replay-") as tmp:
        try:
            fresh = replay(recorded, Harness(default_models(profile), args.stack, Path(tmp)))
        except ScenarioError as err:
            print(err, file=sys.stderr)
            return 2
    changes = diff(tool_sequence(recorded), tool_sequence(fresh))
    print(f"replayed {recorded.key} as {fresh.call_id}: outcome {recorded.outcome} -> "
          f"{fresh.outcome}")
    if changes:
        print("tool calls differ:\n" + "\n".join(changes))
        return 1
    print(f"tool calls match ({len(recorded.tool_calls)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
