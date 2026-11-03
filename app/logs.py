"""One JSON line per request and per turn, and the read side behind GET /debug/logs.

Each line carries the call id, request id, tool, arguments, result, the gate that fired and the
timings: the whole request, each FSM request inside it, and for turns the stage timings the voice
layer sent (docs/latency.md).
"""

import json
import sqlite3
import threading
from pathlib import Path

from .db import iso

MAX_LIMIT = 1000
_write_lock = threading.Lock()


def write(path: Path, conn: sqlite3.Connection, record: dict) -> None:
    with _write_lock, path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    conn.execute(
        """INSERT INTO tool_calls (call_id, request_id, tool, args_json, ok, refused, gate, say, ms,
                                   error, at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            record["call_id"],
            record["request_id"],
            record["tool"],
            json.dumps(record["args"], default=str),
            int(record["ok"]),
            record["refused"],
            record["gate"],
            record["say"],
            record["ms"],
            record["error"],
            record["ts"],
        ),
    )


def record(
    *,
    now,
    request_id: str,
    call_id: str | None,
    tool: str,
    args: dict,
    body: dict,
    gate: str | None,
    ms: float,
    fsm: list,
    error: str | None,
    replayed: bool,
) -> dict:
    return {
        "ts": iso(now),
        "request_id": request_id,
        "call_id": call_id,
        "tool": tool,
        "args": args,
        "ok": body["ok"],
        "refused": body["refused"],
        "gate": gate,
        "say": body["say"],
        "result": body["data"],
        "replayed": replayed,
        "ms": ms,
        "fsm": fsm,
        "error": error,
    }


def read(
    path: Path,
    *,
    call_id: str | None = None,
    request_id: str | None = None,
    tool: str | None = None,
    errors_only: bool = False,
    limit: int = 100,
) -> list[dict]:
    """errors_only keeps refusals and failures, the lines worth reading first when triaging."""
    limit = max(1, min(limit, MAX_LIMIT))
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a half-written line from a crash must not hide every other entry
        if call_id and row["call_id"] != call_id:
            continue
        if request_id and row["request_id"] != request_id:
            continue
        if tool and row["tool"] != tool:
            continue
        if errors_only and row["ok"] and not row["error"]:
            continue
        rows.append(row)
    return rows[-limit:]
