"""The eval gate for owner edits (app/rules.py, exploration 009): every must-pass scenario, once,
on our stack, with the owner's draft live in each scratch world. The result goes back on the
version it checked; only a pass makes it live.

A draft is blocked by any failed code check or any run with no verdict on those scenarios. The
grader's view of how a call felt is reported but does not block: the must-pass lines are code
checks (docs/spec.md), and the grader can fail a call on manner an owner edit is allowed to change.

    uv run python -m evals.gate --db data/tool_server.db        # every draft waiting for a check
    make rules-gate

Paid: about $0.02 a scenario on the fast profile (MEASURED, evals/README.md smoke runs), so about
$0.35 for the 18 must-pass scenarios (ASSUMED from that rate).
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


def scenarios():
    chosen, _samples = suites.select(load_all(), None, ["must-pass"])
    return chosen


def summary(run: Run, content: dict) -> dict:
    """What the version keeps from a gate run. record_gate reads failed and errors to decide."""
    def code_failures(s):
        return [f for f in s.check_failures if not f.startswith("grader:")]

    return {
        "content_hash": rules.content_hash(content),
        "run_id": run.run_id,
        "hash": run.hash,
        "stack": run.stack,
        "profile": run.profile,
        "scenarios": len(run.scenarios),
        "passed_count": sum(1 for s in run.scenarios if s.outcome != "error" and not code_failures(s)),
        "failed": sorted(s.scenario_id for s in run.scenarios if code_failures(s)),
        "errors": sorted(s.scenario_id for s in run.scenarios if s.outcome == "error"),
        "grader_flags": sorted(
            s.scenario_id for s in run.scenarios
            if s.outcome == "fail" and not code_failures(s)
        ),
        "cost_usd": round(sum(s.cost_usd for s in run.scenarios), 4),
        "finished_at": run.created.isoformat(),
    }


def run_gate(content: dict, profile: str, results_dir: Path = RESULTS_DIR, workers: int = 4,
             harness=None) -> tuple[Run, dict]:
    chosen = scenarios()
    with tempfile.TemporaryDirectory(prefix="summit-gate-") as tmp:
        harness = harness or Harness(default_models(profile), STACK, Path(tmp), overlay=content)
        scenario_results = run_suite(harness, chosen, 1, workers)
    run = build_run(SUITE, ["must-pass"], STACK, profile, scenario_results, overlay=content)
    results.save(run, results_dir)
    return run, summary(run, content)


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
            f"{gate['scenarios']} passed, ${gate['cost_usd']:.2f}, run {gate['run_id']}"
        )
    if not done:
        print("no draft is waiting for a check")
    return 0 if all(v["status"] == "published" for v in done) else 1


if __name__ == "__main__":
    sys.exit(main())
