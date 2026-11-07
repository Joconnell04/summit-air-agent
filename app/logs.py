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
    lock_ms: float = 0.0,
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
        # The step the server told the agent to take next, when it named one (app/envelope.py).
        "next": body.get("next", ""),
        "result": body["data"],
        "replayed": replayed,
        "ms": ms,
        # Time spent waiting for the call's lock before the handler ran: a calls.start took 2.9 s
        # with 16 ms of it in ServiceTitan, and this says whether the rest was a wait.
        "lock_ms": lock_ms,
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


def rewrite_turn_texts(path: Path, call_id: str, olds: dict[str, str]) -> None:
    """Earlier caller lines found to hold part of a card get the redacted text in the JSON log
    too (app/cards.py). The file is written whole and swapped in, under the write lock."""
    if not olds or not path.exists():
        return
    with _write_lock:
        lines = path.read_text().splitlines()
        out = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                out.append(line)
                continue
            text = (row.get("args") or {}).get("text") if row.get("call_id") == call_id else None
            if row.get("tool") == "turn" and text in olds:
                row["args"]["text"] = olds[text]
                line = json.dumps(row, default=str)
            out.append(line)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text("\n".join(out) + "\n")
        temp.replace(path)
