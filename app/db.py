import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: str | Path) -> sqlite3.Connection:
    # isolation_level=None: transactions are explicit (see tx()).
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init(path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA.read_text())
    _relabel(conn)
    conn.close()


def _relabel(conn: sqlite3.Connection) -> None:
    """A database from before DECISIONS.md 130 checks call labels against the old names (Urgent,
    Routine): rebuild that table with the new check. Old rows keep their reason; a visit's label
    becomes Scheduled, since which were dispatches isn't kept, and No visit stays."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'call_labels'"
    ).fetchone()
    if row is None or "'Routine'" not in row["sql"]:
        return
    conn.executescript(
        """
        BEGIN;
        ALTER TABLE call_labels RENAME TO call_labels_old;
        CREATE TABLE call_labels (
            call_id TEXT PRIMARY KEY REFERENCES calls (id) ON DELETE CASCADE,
            label TEXT NOT NULL CHECK (label IN ('Emergency dispatch', 'Scheduled', 'No visit')),
            reason TEXT NOT NULL,
            at TEXT NOT NULL
        );
        INSERT INTO call_labels (call_id, label, reason, at)
            SELECT call_id, CASE label WHEN 'No visit' THEN 'No visit' ELSE 'Scheduled' END,
                   reason, at FROM call_labels_old;
        DROP TABLE call_labels_old;
        COMMIT;
        """
    )


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One write transaction. BEGIN IMMEDIATE takes the write lock up front, so a read-then-write
    inside it cannot interleave with another request's."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)
