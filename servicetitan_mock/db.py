import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from fastapi.responses import JSONResponse

from .errors import ApiError

SCHEMA = Path(__file__).with_name("schema.sql")
ADDED = Path(__file__).with_name("schema_added.sql")
# Raised whenever schema.sql changes, so a database seeded under an older one is rebuilt
# (python -m servicetitan_mock.seed --if-stale) instead of failing on a missing column.
SCHEMA_VERSION = 2


def connect(path: str | Path) -> sqlite3.Connection:
    # isolation_level=None: transactions are explicit (see write()).
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def create_schema(path: str | Path) -> None:
    """Create a fresh database file, replacing any existing one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    conn = connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA.read_text())
    conn.executescript(ADDED.read_text())
    conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.close()


def add_missing_tables(path: str | Path) -> None:
    """Create the tables in schema_added.sql on a database that predates them. Those tables came
    after the live world was seeded; adding them this way keeps its bookings, where raising
    SCHEMA_VERSION would rebuild the world from the seed."""
    if not Path(path).exists():
        return
    conn = connect(path)
    try:
        conn.executescript(ADDED.read_text())
    finally:
        conn.close()


def schema_is_current(path: str | Path) -> bool:
    """Does the database exist and was it built under the current schema?"""
    if not Path(path).exists():
        return False
    conn = connect(path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
    return bool(row) and row[0] == str(SCHEMA_VERSION)


def write(
    conn: sqlite3.Connection,
    fn: Callable[[sqlite3.Connection], tuple[int, dict]],
    *,
    idem_key: str | None = None,
    route: str = "",
    body: dict | None = None,
) -> JSONResponse:
    """Run fn in one write transaction and return its (status, payload) as JSON.

    With an idempotency key, the first result is stored and every later request with the same
    key and body gets it back. The key is checked inside the same transaction as the write, so
    two parallel requests with one key cannot both run.
    """
    request_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if idem_key:
            row = conn.execute(
                "SELECT request_hash, status, body FROM idempotency WHERE key = ? AND route = ?",
                (idem_key, route),
            ).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise ApiError(
                        422,
                        "idempotency_key_reused",
                        "That Idempotency-Key was already used with a different request body.",
                    )
                conn.execute("COMMIT")
                return JSONResponse(
                    json.loads(row["body"]), row["status"], headers={"Idempotent-Replayed": "true"}
                )
        status, payload = fn(conn)
        if idem_key:
            conn.execute(
                "INSERT INTO idempotency (key, route, request_hash, status, body) VALUES (?, ?, ?, ?, ?)",
                (idem_key, route, request_hash, status, json.dumps(payload)),
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return JSONResponse(payload, status)


def page_of(
    conn: sqlite3.Connection,
    sql: str,
    params: list,
    shape: Callable[[sqlite3.Row], dict],
    page: int,
    page_size: int,
) -> dict:
    """Wrap a query in ServiceTitan's list envelope."""
    total = conn.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    rows = conn.execute(
        f"{sql} LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size]
    ).fetchall()
    return {
        "page": page,
        "pageSize": page_size,
        "hasMore": page * page_size < total,
        "totalCount": total,
        "data": [shape(r) for r in rows],
    }
