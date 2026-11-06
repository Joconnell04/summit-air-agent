"""What the tool server recorded for the calls that started in a time window, as JSON.

    python3 rows.py <tool_server.db> <start_ms> <end_ms> [<after_rowid>]

Run right after each scripted call, before the next world reset, so the do-not-call list and the
records are read as the call left them. With <after_rowid> (the highest calls rowid before the
call), the calls are the rows added since, whatever their timestamps: a world whose clock is
shifted (SUMMIT_CLOCK_OFFSET_H) stamps its calls in its own time. Standard library only.
"""

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta

TABLES = ("bookings", "booking_names", "call_names", "flags", "callbacks", "leads")


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    db, start_ms, end_ms = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    since = iso(start_ms - 5000)
    until = iso(end_ms + 5000)
    if len(sys.argv) > 4:
        query = ("SELECT * FROM calls WHERE rowid > ? ORDER BY rowid", (int(sys.argv[4]),))
    else:
        query = (
            "SELECT * FROM calls WHERE started_at >= ? AND started_at <= ? ORDER BY started_at",
            (since, until),
        )
    calls = [dict(r) for r in conn.execute(*query)]
    out = {"window": [since, until], "calls": []}
    for call in calls:
        rows = {
            t: [
                dict(r) for r in conn.execute(f"SELECT * FROM {t} WHERE call_id = ?", (call["id"],))
            ]
            for t in TABLES
        }
        out["calls"].append({"call": call, **rows})
    out["do_not_call"] = [dict(r) for r in conn.execute("SELECT * FROM do_not_call")]
    out["dumped_at"] = (datetime.now(UTC) + timedelta()).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
