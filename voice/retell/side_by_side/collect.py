"""Retell's record of each scripted call in a side-by-side run, saved beside the call's log.

    PYTHONPATH=. uv run --env-file .env python voice/retell/side_by_side/collect.py <run_dir>

Finds each call in manifest.jsonl by the number it dialed and its time window (POST /v3/list-calls,
read `items`), then saves GET /v2/get-call/<id> as calls/<tag>.retell.json. Calls already saved
are skipped, so it can run again while a slate is still going.
"""

import json
import os
import sys
from pathlib import Path

from voice.retell.api import Retell


def find_call(retell: Retell, entry: dict) -> dict | None:
    body = {
        "filter_criteria": {
            "to_number": {"type": "string", "op": "eq", "value": entry["number"]},
            "start_timestamp": {
                "type": "range",
                "op": "bt",
                "value": [entry["start_ms"] - 5000, entry["end_ms"] + 5000],
            },
        },
        "sort_order": "ascending",
        "limit": 10,
    }
    items = retell.request("POST", "/v3/list-calls", json=body).get("items", [])
    mine = [c for c in items if c.get("agent_id") == entry["agent_id"]]
    return retell.get_call(mine[0]["call_id"]) if mine else None


def main() -> None:
    run = Path(sys.argv[1])
    retell = Retell(os.environ["RETELL_API_KEY"])
    for line in (run / "manifest.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        entry.setdefault("number", "+16466240418")
        out = run / entry["log"].replace(".log", ".retell.json")
        if out.exists():
            continue
        call = find_call(retell, entry)
        if call is None:
            print(f"no Retell call for {entry['log']} on {entry['agent_id']}", file=sys.stderr)
            continue
        out.write_text(json.dumps(call, indent=1))
        cost = (call.get("call_cost") or {}).get("combined_cost") or 0
        print(f"{entry['log']}: {call['call_id']} ${cost / 100:.2f}")


if __name__ == "__main__":
    main()
