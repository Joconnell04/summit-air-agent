"""The eval gate for owner edits (app/rules.py, exploration 009): every must-pass scenario, once,
on our stack, with the owner's draft live in each scratch world. The result goes back on the
version it checked; only a pass makes it live.

A scenario that fails a code check or ends with no verdict is run once more on the same draft, and
the draft is blocked if it fails again. Every scenario must pass, so a failure that repeats always
blocks; the rerun only keeps a one-off miss from blocking an edit that didn't cause it (three runs
before the rerun existed failed five different must-pass scenarios, none twice). Both runs are
saved and kept on the version. The grader's view of how a call felt is reported but does not
block: the must-pass lines are code checks (docs/spec.md), and the grader can fail a call on
manner an owner edit is allowed to change.

    uv run python -m evals.gate --db data/tool_server.db        # every draft waiting for a check
    make rules-gate

Paid: about $0.02 a scenario on the fast profile (MEASURED, evals/README.md smoke runs), so about
$0.39 for the 18 must-pass scenarios (MEASURED, evals/README.md), plus about $0.02 a rerun.
"""
import argparse
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app import db, rules
from evals import results, suites
from evals.models import load_config
from evals.results import RESULTS_DIR, Run
from evals.run import Harness, build_run, default_models, run_suite
from evals.scenario import load_all

STACK = "livekit"
SUITE = "gate"
RERUN_SUITE = "gate-rerun"


def scenarios():
    chosen, _samples = suites.select(load_all(), None, ["must-pass"])
    return chosen


def _code_failures(s) -> list[str]:
    return [f for f in s.check_failures if not f.startswith("grader:")]


def _missed(run: Run | None) -> set[str]:
    """Scenarios with a failed code check or no verdict."""
    if run is None:
        return set()
    return {s.scenario_id for s in run.scenarios if s.outcome == "error" or _code_failures(s)}


def summary(run: Run, content: dict, rerun: Run | None = None) -> dict:
    """What the version keeps from a gate run and its rerun. record_gate reads failed and errors to
    decide: a scenario counts against the draft only if it missed on both runs."""
    final = {s.scenario_id: s for s in run.scenarios}
    if rerun is not None:
        final |= {s.scenario_id: s for s in rerun.scenarios}
    missed_first = _missed(run)
    failed = sorted(i for i, s in final.items() if _code_failures(s))
    errors = sorted(i for i, s in final.items() if s.outcome == "error" and not _code_failures(s))
    runs = [run] + ([rerun] if rerun else [])
    return {
        "content_hash": rules.content_hash(content),
        "run_id": run.run_id,
        "rerun_id": rerun.run_id if rerun else None,
        "hash": run.hash,
        "stack": run.stack,
        "profile": run.profile,
        "scenarios": len(final),
        "passed_count": len(final) - len(failed) - len(errors),
        "failed": failed,
        "errors": errors,
        "first_missed": sorted(missed_first),
        "passed_on_rerun": sorted(missed_first - set(failed) - set(errors)),
        "reruns": len(rerun.scenarios) if rerun else 0,
        "rerun_cost_usd": round(sum(s.cost_usd for s in rerun.scenarios), 4) if rerun else 0.0,
        "grader_flags": sorted(
            s.scenario_id for s in final.values() if s.outcome == "fail" and not _code_failures(s)
        ),
        "cost_usd": round(sum(s.cost_usd for r in runs for s in r.scenarios), 4),
        "finished_at": (rerun or run).created.isoformat(),
    }


def run_gate(content: dict, profile: str, results_dir: Path = RESULTS_DIR, workers: int = 4,
             harness=None) -> tuple[Run, dict]:
    """Every must-pass scenario once, then the ones that missed once more."""
    chosen = scenarios()
    with tempfile.TemporaryDirectory(prefix="summit-gate-") as tmp:
        harness = harness or Harness(default_models(profile), STACK, Path(tmp), overlay=content)
        first = build_run(SUITE, ["must-pass"], STACK, profile, run_suite(harness, chosen, 1, workers),
                          overlay=content)
        results.save(first, results_dir)
        again = [s for s in chosen if s.id in _missed(first)]
        rerun = None
        if again:
            rerun = build_run(RERUN_SUITE, ["must-pass"], STACK, profile,
                              run_suite(harness, again, 1, workers), overlay=content)
            results.save(rerun, results_dir)
    return first, summary(first, content, rerun)


def check_waiting(db_path: Path, profile: str, results_dir: Path = RESULTS_DIR, run=run_gate
                  ) -> list[dict]:
    """Run the gate on every version waiting for one and record each result. The draft is tested
    exactly as saved; the server applies it the same way on a real call."""
    conn = db.connect(db_path)
    done = []
    try:
        waiting = conn.execute(
            "SELECT id, tenant FROM rule_versions WHERE status = 'checking' ORDER BY id"
        ).fetchall()
        for row in waiting:
            version = rules.get(conn, row["id"])
            _run, found = run(version["content"], profile, results_dir)
            done.append(rules.record_gate(conn, row["id"], found, datetime.now(UTC)))
    finally:
        conn.close()
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the eval gate on owner drafts.")
    parser.add_argument("--db", default="data/tool_server.db")
    parser.add_argument("--profile", default="")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    args = parser.parse_args(argv)
    profile = args.profile or load_config()["default"]
    done = check_waiting(Path(args.db), profile, Path(args.results_dir))
    for version in done:
        gate = version["gate"]
        print(
            f"version {version['number']}: {version['status']}, {gate['passed_count']} of "
            f"{gate['scenarios']} passed ({len(gate['passed_on_rerun'])} on a rerun of "
            f"{gate['reruns']}), ${gate['cost_usd']:.2f}, run {gate['run_id']}"
        )
    if not done:
        print("no draft is waiting for a check")
    return 0 if all(v["status"] == "published" for v in done) else 1


if __name__ == "__main__":
    sys.exit(main())
