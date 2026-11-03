"""Owner edits to how the agent works (exploration 009): a few wording lines and short guidance per
call stage, stored as versions over the tenant's YAML defaults.

What an owner can change is a fixed list here, and every draft is checked against it in this
module, so no screen can reach a gate. The read-back, the safety script, priority, the service
area and every other rule stay in tenant files and server code, changed only by engineers. A draft
goes live only when the must-pass evals pass against it (evals/gate.py), and a rollback is a new
version that copies an older one, so nothing is ever deleted.

The server decides which stage a call is in, from its own records, never from the model.
"""

import hashlib
import json
import re
import sqlite3
import string
from datetime import datetime

from .db import iso, tx
from .tenant import Tenant
from .text import has_phrase, words

# The stages of a call, in order (exploration 009, section 2a).
STAGES = {
    "before_hello": "Before hello",
    "opening": "Opening",
    "whole_call": "Whole call",
    "booking": "Booking",
    "closing": "Closing",
    "after_call": "After the call",
}
# Stages the owner can add guidance to. Before hello has no model, and after the call there is no
# model step yet that would read it (the technician summary is not built).
GUIDANCE_STAGES = ("opening", "whole_call", "booking", "closing")
GUIDANCE_PER_STAGE = 5
GUIDANCE_MAX_CHARS = 300
LINE_MAX_CHARS = 200

# The wording lines an owner can change: where the default lives in agent.yaml, the stage, and a
# label. The greeting and the callback opening each have a form for a known and an unknown caller.
EDITABLE_LINES = {
    "greeting_unknown": ("voice", "opening", "Greeting"),
    "greeting_known": ("voice", "opening", "Greeting, known caller"),
    "callback_opening": ("lines", "opening", "Callback opening"),
    "callback_opening_unknown": ("lines", "opening", "Callback, no name"),
    "anything_else_first": ("lines", "closing", "Anything else"),
    "goodbye": ("voice", "closing", "Goodbye"),
}

# Why a draft that reaches past the list is refused. The first two are the risks exploration 009
# found: the read-back names the address as a home, which is how every booking confirms the caller
# is residential, and priority brackets set the level a call starts at.
LOCKED = {
    "read_back": (
        "The read-back is locked. It names the address as the caller's home, which is how every "
        "booking checks it isn't a business."
    ),
    "priority": "Priority is locked. It can only go up, and only engineers change it.",
    "safety": "The safety script is locked. It was signed off word for word.",
}
LOCKED_DEFAULT = "This is locked. Only engineers change it."

# Guidance is soft, so it may not touch a subject a locked rule covers, and it may not try to talk
# the model out of its instructions. Matched as whole words. The trade's own safety words (a gas
# smell, say) come from the tenant's agent.yaml (owner_rules.locked_topics).
LOCKED_TOPICS = (
    "911", "safety", "evacuate", "read back", "readback", "commercial", "business", "price",
    "prices", "pricing", "fee", "fees", "cost", "costs", "discount", "free", "priority", "urgent",
    "emergency", "service area", "zip", "out of area", "delete", "cancel", "refund", "promise",
    "guarantee", "ignore", "instruction", "instructions", "override", "system prompt", "server",
)
# Wording lines are said as written, so they may not quote a price either.
LINE_TOPICS = ("price", "prices", "fee", "fees", "cost", "costs", "discount", "free", "$")
# Characters that could pass for prompt structure or a template field.
NOT_PLAIN = re.compile(r"[{}\[\]#`<>\\|*_]")

STATUSES = ("draft", "checking", "published", "blocked", "discarded")


class RulesError(ValueError):
    pass


def default_line(tenant: Tenant, key: str) -> str:
    where = EDITABLE_LINES[key][0]
    source = tenant.lines if where == "lines" else tenant.agent["voice"]
    return " ".join(source[key].split())


def _fields(text: str) -> set[str] | None:
    """The {name} fields in a line, or None if its braces don't parse."""
    try:
        return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}
    except ValueError:
        return None


def _topic(text: str, topics) -> str | None:
    if "$" in topics and "$" in text:
        return "$"
    text_words = words(text)
    return next((t for t in topics if t != "$" and has_phrase(text_words, t)), None)


def validate(tenant: Tenant, content: dict) -> tuple[dict, dict[str, str]]:
    """A draft's content made clean, and what's wrong with it, keyed by field ("lines.goodbye",
    "guidance.booking.2"). Only the editable lines and guidance stages are kept; anything else is an
    error, never ignored, so a locked rule can't be slipped into a draft."""
    errors: dict[str, str] = {}
    clean: dict = {"lines": {}, "guidance": {}}
    if not isinstance(content, dict):
        return clean, {"content": "Not a draft."}
    for key in content:
        if key not in ("lines", "guidance"):
            errors[key] = LOCKED.get(key, LOCKED_DEFAULT)

    lines = content.get("lines") or {}
    if not isinstance(lines, dict):
        lines, errors["lines"] = {}, "Not a list of lines."
    for key, text in lines.items():
        field = f"lines.{key}"
        if key not in EDITABLE_LINES:
            errors[field] = LOCKED.get(key, LOCKED_DEFAULT)
            continue
        text = " ".join(str(text or "").split())
        default = default_line(tenant, key)
        if not text or text == default:
            continue
        wanted, found = _fields(default), _fields(text)
        topic = _topic(text, LINE_TOPICS)
        if len(text) > LINE_MAX_CHARS:
            errors[field] = f"Keep it under {LINE_MAX_CHARS} characters."
        elif found is None or found != wanted:
            keep = ", ".join("{" + f + "}" for f in sorted(wanted))
            errors[field] = f"Keep {keep} in the line, and no other braces." if wanted else (
                "No braces in this line."
            )
        elif topic:
            errors[field] = f"No prices or fees in a line (found \"{topic}\")."
        else:
            clean["lines"][key] = text

    topics = LOCKED_TOPICS + tuple(tenant.agent.get("owner_rules", {}).get("locked_topics", ()))
    guidance = content.get("guidance") or {}
    if not isinstance(guidance, dict):
        guidance, errors["guidance"] = {}, "Not a list of guidance."
    for stage, items in guidance.items():
        if stage not in GUIDANCE_STAGES:
            errors[f"guidance.{stage}"] = "Nothing can be added at this stage."
            continue
        items = [" ".join(str(i or "").split()) for i in (items or [])]
        items = [i for i in items if i]
        if len(items) > GUIDANCE_PER_STAGE:
            errors[f"guidance.{stage}"] = f"At most {GUIDANCE_PER_STAGE} per stage."
        for n, text in enumerate(items):
            field = f"guidance.{stage}.{n}"
            topic = _topic(text, topics)
            if len(text) > GUIDANCE_MAX_CHARS:
                errors[field] = f"Keep it under {GUIDANCE_MAX_CHARS} characters."
            elif NOT_PLAIN.search(text):
                errors[field] = "Plain words only, no symbols like braces or #."
            elif topic:
                errors[field] = f"That touches a locked rule (\"{topic}\"). Ask Revin to change it."
        if items:
            clean["guidance"][stage] = items[:GUIDANCE_PER_STAGE]
    return clean, errors


def content_hash(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    found = dict(row)
    found["content"] = json.loads(found.pop("content_json"))
    found["gate"] = json.loads(found.pop("gate_json")) if found["gate_json"] else None
    return found


def live(conn: sqlite3.Connection, slug: str) -> dict | None:
    """The version calls run under now, or None for the shipped defaults."""
    return _row(
        conn.execute(
            "SELECT * FROM rule_versions WHERE tenant = ? AND status = 'published' "
            "ORDER BY published_at DESC, id DESC LIMIT 1",
            (slug,),
        ).fetchone()
    )


def open_draft(conn: sqlite3.Connection, slug: str) -> dict | None:
    """The draft being edited or checked. A blocked version stays in the history; editing again
    starts a new draft."""
    return _row(
        conn.execute(
            "SELECT * FROM rule_versions WHERE tenant = ? AND status IN ('draft', 'checking') "
            "ORDER BY id DESC LIMIT 1",
            (slug,),
        ).fetchone()
    )


def history(conn: sqlite3.Connection, slug: str) -> list[dict]:
    """Every version but discarded drafts, newest first, with which one is live."""
    rows = [
        _row(r)
        for r in conn.execute(
            "SELECT * FROM rule_versions WHERE tenant = ? AND status != 'discarded' "
            "ORDER BY number DESC",
            (slug,),
        )
    ]
    current = live(conn, slug)
    for r in rows:
        r["live"] = bool(current and r["id"] == current["id"])
    return rows


def get(conn: sqlite3.Connection, version_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM rule_versions WHERE id = ?", (version_id,)).fetchone())


def _insert(conn, slug: str, content: dict, status: str, author: str, note: str, now, **extra):
    number = conn.execute(
        "SELECT COALESCE(MAX(number), 0) + 1 FROM rule_versions WHERE tenant = ?", (slug,)
    ).fetchone()[0]
    columns = {
        "tenant": slug,
        "number": number,
        "status": status,
        "content_json": json.dumps(content, sort_keys=True),
        "content_hash": content_hash(content),
        "author": author,
        "note": note,
        "created_at": iso(now),
        "updated_at": iso(now),
        **extra,
    }
    cursor = conn.execute(
        f"INSERT INTO rule_versions ({', '.join(columns)}) VALUES "
        f"({', '.join('?' for _ in columns)})",
        tuple(columns.values()),
    )
    return cursor.lastrowid


def save_draft(
    conn: sqlite3.Connection, tenant: Tenant, content: dict, author: str, note: str, now: datetime
) -> tuple[dict | None, dict[str, str]]:
    """Save the owner's draft. Nothing goes live here. With errors, nothing is saved."""
    clean, errors = validate(tenant, content)
    if errors:
        return None, errors
    note = " ".join(note.split())[:200]
    with tx(conn):
        draft = open_draft(conn, tenant.slug)
        if draft and draft["status"] == "checking":
            return None, {"content": "A check is running on the last draft. Wait for it to finish."}
        base = live(conn, tenant.slug)
        if draft:
            conn.execute(
                "UPDATE rule_versions SET content_json = ?, content_hash = ?, author = ?, note = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    json.dumps(clean, sort_keys=True),
                    content_hash(clean),
                    author,
                    note,
                    iso(now),
                    draft["id"],
                ),
            )
            version_id = draft["id"]
        else:
            version_id = _insert(
                conn, tenant.slug, clean, "draft", author, note, now,
                parent_id=base["id"] if base else None,
            )
    return get(conn, version_id), {}


def request_publish(conn: sqlite3.Connection, slug: str, author: str, now: datetime) -> dict:
    """Send the draft to the eval gate. It goes live only if the gate passes (record_gate)."""
    with tx(conn):
        draft = open_draft(conn, slug)
        if not draft or draft["status"] != "draft":
            raise RulesError("There is no draft to publish.")
        current = live(conn, slug)
        if content_hash(draft["content"]) == content_hash(current["content"] if current else {}):
            raise RulesError("The draft is the same as what's live.")
        conn.execute(
            "UPDATE rule_versions SET status = 'checking', published_by = ?, updated_at = ? "
            "WHERE id = ?",
            (author, iso(now), draft["id"]),
        )
    return get(conn, draft["id"])


def gate_passed(result: dict) -> bool:
    """The gate's own reading of a run: at least one scenario, and no must-pass failure and no
    error among them. A run with no verdict never opens the gate."""
    return result.get("scenarios", 0) > 0 and not result.get("failed") and not result.get("errors")


def record_gate(conn: sqlite3.Connection, version_id: int, result: dict, now: datetime) -> dict:
    """Store the gate's result on the version it checked. Passed, it goes live; otherwise it is
    blocked and what's live stays as it was. The result must be for this exact content."""
    with tx(conn):
        row = get(conn, version_id)
        if row is None or row["status"] != "checking":
            raise RulesError("That version isn't waiting for a check.")
        if result.get("content_hash") != row["content_hash"]:
            raise RulesError("That check ran on different content.")
        passed = gate_passed(result)
        conn.execute(
            "UPDATE rule_versions SET status = ?, gate_json = ?, gate_at = ?, published_at = ?, "
            "updated_at = ? WHERE id = ?",
            (
                "published" if passed else "blocked",
                json.dumps({**result, "passed": passed}, sort_keys=True),
                iso(now),
                iso(now) if passed else None,
                iso(now),
                version_id,
            ),
        )
    return get(conn, version_id)


def discard_draft(conn: sqlite3.Connection, slug: str, now: datetime) -> None:
    with tx(conn):
        conn.execute(
            "UPDATE rule_versions SET status = 'discarded', updated_at = ? WHERE tenant = ? AND "
            "status = 'draft'",
            (iso(now), slug),
        )


def rollback(conn: sqlite3.Connection, slug: str, number: int, author: str, now: datetime) -> dict:
    """Make an earlier version live again, as a new version that copies it. Number 0 is the
    shipped defaults. Only a version that went live before can come back, so it passed the gate."""
    with tx(conn):
        if number == 0:
            content, gate = {}, {"passed": True, "defaults": True}
        else:
            target = _row(
                conn.execute(
                    "SELECT * FROM rule_versions WHERE tenant = ? AND number = ? AND "
                    "status = 'published'",
                    (slug, number),
                ).fetchone()
            )
            if target is None:
                raise RulesError("Only a version that was live can be brought back.")
            content, gate = target["content"], target["gate"]
        version_id = _insert(
            conn, slug, content, "published", author, f"Back to version {number}", now,
            rollback_of=number, gate_json=json.dumps(gate, sort_keys=True), gate_at=iso(now),
            published_at=iso(now), published_by=author,
        )
    return get(conn, version_id)


def install_for_eval(conn: sqlite3.Connection, slug: str, content: dict, now: datetime) -> None:
    """Make content live on an eval scratch database, so a gate run serves the draft through the
    same path a real call takes. No route calls this; it is for evals/gate.py and tests."""
    with tx(conn):
        _insert(
            conn, slug, content, "published", "eval gate", "draft under test", now,
            published_at=iso(now),
        )


# ---- What a call runs under ---------------------------------------------------------------------


def applied(tenant: Tenant, content: dict | None) -> dict:
    """Content as calls use it: validated again, so a row edited outside this module can still only
    change the editable lines and guidance, and a line that would fail to fill is dropped."""
    clean, _errors = validate(tenant, content or {})
    lines = {
        k: v for k, v in clean["lines"].items()
        if _fields(v) == _fields(default_line(tenant, k))
    }
    return {"lines": lines, "guidance": clean["guidance"]}


def start_call(conn: sqlite3.Connection, tenant: Tenant, call_id: str, now: datetime) -> dict:
    """Pin the live version to a call at its start, so an edit published mid-call never changes
    what that call hears, and the call's record says which version it ran under. Returns what the
    voice layer needs: the version, the guidance by stage and the spoken lines it overrides."""
    with tx(conn):
        current = live(conn, tenant.slug)
        conn.execute(
            "INSERT OR IGNORE INTO call_stages (call_id, stage, version_id, at) VALUES (?, ?, ?, ?)",
            (call_id, "opening", current["id"] if current else None, iso(now)),
        )
    found = for_call(conn, tenant, call_id)
    voice = {k: v for k, v in found["lines"].items() if EDITABLE_LINES[k][0] == "voice"}
    return {"version": found["number"], "guidance": found["guidance"], "lines": voice}


def for_call(conn: sqlite3.Connection, tenant: Tenant, call_id: str) -> dict:
    """The version a call started under, applied. Number None means the shipped defaults."""
    row = conn.execute(
        "SELECT v.number, v.content_json FROM call_stages s LEFT JOIN rule_versions v "
        "ON v.id = s.version_id WHERE s.call_id = ? AND s.stage = 'opening'",
        (call_id,),
    ).fetchone()
    content = json.loads(row["content_json"]) if row and row["content_json"] else {}
    return {"number": row["number"] if row else None, **applied(tenant, content)}


def tenant_for_call(conn: sqlite3.Connection, tenant: Tenant, call_id: str) -> Tenant:
    """The tenant with the call's version of the server's own lines (anything-else, and so on)."""
    lines = for_call(conn, tenant, call_id)["lines"]
    return tenant.with_lines({k: v for k, v in lines.items() if EDITABLE_LINES[k][0] == "lines"})


def tenant_live(conn: sqlite3.Connection, tenant: Tenant) -> Tenant:
    """The tenant with the live version's server lines, for a call the system is about to place."""
    current = live(conn, tenant.slug)
    lines = applied(tenant, current["content"] if current else {})["lines"]
    return tenant.with_lines({k: v for k, v in lines.items() if EDITABLE_LINES[k][0] == "lines"})


def stage_now(conn: sqlite3.Connection, call: sqlite3.Row) -> str:
    """The stage the server's records put the call in. Booking starts when the agent looked for
    openings or read the address back; closing once a booking, a change or a lead is done, a
    person is flagged, or the agent asked to end. The model never says which stage it is in."""
    call_id = call["id"]

    def has(sql: str) -> bool:
        return bool(conn.execute(sql, (call_id,)).fetchone())

    if call["end_requested"] or has(
        "SELECT 1 FROM bookings WHERE call_id = ? AND status IN ('booked', 'queued', 'done')"
    ) or has("SELECT 1 FROM leads WHERE call_id = ?") or has(
        "SELECT 1 FROM flags WHERE call_id = ? AND kind IN ('person', 'callback_requested')"
    ):
        return "closing"
    if call["readback_asked_seq"] is not None or has(
        "SELECT 1 FROM tool_calls WHERE call_id = ? AND tool IN ('find_slots', 'read_back') AND ok = 1"
    ):
        return "booking"
    return "opening"


def advance_stage(conn: sqlite3.Connection, call: sqlite3.Row, now: datetime) -> str | None:
    """Record the stage the call has reached, if it is new. Returns it, or None. Stages only move
    forward: a call at closing never goes back to booking. Runs inside the caller's transaction."""
    stage = stage_now(conn, call)
    seen = {
        r["stage"]
        for r in conn.execute("SELECT stage FROM call_stages WHERE call_id = ?", (call["id"],))
    }
    if stage in seen or (stage == "booking" and "closing" in seen):
        return None
    version = conn.execute(
        "SELECT version_id FROM call_stages WHERE call_id = ? AND stage = 'opening'", (call["id"],)
    ).fetchone()
    conn.execute(
        "INSERT INTO call_stages (call_id, stage, version_id, at) VALUES (?, ?, ?, ?)",
        (call["id"], stage, version["version_id"] if version else None, iso(now)),
    )
    return stage
