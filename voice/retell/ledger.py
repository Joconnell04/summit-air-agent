"""Which lines of a call are already posted to the tool server's /calls/{id}/turn.

Retell sends the whole transcript with every event, from more than one place at once (a custom
function, the transcript_updated webhook, call_ended), so each sync posts only what is new. One
sync at a time per call keeps the lines in order; the record is in SQLite so a restarted bridge
doesn't post a call's lines twice.
"""

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .timings import new_words, turn_timings
from .transcript import Line

# post(call_id, speaker, text, timings) returns the tool server's envelope, or None if it failed.
Post = Callable[[str, str, str, dict], dict | None]

SCHEMA = """
CREATE TABLE IF NOT EXISTS posted (
    call_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (call_id, idx)
);
CREATE TABLE IF NOT EXISTS calls (
    retell_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    started INTEGER NOT NULL DEFAULT 0,
    ended INTEGER NOT NULL DEFAULT 0,
    -- 1 between an end_call the server allowed and the bridge stopping the call.
    hangup_pending INTEGER NOT NULL DEFAULT 0,
    hung_up INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT
);
"""


class Ledger:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, call_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(call_id, threading.Lock())

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def sync(
        self, call_id: str, lines: list[Line], post: Post, settled: bool = True
    ) -> list[tuple[str, dict]]:
        """Post every line not posted yet, in order, and return (speaker, tool server answer)
        for each.

        A line can grow between events. A caller line that grew is posted again whole, so a phrase
        split by a pause ("I smell... gas") reaches the safety check in one piece. An agent line
        that grew (Retell adds the agent's words as they are spoken) is posted as the new part
        only, so the trace shows each thing the agent said once. An older, shorter copy of a line,
        from an event that arrived late, is skipped.

        Not settled (a transcript_updated in the middle of a call): a last line from the agent may
        still be playing, and Retell's transcript holds only the words spoken so far, so it waits
        for the next event rather than going in half said. Caller lines never wait.
        """
        if not settled and lines and lines[-1].speaker == "agent":
            lines = lines[:-1]
        answers = []
        with self._lock(call_id), self.connect() as conn:
            posted = {
                r["idx"]: r["text"]
                for r in conn.execute("SELECT idx, text FROM posted WHERE call_id = ?", (call_id,))
            }
            for i, line in enumerate(lines):
                before = posted.get(i)
                if before is not None and before.startswith(line.text):
                    continue
                text, words = line.text, line.words
                if before is not None and line.speaker == "agent" and text.startswith(before):
                    text = text[len(before) :].strip()
                    words = new_words(line, len(before))
                timings = turn_timings(lines[:i], words) if line.speaker == "agent" else {}
                answer = post(call_id, line.speaker, text, timings) if text else {}
                if answer is None:
                    break  # the tool server didn't take it; the next sync tries again from here
                answers.append((line.speaker, answer))
                conn.execute(
                    "INSERT INTO posted (call_id, idx, text) VALUES (?, ?, ?) "
                    "ON CONFLICT (call_id, idx) DO UPDATE SET text = excluded.text",
                    (call_id, i, line.text),
                )
        return answers

    def posted_all(self, call_id: str, lines: list[Line]) -> bool:
        """Has every line reached the tool server, in the sense `sync` skips it?"""
        with self.connect() as conn:
            posted = {
                r["idx"]: r["text"]
                for r in conn.execute("SELECT idx, text FROM posted WHERE call_id = ?", (call_id,))
            }
        return all(posted.get(i, "\0").startswith(line.text) for i, line in enumerate(lines))

    # ---- the Retell id to our call id, and what happened to it ---------------------------------

    def remember(self, retell_id: str, call_id: str, kind: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO calls (retell_id, call_id, kind) VALUES (?, ?, ?)",
                (retell_id, call_id, kind),
            )

    def mark(self, retell_id: str, **fields) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE calls SET {sets} WHERE retell_id = ?", (*fields.values(), retell_id)
            )

    def get(self, retell_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM calls WHERE retell_id = ?", (retell_id,)).fetchone()

    def summaries(self) -> list[dict]:
        """Retell's latency and cost per call (timings.call_summary), newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT call_id, retell_id, summary_json FROM calls "
                "WHERE summary_json IS NOT NULL ORDER BY rowid DESC"
            ).fetchall()
        return [
            {"call_id": r["call_id"], "retell_id": r["retell_id"], **json.loads(r["summary_json"])}
            for r in rows
        ]
