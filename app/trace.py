"""A call's timeline: what the caller said, what the agent said, each tool call and its result, the
gates that fired, flags and callbacks. Served at GET /debug/calls/{id} and printed as plain text by
`make trace CALL=<id>`, so a reviewing agent can read a call without a browser.

Run: python -m app.trace <call_id> [--db data/tool_server.db]
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .db import connect
from .settings import Settings


def timeline(conn: sqlite3.Connection, call_id: str) -> dict | None:
    call = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    if not call:
        return None
    events = []
    for t in conn.execute("SELECT * FROM turns WHERE call_id = ? ORDER BY seq", (call_id,)):
        events.append(
            {
                "at": t["at"],
                "order": (t["seq"], 0),
                "type": "turn",
                "seq": t["seq"],
                "speaker": t["speaker"],
                "text": t["text"],
                "safety_action": t["safety_action"],
                "timings": json.loads(t["timings_json"]),
            }
        )
    seq = 0
    for row in conn.execute("SELECT * FROM tool_calls WHERE call_id = ? ORDER BY id", (call_id,)):
        if row["tool"] == "turn":
            seq += 1
            continue
        events.append(
            {
                "at": row["at"],
                "order": (seq, 1, row["id"]),
                "type": "tool",
                "tool": row["tool"],
                "args": json.loads(row["args_json"]),
                "ok": bool(row["ok"]),
                "refused": row["refused"],
                "gate": row["gate"],
                "say": row["say"],
                "ms": row["ms"],
                "error": row["error"],
                "request_id": row["request_id"],
            }
        )
    events.sort(key=lambda e: e["order"])
    for event in events:
        del event["order"]

    def rows(sql: str) -> list[dict]:
        return [dict(r) for r in conn.execute(sql, (call_id,))]

    return {
        "call": dict(call),
        "events": events,
        "flags": rows("SELECT * FROM flags WHERE call_id = ? ORDER BY id"),
        "callbacks": rows("SELECT * FROM callbacks WHERE call_id = ? ORDER BY id"),
        "bookings": rows(
            "SELECT id, kind, status, fsm_job_id, window_start, priority, priority_reasons, "
            "safety_override, seek_earlier, attempts, last_error, "
            "json_extract(request_json, '$.raised_after_wait') AS raised_after_wait, "
            "json_extract(request_json, '$.priority_rules') AS priority_rules, "
            "json_extract(request_json, '$.priority_reasons') AS priority_reason_list, "
            "json_extract(request_json, '$.details.who_home') AS who_home, job_type, "
            "json_extract(request_json, '$.address.street') AS street, "
            "json_extract(request_json, '$.address.unit') AS unit, "
            "json_extract(request_json, '$.notes') AS job_notes "
            "FROM bookings WHERE call_id = ? ORDER BY id"
        ),
        "notes": rows("SELECT attach_to, text, fsm_note_id FROM notes WHERE call_id = ? ORDER BY id"),
        "leads": rows("SELECT id, kind, summary, status FROM leads WHERE call_id = ? ORDER BY id"),
        "out_of_area": rows("SELECT zip, at FROM out_of_area WHERE call_id = ?"),
        "summary": rows("SELECT * FROM summaries WHERE call_id = ?"),
        # The electrical route and holds (DECISIONS.md 081, 082).
        "electrical": rows("SELECT * FROM electrical WHERE call_id = ?"),
        "holds": rows("SELECT * FROM holds WHERE call_id = ? ORDER BY seq"),
        # Speaking rate (app/pace.py): the pace in effect, each change, and what is remembered for
        # the caller's number.
        "pace": rows("SELECT * FROM call_pace WHERE call_id = ?"),
        "pace_changes": rows("SELECT * FROM pace_changes WHERE call_id = ? ORDER BY id"),
        "caller_pace": [
            dict(r)
            for r in conn.execute(
                "SELECT speed, call_id, set_at FROM caller_pace WHERE tenant = ? AND phone_key = ?",
                (call["tenant"], call["caller_key"]),
            )
        ],
        "transfers": rows(
            "SELECT id, flag_id, callback_id, role, status FROM transfers WHERE call_id = ?"
        ),
        "handoffs": rows(
            "SELECT id, flag_id, callback_id, role, status FROM handoffs WHERE call_id = ?"
        ),
        # The address lookup (app/geocode.py) and what the caller asked to be called.
        "address_lookups": rows(
            "SELECT house, street, hint, status, zips_json, source FROM address_lookups "
            "WHERE call_id = ? ORDER BY id"
        ),
        "address_checks": rows("SELECT * FROM address_checks WHERE call_id = ?"),
        "names": rows("SELECT name, seq, saved_at FROM call_names WHERE call_id = ?"),
        # A caller who moved, and the name and address check for a change from a number that
        # isn't on the account (issue #128).
        "address_moves": rows("SELECT * FROM address_moves WHERE call_id = ? ORDER BY id"),
        "identity_checks": rows(
            "SELECT customer_id, status, asks, callback_id FROM identity_checks WHERE call_id = ?"
        ),
        "heads_ups": rows(
            "SELECT id, customer_id, job_id, status, result, line FROM heads_ups WHERE call_id = ?"
        ),
    }


def as_text(trace: dict) -> str:
    call = trace["call"]
    ended = f"ended {call['ended_at'] or '-'} ({call['end_reason'] or 'open'})"
    lines = [
        f"call {call['id']}  {call['tenant']}  from {call['caller_id']}  started {call['started_at']}",
        f"{ended}  outcome {call['outcome'] or '-'}  safety {call['safety_state']}"
        + f"  linked to {call['linked_call_id'] or '-'}",
        "",
    ]
    for e in trace["events"]:
        time = e["at"][11:19]
        if e["type"] == "turn":
            mark = f"  [safety: {e['safety_action']}]" if e["safety_action"] else ""
            stages = "  " + json.dumps(e["timings"]) if e["timings"] else ""
            lines.append(f"{time}  {e['speaker']:>6}: {e['text']}{mark}{stages}")
        else:
            status = "ok" if e["ok"] else f"REFUSED {e['refused']}"
            gate = f"  gate={e['gate']}" if e["gate"] else ""
            lines.append(f"{time}    tool {e['tool']} -> {status}{gate}  ({e['ms']} ms)")
            lines.append(f"              args {json.dumps(e['args'])}")
            if e["say"]:
                lines.append(f"              say  {e['say']}")
            if e["error"]:
                lines.append(f"              error {e['error']}")
    for title in ("flags", "callbacks", "bookings", "leads", "out_of_area", "summary", "pace_changes",
                  "transfers", "handoffs"):
        if trace[title]:
            lines += ["", f"{title}:"] + [f"  {json.dumps(r)}" for r in trace[title]]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a call's timeline.")
    parser.add_argument("call_id")
    parser.add_argument("--db", default=str(Settings().db_path))
    args = parser.parse_args()
    if not Path(args.db).exists():
        print(f"no database at {args.db}", file=sys.stderr)
        return 1
    conn = connect(args.db)
    trace = timeline(conn, args.call_id)
    conn.close()
    if trace is None:
        print(f"no call {args.call_id} in {args.db}", file=sys.stderr)
        return 1
    print(as_text(trace))
    return 0


if __name__ == "__main__":
    sys.exit(main())
