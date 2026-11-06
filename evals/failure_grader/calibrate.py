"""Grade the calibration set (labels.yaml) with the real failure grader and report precision and
recall (issue #178).

Each call goes through app.failure_grader.grade_call unchanged: the Retell call from
GET /v2/get-call/<id> and the tool server's rows, loaded into a scratch database from the
side-by-side dump. Spend is counted from the model's usage and the run stops before MAX_USD.

    uv run --env-file .env python -m evals.failure_grader.calibrate [--out results.json]

Needs ANTHROPIC_API_KEY and RETELL_API_KEY. The side-by-side dumps are read from the working tree
when present, or from SIDE_BY_SIDE_REF (default origin/joco/side-by-side-014a4c) with git show.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import tempfile
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
MAX_USD = 4.50
TABLES = ("bookings", "flags", "callbacks", "leads")
NO_ROWS = {
    "not_available": "The company's records for this call were not kept for this review; judge "
    "from the transcript and the tool results in it."
}


def read_rows(rel: str) -> dict:
    path = ROOT / DATA / rel
    if path.exists():
        return json.loads(path.read_text())
    return json.loads(subprocess.check_output(["git", "show", f"{REF}:{DATA}/{rel}"], cwd=ROOT))


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def insert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    keep = {k: v for k, v in row.items() if k in columns(conn, table)}
    marks = ",".join("?" * len(keep))
    conn.execute(f"INSERT OR IGNORE INTO {table} ({','.join(keep)}) VALUES ({marks})", list(keep.values()))


def load_call(conn: sqlite3.Connection, retell_id: str, rows_rel: str, retell: dict) -> str:
    """Put the call and its rows in the scratch database; returns our call id."""
    call_id = grader.RETELL_PREFIX + retell_id
    if rows_rel == "none":
        started = datetime.fromtimestamp(retell["start_timestamp"] / 1000, UTC)
        ended = datetime.fromtimestamp(retell["end_timestamp"] / 1000, UTC)
        number = retell.get("from_number") or ""
        insert(conn, "calls", {
            "id": call_id, "tenant": "summit-air", "caller_id": number,
            "caller_key": "".join(ch for ch in number if ch.isdigit())[-10:],
            "dialed_number": retell.get("to_number") or "", "direction": "inbound",
            "started_at": db.iso(started), "ended_at": db.iso(ended),
            "end_reason": retell.get("disconnection_reason"),
        })
        return call_id
    dump = read_rows(rows_rel)
    for entry in dump["calls"]:
        insert(conn, "calls", entry["call"])
        for table in TABLES:
            for row in entry.get(table, []):
                insert(conn, table, row)
    for row in dump.get("do_not_call", []):
        insert(conn, "do_not_call", row)
    return call_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(HERE / "results.json"))
    parser.add_argument("--only", action="append", help="grade only these call ids")
    args = parser.parse_args()

    labels = yaml.safe_load((HERE / "labels.yaml").read_text())
    cases = (
        [(c, True) for c in labels["failures"]]
        + [(c, False) for c in labels["not_failures"]]
        + [(c, None) for c in labels.get("unlabeled", [])]
    )
    if args.only:
        cases = [(c, label) for c, label in cases if c["id"] in args.only]
    settings = Settings()
    tenant = Tenants.load(settings.tenants_dir, settings.default_tenant, settings.trades_dir).default
    model = grader.model_from_config()
    if model is None:
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    transcripts = grader.RetellTranscripts(os.environ["RETELL_API_KEY"])
    no_rows: set[str] = set()
    real_rows = grader.call_rows
    # Only for a live call whose rows weren't dumped: tell the model so, instead of empty records.
    grader.call_rows = lambda conn, t, call: NO_ROWS if call["id"] in no_rows else real_rows(conn, t, call)

    results, spent = [], 0.0
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "tool.db"
        db.init(path)
        conn = db.connect(path)
        for case, label in cases:
            if spent >= MAX_USD:
                print(f"stopped: ${spent:.2f} spent")
                break
            retell = transcripts.get(case["id"])
            call_id = load_call(conn, case["id"], case["rows"], retell)
            if case["rows"] == "none":
                no_rows.add(call_id)
            got = grader.grade_call(conn, tenant, model, transcripts, call_id,
                                    datetime.now(UTC), force=True)
            spent += got["cost_now"]
            results.append({
                "id": case["id"], "label": label, "label_kind": case.get("kind"),
                "flagged": bool(got["failure"]), "kind": got["kind"], "severity": got["severity"],
                "reason": got["reason"], "evidence": json.loads(got["evidence_json"]),
                "dropped": got["dropped"], "error": got["error"], "source": got["source"],
                "raw": json.loads(got["raw_json"]) if got["raw_json"] else None,
                "cost_usd": round(got["cost_now"], 4),
            })
            mark = {True: "FAIL", False: "ok  ", None: "??  "}[label]
            print(f"{mark} {case['id']} flagged={got['failure']} {got['kind'] or ''} "
                  f"${got['cost_now']:.3f} {got['reason'][:90]}")
        conn.close()

    scored = [r for r in results if r["label"] is not None]
    tp = sum(r["label"] and r["flagged"] for r in scored)
    fp = sum(not r["label"] and r["flagged"] for r in scored)
    fn = sum(r["label"] and not r["flagged"] for r in scored)
    summary = {
        "graded": len(results),
        "labeled": len(scored),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "kind_right": sum(r["label"] and r["flagged"] and r["kind"] == r["label_kind"] for r in scored),
        "spent_usd": round(spent, 4),
        "profile": grader.PROFILE,
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "calls": results}, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
