import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from fastapi.responses import JSONResponse

from .errors import ApiError

SCHEMA = Path(__file__).with_name("schema.sql")


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
    conn.close()


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
                        422, "idempotency_key_reused",
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
    conn: sqlite3.Connection, sql: str, params: list, shape: Callable[[sqlite3.Row], dict],
    page: int, page_size: int,
) -> dict:
    """Wrap a query in ServiceTitan's list envelope."""
    total = conn.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    rows = conn.execute(f"{sql} LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size]).fetchall()
    return {
        "page": page,
        "pageSize": page_size,
        "hasMore": page * page_size < total,
        "totalCount": total,
        "data": [shape(r) for r in rows],
    }
