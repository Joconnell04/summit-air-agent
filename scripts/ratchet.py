"""Fail if the test count or the committed eval run falls below the baseline. Offline: it never calls
a model.

The eval floor reads the results file named by `eval_results` in evals/baseline.json and fails on a
stale hash (prompts, those scenarios or the model profiles changed since the run), fewer scenarios
than `min_eval_scenarios`, any failed must-pass case, or a pass rate under `min_eval_pass_rate`.

Run with --update to raise the test baseline after adding tests, and with
--adopt evals/results/<run>.json to make a paid run the eval reference.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "evals" / "baseline.json"
sys.path.insert(0, str(ROOT))


def count_tests() -> int:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return sum(1 for line in out.splitlines() if "::" in line)


def check(count: int, baseline: dict) -> tuple[bool, str]:
    floor = baseline["min_tests"]
    if count < floor:
        return False, f"test count {count} is below the baseline of {floor}"
    return True, f"test count {count} (baseline {floor})"


def check_eval(baseline: dict, root: Path = ROOT, current_hash=None) -> tuple[bool, str]:
    from evals.results import ResultsError, load

    reference = baseline.get("eval_results")
    if not reference:
        return True, "eval floor: no reference run adopted yet"
    try:
        run = load(root / reference)
    except ResultsError as err:
        return False, f"eval floor: {err}"
    if current_hash is None:
        from evals.fingerprint import run_hash

        def current_hash(r):
            return run_hash([s.scenario_id for s in r.scenarios], r.profile)

    problems = []
    try:
        fresh = current_hash(run)
    except (OSError, KeyError) as err:
        fresh = f"unreadable ({err})"
    if fresh != run.hash:
        problems.append(
            f"{reference} is stale: its prompts, scenarios or profile changed; rerun the suite"
        )
    floor = baseline.get("min_eval_scenarios") or 0
    if len(run.scenarios) < floor:
        problems.append(f"{len(run.scenarios)} scenarios, fewer than the floor of {floor}")
    failed = [s.key for s in run.scenarios if s.must_pass_failures]
    if failed:
        problems.append(f"must-pass case(s) failed: {', '.join(failed)}")
    rate, min_rate = run.pass_rate(), baseline.get("min_eval_pass_rate")
    if min_rate is not None and (rate is None or rate < min_rate):
        shown = "none" if rate is None else f"{rate:.0%}"
        problems.append(f"pass rate {shown} is under the floor of {min_rate:.0%}")
    if problems:
        return False, "eval floor: " + "; ".join(problems)
    return True, f"eval floor: {reference} ({len(run.scenarios)} scenarios, hash current)"


def adopt(path: str, baseline: dict, root: Path = ROOT) -> tuple[bool, str]:
    """Make a results file the eval reference. Floors only go up."""
    from evals.results import ResultsError, load

    try:
        run = load(root / path)
    except ResultsError as err:
        return False, str(err)
    if any(s.must_pass_failures for s in run.scenarios):
        return False, "refusing to adopt a run with a failed must-pass case"
    if len(run.scenarios) < (baseline.get("min_eval_scenarios") or 0):
        return False, "refusing to adopt a run with fewer scenarios than the floor"
    baseline["eval_results"] = path
    baseline["min_eval_scenarios"] = len(run.scenarios)
    return True, f"eval reference is now {path} ({len(run.scenarios)} scenarios)"


def main() -> int:
    baseline = json.loads(BASELINE.read_text())
    if "--adopt" in sys.argv:
        rest = sys.argv[sys.argv.index("--adopt") + 1 :]
        if not rest:
            print("usage: python scripts/ratchet.py --adopt evals/results/<run>.json")
            return 2
        ok, msg = adopt(rest[0], baseline)
        if ok:
            BASELINE.write_text(json.dumps(baseline, indent=2) + "\n")
        print(msg)
        return 0 if ok else 1
    count = count_tests()
    if "--update" in sys.argv:
        if count < baseline["min_tests"]:
            print(f"refusing to lower the baseline from {baseline['min_tests']} to {count}")
            return 1
        baseline["min_tests"] = count
        BASELINE.write_text(json.dumps(baseline, indent=2) + "\n")
        print(f"baseline raised to {count}")
        return 0
    ok, msg = check(count, baseline)
    print(("ok: " if ok else "ratchet: ") + msg)
    eval_ok, eval_msg = check_eval(baseline)
    print(("ok: " if eval_ok else "ratchet: ") + eval_msg)
    return 0 if ok and eval_ok else 1


if __name__ == "__main__":
    sys.exit(main())
