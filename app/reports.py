"""Reports from the owner (story O-8): "this was wrong on the call, and here is what should have
happened." The owner view stores them; the jobs loop files each one as a GitHub issue carrying the
replay command for the call, so the fix arrives with an eval case (`make replay CALL=...`).

An issue carries the reported line of the transcript, so the repository it goes to must stay
private: the transcript purge in app/jobs.py doesn't reach it.

With no REPORTS_GITHUB_TOKEN a report is held: it stays on the owner view with that status, the
way an alert email is held without a mail server. A filing that fails is tried again on the next
runs, up to REPORT_MAX_ATTEMPTS.
"""

import sqlite3
from collections.abc import Callable
from datetime import datetime

import httpx

from .db import iso, parse_iso, tx
from .settings import REPORT_MAX_ATTEMPTS, Settings
from .tenant import Tenant

# Opens one issue and returns its number. Tests pass a fake; the server uses github_poster.
Poster = Callable[[str, str], int]


def add(conn: sqlite3.Connection, call_id: str, turn_seq: int | None, text: str, now: datetime):
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO owner_reports (call_id, turn_seq, text, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (call_id, turn_seq, text.strip(), iso(now), iso(now)),
        )
    return cur.lastrowid


def for_call(conn: sqlite3.Connection, call_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM owner_reports WHERE call_id = ? ORDER BY id", (call_id,)
    ).fetchall()
    return [{**dict(r), "at": parse_iso(r["created_at"])} for r in rows]


def recent(conn: sqlite3.Connection, tenant: Tenant, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT r.* FROM owner_reports r JOIN calls c ON c.id = r.call_id WHERE c.tenant = ? "
        "ORDER BY r.id DESC LIMIT ?",
        (tenant.slug, limit),
    ).fetchall()
    return [{**dict(r), "at": parse_iso(r["created_at"])} for r in rows]


def issue_text(report: sqlite3.Row, tenant: Tenant, turn: str | None) -> tuple[str, str]:
    title = " ".join(f"{tenant.name} owner report: {report['text']}".split())
    title = title if len(title) <= 100 else title[:99].rstrip() + "…"
    lines = [
        f"Call `{report['call_id']}`, reported {report['created_at']} from the owner view.",
        "",
        "What the owner says should have happened:",
        "",
        "> " + "\n> ".join(report["text"].splitlines()),
    ]
    if turn:
        lines += ["", f"About this line of the call (turn {report['turn_seq']}):", ""]
        lines += ["> " + line for line in turn.splitlines()]
    lines += [
        "",
        "Reproduce and keep as an eval case:",
        "",
        f"    make replay CALL={report['call_id']}",
    ]
    return title, "\n".join(lines)


def github_poster(settings: Settings, http: httpx.Client | None = None) -> Poster | None:
    """An issue poster for the configured repository, or None when reports are to be held."""
    if not (settings.reports_github_token and settings.reports_github_repo):
        return None
    client = http or httpx.Client(timeout=10)

    def post(title: str, body: str) -> int:
        response = client.post(
            f"https://api.github.com/repos/{settings.reports_github_repo}/issues",
            headers={
                "Authorization": f"Bearer {settings.reports_github_token}",
                "Accept": "application/vnd.github+json",
            },
            json={"title": title, "body": body, "labels": ["owner-report"]},
        )
        response.raise_for_status()
        return response.json()["number"]

    return post


def file_held(conn: sqlite3.Connection, tenant: Tenant, post: Poster | None, now: datetime) -> dict:
    """File each held or failed report for the tenant as an issue. Without a poster nothing is
    sent and reports stay held."""
    counts = {"filed": 0, "failed": 0, "held": 0}
    rows = conn.execute(
        "SELECT r.* FROM owner_reports r JOIN calls c ON c.id = r.call_id WHERE c.tenant = ? "
        "AND r.status IN ('held', 'failed') AND r.attempts < ? ORDER BY r.id",
        (tenant.slug, REPORT_MAX_ATTEMPTS),
    ).fetchall()
    if post is None:
        counts["held"] = len(rows)
        return counts
    for report in rows:
        turn = conn.execute(
            "SELECT text FROM turns WHERE call_id = ? AND seq = ?",
            (report["call_id"], report["turn_seq"]),
        ).fetchone()
        title, body = issue_text(report, tenant, turn["text"] if turn else None)
        try:
            number, status, error = int(post(title, body)), "filed", None
        except Exception as exc:  # noqa: BLE001
            # A bad setting or reply fails this report only; the rest of the jobs run still runs.
            number, status, error = None, "failed", f"{type(exc).__name__}: {exc}"
        with tx(conn):
            conn.execute(
                "UPDATE owner_reports SET status = ?, issue_number = ?, attempts = attempts + 1, "
                "error = ?, updated_at = ? WHERE id = ?",
                (status, number, error, iso(now), report["id"]),
            )
        counts[status] += 1
    return counts
