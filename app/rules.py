"""Owner edits to how the agent works (exploration 009): a few wording lines and short guidance per
call stage, stored as versions over the tenant's YAML defaults.

What an owner can change is a fixed list here, and every draft is checked against it in this
module, so no screen can reach a gate. The read-back, the safety script, priority, the service
area and every other rule stay in tenant files and server code, changed only by engineers. New or
changed text is read by the guidance check when a draft is saved (app/guidance_check.py), which
refuses only what contradicts a locked rule or tells the agent to skip one. A draft goes live only when the must-pass evals pass against it (evals/gate.py), and a rollback is a new
version that copies an older one, so nothing is ever deleted.

The server decides which stage a call is in, from its own records, never from the model.
"""

import hashlib
import json
import re
import sqlite3
import string
import unicodedata
from datetime import datetime, timedelta

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
# Stages the owner can add guidance to. Before hello has no model, and the technician summary after
# the call does not read owner guidance yet (docs/later.md).
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
    "read_back_for": (
        "The read-back is locked. It names the address as the caller's home, which is how every "
        "booking checks it isn't a business."
    ),
    "priority": "Priority is locked. It can only go up, and only engineers change it.",
    "safety": "The safety script is locked. It was signed off word for word.",
}
LOCKED_DEFAULT = "This is locked. Only engineers change it."

# Characters that could pass for prompt structure, a template field or a role label. Whether a
# note contradicts a locked rule is for the guidance check (app/guidance_check.py), not a word list.
NOT_PLAIN = re.compile(r"[{}\[\]#`<>\\|*_]")
# What a line's fields are filled with when it is checked, so a line that can't be said is caught
# here and not at hello or on a callback.
SAMPLE = {"first_name": "Maria"}

STATUSES = ("draft", "checking", "published", "blocked", "discarded")
# A check that hasn't reported back in this long is taken as dead and the draft returned to the
# owner, so a crashed gate process can't leave it stuck. A full gate run takes a few minutes.
CHECK_TIMEOUT_MINUTES = 60


class RulesError(ValueError):
    pass


def default_line(tenant: Tenant, key: str) -> str:
    where = EDITABLE_LINES[key][0]
    source = tenant.lines if where == "lines" else tenant.agent["voice"]
    return " ".join(source[key].split())


def _ascii(text: str) -> str | None:
    """Text folded with NFKC and whitespace collapsed, or None if anything is left that isn't
    printable ASCII: a zero-width space, a look-alike letter from another alphabet, an emoji."""
    folded = " ".join(unicodedata.normalize("NFKC", str(text or "")).split())
    return folded if all(" " <= c <= "~" for c in folded) else None


def _fields(text: str) -> set[str] | None:
    """The {name} fields in a line, or None unless every one is a bare {name}: no format spec, no
    conversion, no index, since those can fail, change or blow up what is said."""
    found = set()
    try:
        for _literal, name, spec, conversion in string.Formatter().parse(text):
            if name is None:
                continue
            if spec or conversion or not name.isidentifier():
                return None
            found.add(name)
    except ValueError:
        return None
    return found


def _renders(text: str) -> bool:
    """The line fills with sample values to something of a sane length."""
    fields = _fields(text)
    if fields is None:
        return False
    try:
        said = text.format(**{f: SAMPLE.get(f, "Maria") for f in fields})
    except (KeyError, IndexError, ValueError):
        return False
    return len(said) <= LINE_MAX_CHARS + 50


def _disclosure(tenant: Tenant) -> tuple[str, ...]:
    return tuple(tenant.agent.get("owner_rules", {}).get("disclosure", ()))


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
    for key, given in lines.items():
        field = f"lines.{key}"
        if key not in EDITABLE_LINES:
            errors[field] = LOCKED.get(key, LOCKED_DEFAULT)
            continue
        default = default_line(tenant, key)
        text = _ascii(given)
        if text is None:
            errors[field] = "Plain letters only. Retype it without special characters."
            continue
        if not text or text == default:
            continue
        wanted = _fields(default)
        keep = ", ".join("{" + f + "}" for f in sorted(wanted))
        dropped = next(
            (p for p in _disclosure(tenant)
             if has_phrase(words(default), p) and not has_phrase(words(text), p)),
            None,
        )
        if len(text) > LINE_MAX_CHARS:
            errors[field] = f"Keep it under {LINE_MAX_CHARS} characters."
        elif _fields(text) != wanted or not _renders(text):
            errors[field] = f"Keep {keep} in the line, and no other braces." if wanted else (
                "No braces in this line."
            )
        elif dropped:
            errors[field] = f"Keep \"{dropped}\" in the line, so callers know it's an AI."
        else:
            clean["lines"][key] = text

    guidance = content.get("guidance") or {}
    if not isinstance(guidance, dict):
        guidance, errors["guidance"] = {}, "Not a list of guidance."
    for stage, items in guidance.items():
        if stage not in GUIDANCE_STAGES:
            errors[f"guidance.{stage}"] = "Nothing can be added at this stage."
            continue
        if not isinstance(items, list):
            errors[f"guidance.{stage}"] = "Not a list of notes."
            continue
        kept = []
        for n, given in enumerate(i for i in items if str(i or "").strip()):
            field = f"guidance.{stage}.{n}"
            text = _ascii(given)
            if text is None:
                errors[field] = "Plain letters only. Retype it without special characters."
            elif len(text) > GUIDANCE_MAX_CHARS:
                errors[field] = f"Keep it under {GUIDANCE_MAX_CHARS} characters."
            elif NOT_PLAIN.search(text):
                errors[field] = "Plain words only, no symbols like braces, brackets or #."
            kept.append(text or "")
        if len(kept) > GUIDANCE_PER_STAGE:
            errors[f"guidance.{stage}"] = f"At most {GUIDANCE_PER_STAGE} per stage."
        if kept:
            clean["guidance"][stage] = kept[:GUIDANCE_PER_STAGE]
    return clean, errors


def content_hash(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]


def _json(text: str | None, empty):
    """A stored JSON value, or `empty` if it is missing or unreadable: a damaged row reads as the
    shipped defaults rather than failing every call."""
    try:
        value = json.loads(text) if text else empty
    except ValueError:
        return empty
    return value if isinstance(value, type(empty)) else empty


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    found = dict(row)
    found["content"] = _json(found.pop("content_json"), {})
    found["gate"] = _json(found.pop("gate_json"), {}) or None
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


def describe(tenant: Tenant, content: dict, base: dict | None) -> str:
    """What a draft changes from the live version, in a few words, for the version's note when
    none is given: "Added 1 rule; reworded Greeting"."""
    after, before = applied(tenant, content), applied(tenant, base)
    reworded = [
        label for key, (_where, _stage, label) in EDITABLE_LINES.items()
        if after["lines"].get(key) != before["lines"].get(key)
    ]
    new = {(s, t) for s, items in after["guidance"].items() for t in items}
    old = {(s, t) for s, items in before["guidance"].items() for t in items}

    def rule(n: int) -> str:
        return f"{n} rule" + ("" if n == 1 else "s")

    parts = (
        ([f"Added {rule(len(new - old))}"] if new - old else [])
        + ([f"Removed {rule(len(old - new))}"] if old - new else [])
        + ([f"Reworded {', '.join(reworded)}"] if reworded else [])
    )
    return "; ".join(parts) or "No change"


def changed(tenant: Tenant, clean: dict, base: dict | None) -> dict[str, str]:
    """The lines and notes in a clean draft that the live version doesn't have, by field: what the
    guidance check reads. A note already live was checked when it was saved."""
    before = applied(tenant, base)
    found = {f"lines.{k}": v for k, v in clean["lines"].items() if before["lines"].get(k) != v}
    for stage, items in clean["guidance"].items():
        found.update({
            f"guidance.{stage}.{n}": text for n, text in enumerate(items)
            if text not in before["guidance"].get(stage, [])
        })
    return found


def save_draft(
    conn: sqlite3.Connection, tenant: Tenant, content: dict, author: str, note: str, now: datetime,
    check,
) -> tuple[dict | None, dict[str, str]]:
    """Save the owner's draft. Nothing goes live here. With errors, nothing is saved. `check` is
    the guidance check (app/guidance_check.py), given the new and changed lines and notes; it
    returns what's wrong by field, and an empty answer is the only way past it."""
    clean, errors = validate(tenant, content)
    if errors:
        return None, errors
    current = live(conn, tenant.slug)
    new = changed(tenant, clean, current["content"] if current else None)
    if new:
        errors = check(tenant, new)
        if errors:
            return None, errors
    note = " ".join(note.split())[:200]
    expire_checks(conn, tenant.slug, now)
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
        # What was live when the check started: if it changes before the result comes back (a
        # rollback), the result must not publish over it.
        conn.execute(
            "UPDATE rule_versions SET status = 'checking', published_by = ?, base_live = ?, "
            "updated_at = ? WHERE id = ?",
            (author, current["number"] if current else 0, iso(now), draft["id"]),
        )
    return get(conn, draft["id"])


OVERRIDE_MAX_CHARS = 200


def publish_now(
    conn: sqlite3.Connection, slug: str, author: str, reason: str, now: datetime, record=None
) -> dict:
    """Publish the draft at once, without the eval gate (DECISIONS.md 055). The guidance check
    already ran when the draft was saved. The version records that the check was skipped, by
    whom, when and why, so the history shows it plainly; a reason is required. `record(conn,
    version)` runs inside the same transaction, so if writing the log fails nothing is
    published."""
    reason = " ".join(reason.split())[:OVERRIDE_MAX_CHARS]
    if not reason:
        raise RulesError("Say why the check is being skipped.")
    with tx(conn):
        draft = open_draft(conn, slug)
        if draft and draft["status"] == "checking":
            raise RulesError("A check is running on this draft. Stop it first.")
        if not draft:
            raise RulesError("There is no draft to publish.")
        current = live(conn, slug)
        if content_hash(draft["content"]) == content_hash(current["content"] if current else {}):
            raise RulesError("The draft is the same as what's live.")
        override = {"passed": False, "overridden": True, "by": author, "at": iso(now),
                    "reason": reason}
        conn.execute(
            "UPDATE rule_versions SET status = 'published', published_by = ?, base_live = ?, "
            "gate_json = ?, gate_at = ?, published_at = ?, updated_at = ? WHERE id = ?",
            (author, current["number"] if current else 0, json.dumps(override, sort_keys=True),
             iso(now), iso(now), iso(now), draft["id"]),
        )
        if record is not None:
            record(conn, get(conn, draft["id"]))
    return get(conn, draft["id"])


def gate_passed(result: dict, expected: int) -> bool:
    """The gate's own reading of a run: every one of the `expected` must-pass scenarios ran, and the
    result lists its failures and errors, and both are empty. A partial or malformed result, or a
    run with no verdict, never opens the gate."""
    failed, errors = result.get("failed"), result.get("errors")
    return (
        expected > 0
        and result.get("scenarios") == expected
        and isinstance(failed, list)
        and isinstance(errors, list)
        and not failed
        and not errors
    )


def record_gate(
    conn: sqlite3.Connection, version_id: int, result: dict, now: datetime, expected: int
) -> dict:
    """Store the gate's result on the version it checked. Passed, it goes live; otherwise it is
    blocked and what's live stays as it was. The result must be for this exact content, and it
    never publishes over a version made live while it ran (a rollback): that draft is blocked as
    stale, and the owner publishes it again if they still want it."""
    with tx(conn):
        row = get(conn, version_id)
        if row is None or row["status"] != "checking":
            raise RulesError("That version isn't waiting for a check.")
        if result.get("content_hash") != row["content_hash"]:
            raise RulesError("That check ran on different content.")
        current = live(conn, row["tenant"])
        stale = (current["number"] if current else 0) != row["base_live"]
        passed = gate_passed(result, expected) and not stale
        conn.execute(
            "UPDATE rule_versions SET status = ?, gate_json = ?, gate_at = ?, published_at = ?, "
            "updated_at = ? WHERE id = ?",
            (
                "published" if passed else "blocked",
                json.dumps({**result, "passed": passed, "stale": stale}, sort_keys=True),
                iso(now),
                iso(now) if passed else None,
                iso(now),
                version_id,
            ),
        )
    return get(conn, version_id)


def cancel_check(conn: sqlite3.Connection, slug: str, now: datetime) -> dict:
    """Stop waiting for a check and return the draft to the owner. A result that arrives later is
    refused by record_gate, since the version is no longer checking."""
    with tx(conn):
        draft = open_draft(conn, slug)
        if not draft or draft["status"] != "checking":
            raise RulesError("No check is running.")
        conn.execute(
            "UPDATE rule_versions SET status = 'draft', updated_at = ? WHERE id = ?",
            (iso(now), draft["id"]),
        )
    return get(conn, draft["id"])


def expire_checks(conn: sqlite3.Connection, slug: str, now: datetime) -> None:
    """Return a check older than CHECK_TIMEOUT_MINUTES to draft: its process has died."""
    cutoff = iso(now - timedelta(minutes=CHECK_TIMEOUT_MINUTES))
    with tx(conn):
        conn.execute(
            "UPDATE rule_versions SET status = 'draft', updated_at = ? WHERE tenant = ? AND "
            "status = 'checking' AND updated_at < ?",
            (iso(now), slug, cutoff),
        )


def discard_draft(conn: sqlite3.Connection, slug: str, now: datetime) -> None:
    with tx(conn):
        conn.execute(
            "UPDATE rule_versions SET status = 'discarded', updated_at = ? WHERE tenant = ? AND "
            "status = 'draft'",
            (iso(now), slug),
        )


def rollback(conn: sqlite3.Connection, slug: str, number: int, author: str, now: datetime) -> dict:
    """Make an earlier version live again, as a new version that copies it. Number 0 is the
    shipped defaults. Only a version that went live before can come back: it passed the gate, or
    was published without it, and the copy keeps that record."""
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
    try:
        clean, _errors = validate(tenant, content or {})
    except (TypeError, ValueError, AttributeError, KeyError):
        return {"lines": {}, "guidance": {}}
    lines = {
        k: v for k, v in clean["lines"].items()
        if _fields(v) == _fields(default_line(tenant, k)) and _renders(v)
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
    content = _json(row["content_json"] if row else None, {})
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


def note_due(conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row) -> tuple[str, list[str]]:
    """The owner's guidance for the stage the call is in now, the first time it is due: the call
    has reached booking or closing, has guidance for it, and is not a safety call (the caller
    passes that in by calling only outside one). Marked sent, so it goes once; a stage reached
    during a safety call gets its note on the next caller line after the safety state clears.
    Returns ("", []) when nothing is due."""
    row = conn.execute(
        "SELECT stage, noted FROM call_stages WHERE call_id = ? AND stage IN ('booking', 'closing') "
        "ORDER BY CASE stage WHEN 'closing' THEN 0 ELSE 1 END LIMIT 1",
        (call["id"],),
    ).fetchone()
    if row is None or row["noted"]:
        return "", []
    items = for_call(conn, tenant, call["id"])["guidance"].get(row["stage"], [])
    if not items:
        return "", []
    conn.execute(
        "UPDATE call_stages SET noted = 1 WHERE call_id = ? AND stage = ?",
        (call["id"], row["stage"]),
    )
    return row["stage"], items
