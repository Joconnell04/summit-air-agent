"""Search for the owner view: every call on record, its job, the flags it raised, its leads and
callbacks, and each line said on it, in one SQLite full-text index (search_docs in schema.sql).

The index is kept current by the jobs loop and again before each search. A call is indexed again
only when its fingerprint changes (new lines, an outcome, a flag closed, a job booked), so a
refresh with nothing new costs one query. Purged calls leave the index on the next refresh.
"""

import json
import re
import sqlite3
from datetime import datetime

from markupsafe import Markup, escape

from . import owner
from .db import SCHEMA, parse_iso, tx
from .tenant import Tenant

# Sections of the results page, in the order shown.
KINDS = {
    "call": "Calls",
    "job": "Jobs",
    "flag": "Needs a person",
    "lead": "Leads and callbacks",
    "said": "Said on a call",
}
PER_KIND = 8
SUGGEST = 8

# Private-use characters mark a match in a snippet, so the text can be escaped before the marks
# become <mark> tags.
OPEN, CLOSE = "", ""

# A call's fingerprint: anything that changes one of its documents changes this. Turns are counted
# rather than read from turn_seq, so a transcript removed by retention leaves the index too.
SIGNATURES = """
SELECT c.id,
       IFNULL(c.ended_at, '') || '|' || IFNULL(c.outcome, '') || '|' || c.safety_state || '|' ||
       c.commercial || '|' || IFNULL(c.safety_kind, '') || '|' ||
       (SELECT COUNT(*) || ':' || IFNULL(MAX(t.seq), '') FROM turns t WHERE t.call_id = c.id) || '|' ||
       (SELECT COUNT(*) FROM tool_calls x WHERE x.call_id = c.id) || '|' ||
       (SELECT IFNULL(GROUP_CONCAT(f.id || f.status), '') FROM flags f WHERE f.call_id = c.id) || '|' ||
       (SELECT IFNULL(GROUP_CONCAT(b.id || b.status || b.updated_at), '')
          FROM bookings b WHERE b.call_id = c.id) || '|' ||
       (SELECT IFNULL(GROUP_CONCAT(k.id || k.status), '') FROM callbacks k WHERE k.call_id = c.id) || '|' ||
       (SELECT COUNT(*) FROM leads l WHERE l.call_id = c.id) AS signature
FROM calls c WHERE c.tenant = ?
"""
# Calls indexed per transaction, so a first build never holds the write lock for long.
BATCH = 100
COLUMNS = ["tenant", "call_id", "kind", "anchor", "at", "label", "title", "body", "extra"]


def _ensure_shape(conn: sqlite3.Connection) -> None:
    """An index built by an earlier version of this file is dropped and built again from scratch,
    since FTS5 tables can't gain columns."""
    found = [r[1] for r in conn.execute("PRAGMA table_info(search_docs)")]
    if found != COLUMNS:
        conn.execute("DROP TABLE IF EXISTS search_docs")
        conn.execute("DROP TABLE IF EXISTS search_indexed")
        conn.executescript(SCHEMA.read_text())


def _digits(text: str) -> str:
    """A phone number's digits as single words too, so "7185550156" and "555-0156" both find it."""
    d = re.sub(r"\D", "", text)
    return " ".join(x for x in (d, d[-7:], d[-4:]) if len(x) >= 4)


def _clean(value):
    """A caller could say the private-use marks; they are dropped so only a real match is marked."""
    return value.replace(OPEN, "").replace(CLOSE, "") if isinstance(value, str) else value


def _join(*parts: str) -> str:
    return ", ".join(p for p in parts if p)


def _docs(conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row) -> list[tuple]:
    """The rows one call adds to the index: (kind, anchor, at, label, title, body, extra). The
    label is what a result is headed with and is not searched; the title is searched and weighs
    most. The body is what a result shows; extra holds words that should match but read badly,
    such as a phone number's bare digits and the day's name."""
    code = owner.call_type(conn, call)
    if call["direction"] == "outbound":
        placed = conn.execute(
            "SELECT kind FROM outbound WHERE placed_call_id = ?", (call["id"],)
        ).fetchone()
        code = placed["kind"] if placed else code
    jobs = conn.execute(
        "SELECT * FROM bookings WHERE call_id = ? AND kind = 'book' ORDER BY id", (call["id"],)
    ).fetchall()
    names = []
    docs = []
    for job in jobs:
        request = json.loads(job["request_json"])
        address = request.get("address") or {}
        name = (request.get("details") or {}).get("name", "")
        names.append(name)
        street = " ".join(x for x in (address.get("street", ""), address.get("unit", "")) if x)
        window = ""
        if job["window_start"]:
            window = tenant.local(parse_iso(job["window_start"])).strftime("%a %b %-d, %-I %p")
        title = owner.type_label(tenant, job["job_type"] or "other")
        body = _join(
            name,
            " ".join(x for x in (street, address.get("city", ""), address.get("zip", "")) if x),
            f"job {job['fsm_job_id']}" if job["fsm_job_id"] else "",
            job["priority"] or "",
            job["priority_reasons"],
            window,
        )
        heading = _join(title, street)
        docs.append(("job", "", job["created_at"], heading, heading, body, ""))
    for lead in conn.execute("SELECT * FROM leads WHERE call_id = ?", (call["id"],)):
        details = json.loads(lead["request_json"]).get("details") or {}
        contact = _join(*(str(details.get(k, "")) for k in ("contact_name", "company", "need")))
        title = owner.type_label(tenant, lead["kind"])
        body = _join(lead["summary"], contact)
        docs.append(("lead", "", lead["created_at"], title, title, body, ""))
    for cb in conn.execute("SELECT * FROM callbacks WHERE call_id = ?", (call["id"],)):
        body = _join(cb["reason"], cb["phone"])
        docs.append(
            ("lead", "", cb["created_at"], "Callback", "callback", body, _digits(cb["phone"]))
        )
    for flag in conn.execute("SELECT * FROM flags WHERE call_id = ?", (call["id"],)):
        shaped = {"code": flag["kind"], "kind": flag["kind"].replace("_", " ").capitalize()}
        title = owner.flag_title(tenant, shaped, call["safety_kind"])
        status = "open" if flag["status"] == "open" else "resolved"
        docs.append(
            (
                "flag",
                "",
                flag["created_at"],
                title,
                title,
                flag["reason"],
                f"{flag['priority']} {status}",
            )
        )
    for turn in conn.execute(
        "SELECT seq, speaker, text, at FROM turns WHERE call_id = ? ORDER BY seq", (call["id"],)
    ):
        who = "Agent" if turn["speaker"] == "agent" else "Caller"
        # The speaker heads the result but is not searched, or "caller" would match every line.
        docs.append(("said", f"turn-{turn['seq']}", turn["at"], who, "", turn["text"], ""))
    name = next((n for n in names if n), "")
    local = tenant.local(parse_iso(call["started_at"]))
    body = _join(
        name,
        call["caller_id"],
        owner.type_label(tenant, code),
        owner.outcome_label(call["outcome"], bool(call["ended_at"])),
        "placed by us" if call["direction"] == "outbound" else "",
    )
    extra = " ".join(
        x
        for x in (
            _digits(call["caller_id"]),
            "safety " + call["safety_kind"] if call["safety_kind"] else "",
            "after hours" if call["after_hours"] else "",
            local.strftime("%A %b %-d"),
        )
        if x
    )
    heading = name or call["caller_id"]
    docs.append(("call", "", call["started_at"], heading, heading, body, extra))
    return docs


def refresh(conn: sqlite3.Connection, tenant: Tenant) -> int:
    """Index the calls that changed since they were last indexed, and drop purged ones. Returns
    how many calls were indexed."""
    _ensure_shape(conn)
    current = {r["id"]: r["signature"] for r in conn.execute(SIGNATURES, (tenant.slug,))}
    stored = {
        r["call_id"]: r["signature"]
        for r in conn.execute(
            "SELECT call_id, signature FROM search_indexed WHERE tenant = ?", (tenant.slug,)
        )
    }
    changed = [c for c, sig in current.items() if stored.get(c) != sig]
    gone = [c for c in stored if c not in current]
    if gone:
        with tx(conn):
            for call_id in gone:
                conn.execute("DELETE FROM search_docs WHERE call_id = ?", (call_id,))
                conn.execute("DELETE FROM search_indexed WHERE call_id = ?", (call_id,))
    for i in range(0, len(changed), BATCH):
        with tx(conn):
            for call_id in changed[i : i + BATCH]:
                call = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
                if call_id in stored:
                    conn.execute("DELETE FROM search_docs WHERE call_id = ?", (call_id,))
                conn.executemany(
                    "INSERT INTO search_docs "
                    "(tenant, call_id, kind, anchor, at, label, title, body, extra) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (tenant.slug, call_id, *map(_clean, doc))
                        for doc in _docs(conn, tenant, call)
                    ],
                )
                conn.execute(
                    "INSERT OR REPLACE INTO search_indexed (call_id, tenant, signature) "
                    "VALUES (?, ?, ?)",
                    (call_id, tenant.slug, current[call_id]),
                )
    return len(changed)


def match_expression(query: str) -> str:
    """The query as an FTS5 expression: every word must match, each as a prefix. Punctuation is
    dropped, so nothing typed can reach FTS5's own syntax. A lone one- or two-digit number is left
    out, since it would match nearly every call."""
    words = [w for w in re.findall(r"\w+", query.lower()) if not (w.isdigit() and len(w) < 3)]
    return " ".join(f'"{w}"*' for w in words)


def _marked(text: str) -> Markup:
    return Markup(str(escape(text)).replace(OPEN, "<mark>").replace(CLOSE, "</mark>"))


def find(conn: sqlite3.Connection, tenant: Tenant, query: str, per_kind: int = PER_KIND) -> dict:
    """Matches grouped by kind, best first within each, with the match marked in a snippet, and
    how many of each kind matched in all. Each kind is its own query, so one kind with many matches
    cannot crowd out the others."""
    refresh(conn, tenant)
    expression = match_expression(query)
    groups = {kind: [] for kind in KINDS}
    counts = {kind: 0 for kind in KINDS}
    if not expression:
        return {"groups": groups, "counts": counts, "total": 0}
    for kind in KINDS:
        counts[kind] = conn.execute(
            "SELECT COUNT(*) FROM search_docs WHERE search_docs MATCH ? AND tenant = ? AND kind = ?",
            (expression, tenant.slug, kind),
        ).fetchone()[0]
        if counts[kind]:
            groups[kind] = _hits(conn, tenant, expression, kind, per_kind)
    return {"groups": groups, "counts": counts, "total": sum(counts.values())}


def _hits(conn: sqlite3.Connection, tenant: Tenant, expression: str, kind: str, limit: int):
    rows = conn.execute(
        f"SELECT call_id, anchor, at, label, "
        f"snippet(search_docs, 7, '{OPEN}', '{CLOSE}', ' ... ', 14) AS snip, "
        "bm25(search_docs, 0, 0, 0, 0, 0, 0, 4.0, 1.0, 0.5) AS rank "
        "FROM search_docs WHERE search_docs MATCH ? AND tenant = ? AND kind = ? "
        "ORDER BY rank, at DESC LIMIT ?",
        (expression, tenant.slug, kind, limit),
    ).fetchall()
    return [
        {
            "call_id": row["call_id"],
            "href": f"/owner/calls/{row['call_id']}"
            + (f"#{row['anchor']}" if row["anchor"] else ""),
            "at": parse_iso(row["at"]),
            "day": tenant.local(parse_iso(row["at"])).strftime("%a %b %-d"),
            "title": row["label"],
            "snippet": _marked(row["snip"]),
            "text": row["snip"].replace(OPEN, "").replace(CLOSE, ""),
        }
        for row in rows
    ]


def suggest(conn: sqlite3.Connection, tenant: Tenant, query: str, now: datetime) -> list[dict]:
    """The few best matches for the search box as it is typed, across kinds, as plain text."""
    found = find(conn, tenant, query, per_kind=3)
    items = []
    for kind, label in KINDS.items():
        for hit in found["groups"][kind]:
            local = tenant.local(hit["at"])
            when = local.strftime("%-I:%M %p")
            if local.date() != tenant.local(now).date():
                when = local.strftime("%a %b %-d, ") + when
            items.append(
                {
                    "kind": label,
                    "title": hit["title"],
                    "text": hit["text"],
                    "when": when,
                    "href": hit["href"],
                }
            )
    return items[:SUGGEST]
