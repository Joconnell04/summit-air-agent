"""One calibration round for the failure grader (issue #178): grade every call in labels.yaml
RUNS times with the real grader and score it.

Each call goes through app.failure_grader.grade_call unchanged, with the Retell call from
GET /v2/get-call/<id> and the tool server's rows from the side-by-side dump, loaded into a scratch
database (one per run, so runs don't share a cache). A call whose rows weren't kept is graded from
its transcript, and the model is told the rows weren't kept.

    uv run --env-file .env python -m evals.failure_grader.calibrate --round 1

Writes round-<n>.json beside this file. Spend is counted from the model's usage, and no new call
starts once MAX_USD is reached. Needs ANTHROPIC_API_KEY and RETELL_API_KEY. The side-by-side dumps
are read from the working tree when present, or from SIDE_BY_SIDE_REF with git show.
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import yaml

from app import db
from app import failure_grader as grader
from app.settings import Settings
from app.tenant import Tenants

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
DATA = "evals/rounds/side-by-side-01-data"
REF = os.environ.get("SIDE_BY_SIDE_REF", "origin/joco/side-by-side-014a4c")
LIVE_ROWS = os.environ.get("LIVE_ROWS_DIR", "")
RUNS = 3
WORKERS = 6
MAX_USD = 6.00
TABLES = ("bookings", "flags", "callbacks", "leads", "notes")
NO_ROWS = {
    "not_available": "The company's records for this call were not kept for this review; judge "
    "from the transcript and the tool results in it."
}


def read_rows(rel: str) -> dict:
    path = ROOT / DATA / rel
    if path.exists():
        return json.loads(path.read_text())
    return json.loads(subprocess.check_output(["git", "show", f"{REF}:{DATA}/{rel}"], cwd=ROOT))


def insert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    names = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    keep = {k: v for k, v in row.items() if k in names}
    marks = ",".join("?" * len(keep))
    conn.execute(
        f"INSERT OR IGNORE INTO {table} ({','.join(keep)}) VALUES ({marks})", list(keep.values())
    )


def load_call(conn: sqlite3.Connection, case: dict, retell: dict) -> str:
    """Put the call and its rows in the scratch database; returns our call id."""
    call_id = grader.RETELL_PREFIX + case["id"]
    live = Path(LIVE_ROWS) / f"{case['id']}.json" if LIVE_ROWS else None
    if case["rows"] == "live" and live and live.exists():
        dump = json.loads(live.read_text())
        for row in dump["calls"]:
            insert(conn, "calls", row)
        for table in (*TABLES, "do_not_call"):
            for row in dump.get(table, []):
                insert(conn, table, row)
        return call_id
    if case["rows"] == "live":
        number = retell.get("from_number") or ""
        insert(conn, "calls", {
            "id": call_id, "tenant": "summit-air", "caller_id": number,
            "caller_key": "".join(ch for ch in number if ch.isdigit())[-10:],
            "dialed_number": retell.get("to_number") or "", "direction": "inbound",
            "started_at": db.iso(datetime.fromtimestamp(retell["start_timestamp"] / 1000, UTC)),
            "ended_at": db.iso(datetime.fromtimestamp(retell["end_timestamp"] / 1000, UTC)),
            "end_reason": retell.get("disconnection_reason"),
        })
        return call_id
    dump = read_rows(case["rows"])
    for entry in dump["calls"]:
        insert(conn, "calls", entry["call"])
        for table in TABLES:
            for row in entry.get(table, []):
                insert(conn, table, row)
    for row in dump.get("do_not_call", []):
        insert(conn, "do_not_call", row)
    return call_id


class CachedTranscripts:
    """Retell is read once per call for the whole round."""

    def __init__(self, calls: dict[str, dict]):
        self.calls = calls

    def get(self, retell_id: str) -> dict:
        return self.calls[retell_id]


def score(results: list[dict], cases: list[dict], run: int | None = None) -> dict:
    """Precision and recall for one run, or for the majority of runs when run is None."""
    out = {}
    for name, keep in (("all", lambda c: True), ("clear", lambda c: not c.get("borderline"))):
        tp = fp = fn = 0
        for case in filter(keep, cases):
            flags = [r["flagged"] for r in results if r["id"] == case["id"]]
            if run is not None:
                flags = [r["flagged"] for r in results if r["id"] == case["id"] and r["run"] == run]
            flagged = sum(flags) * 2 > len(flags)
            if case["label"] == "fail":
                tp += flagged
                fn += not flagged
            else:
                fp += flagged
        out[name] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        }
    return out


def summarize(args, cases: list[dict], results: list[dict], spent: float, graded_ids) -> dict:
    """The round's scores: per run, by majority of runs, and which calls were flagged wrongly,
    missed, or flagged differently across runs."""
    ok_ids = {c["id"] for c in cases if c["label"] == "ok"}
    labels = {c["id"]: c["label"] for c in cases}
    return {
        "round": args.round,
        "prompt": "prompts/failure-grader.md",
        "profile": grader.PROFILE,
        "calls": len(cases),
        "fails_labeled": sum(c["label"] == "fail" for c in cases),
        "runs": args.runs,
        "graded": len(results),
        "errors": sum(bool(r["error"]) for r in results),
        "live_rows": bool(LIVE_ROWS),
        "per_run": {run: score(results, cases, run) for run in range(args.runs)},
        "majority": score(results, cases),
        "false_flags_any_run": sorted({r["id"] for r in results if r["id"] in ok_ids and r["flagged"]}),
        "missed_any_run": sorted({
            r["id"] for r in results if labels.get(r["id"]) == "fail" and not r["flagged"]
        }),
        "unstable": sorted({
            c["id"] for c in cases
            if len({r["flagged"] for r in results if r["id"] == c["id"]}) > 1
        }),
        "spent_usd": round(spent, 4),
        "carried_from": args.carry,
        "graded_this_round": graded_ids if args.carry else "all",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--only", action="append", help="grade only these call ids")
    parser.add_argument("--carry", type=int, help="take the other calls' results from this round")
    parser.add_argument("--max-usd", type=float, default=MAX_USD)
    parser.add_argument("--rescore", action="store_true", help="score a finished round again")
    args = parser.parse_args()
    if args.rescore:
        # Score a finished round again against the labels as they are now; no model calls.
        path = HERE / f"round-{args.round}.json"
        done = json.loads(path.read_text())
        cases = yaml.safe_load((HERE / "labels.yaml").read_text())["calls"]
        labels = {c["id"]: c["label"] for c in cases}
        results = [{**r, "label": labels[r["id"]]} for r in done["results"]]
        old = done["summary"]
        args.runs, args.carry = old["runs"], old.get("carried_from")
        summary = summarize(args, cases, results, old["spent_usd"], old.get("graded_this_round"))
        summary["rescored"] = "against labels.yaml as relabeled after this round"
        path.write_text(json.dumps({"summary": summary, "results": results}, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        return

    cases = yaml.safe_load((HERE / "labels.yaml").read_text())["calls"]
    if args.only:
        by_id = {c["id"]: c for c in cases}
        cases = [by_id[i] for i in dict.fromkeys(args.only) if i in by_id]
    settings = Settings()
    tenant = Tenants.load(settings.tenants_dir, settings.default_tenant, settings.trades_dir).default
    model = grader.model_from_config()
    if model is None:
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    retell = grader.RetellTranscripts(os.environ["RETELL_API_KEY"])
    transcripts = CachedTranscripts({c["id"]: retell.get(c["id"]) for c in cases})
    no_rows = {
        grader.RETELL_PREFIX + c["id"]
        for c in cases
        if c["rows"] == "live" and not (LIVE_ROWS and (Path(LIVE_ROWS) / f"{c['id']}.json").exists())
    }
    real_rows = grader.call_rows
    # Only for a call whose rows weren't kept: tell the model so, instead of empty records.
    grader.call_rows = lambda conn, t, call: (
        NO_ROWS if call["id"] in no_rows else real_rows(conn, t, call)
    )

    spent, lock, results = [0.0], threading.Lock(), []
    carried = []
    if args.carry:
        before = json.loads((HERE / f"round-{args.carry}.json").read_text())["results"]
        graded_now = {c["id"] for c in cases}
        all_cases = yaml.safe_load((HERE / "labels.yaml").read_text())["calls"]
        labels = {c["id"]: c["label"] for c in all_cases}
        carried = [{**r, "label": labels[r["id"]], "carried": True}
                   for r in before if r["id"] not in graded_now and r["id"] in labels]
    with tempfile.TemporaryDirectory() as tmp:
        # One database per call and run: each side-by-side call ran in its own reset world, and
        # their dumps reuse row ids and the same caller number, so one shared database would mix
        # one world's do-not-call list into another's and drop rows whose ids repeat.
        paths = {}
        for case in cases:
            base = Path(tmp) / f"{case['id']}.db"
            db.init(base)
            conn = db.connect(base)
            load_call(conn, case, transcripts.get(case["id"]))
            conn.close()
            for run in range(args.runs):
                paths[run, case["id"]] = Path(tmp) / f"{case['id']}-{run}.db"
                shutil.copy(base, paths[run, case["id"]])

        def grade(job):
            run, case = job
            with lock:
                if spent[0] >= args.max_usd:
                    return None
            conn = db.connect(paths[run, case["id"]])
            try:
                got = grader.grade_call(conn, tenant, model, transcripts,
                                        grader.RETELL_PREFIX + case["id"], datetime.now(UTC),
                                        force=True)
            finally:
                conn.close()
            with lock:
                spent[0] += got["cost_now"]
            mark = "FAIL" if case["label"] == "fail" else "ok  "
            print(f"r{run} {mark} {case['id']} flagged={got['failure']} {got['kind'] or ''} "
                  f"{(got['dropped'] or '')[:40]} {got['reason'][:80]}", flush=True)
            return {
                "run": run, "id": case["id"], "label": case["label"],
                "flagged": bool(got["failure"]), "kind": got["kind"], "severity": got["severity"],
                "reason": got["reason"], "evidence": json.loads(got["evidence_json"]),
                "dropped": got["dropped"], "error": got["error"],
                "raw": json.loads(got["raw_json"]) if got["raw_json"] else None,
                "cost_usd": round(got["cost_now"], 4),
            }

        # Call by call, so a spend cap leaves whole calls ungraded rather than one run short.
        jobs = [(run, case) for case in cases for run in range(args.runs)]
        with ThreadPoolExecutor(WORKERS) as pool:
            results = [r for r in pool.map(grade, jobs) if r is not None]
    graded_ids = [c["id"] for c in cases] if args.carry else "all"
    if carried:
        results += carried
        cases = all_cases

    summary = summarize(args, cases, results, spent[0], graded_ids)
    out = HERE / f"round-{args.round}.json"
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
