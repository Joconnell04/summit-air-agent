"""The bench's Questions, Cost and System additions (app/bench.py). Questions lists each live-call
scenario and owner question from the interview walkthrough with the evidence the recorded calls
hold and the bench view that shows it. Cost is each call at the measured rates. System adds the
prompt files, the after-hours roster and the documented capacity limits to the settings page.
All read-only.
"""

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from . import bench_call, bench_data, cost, failure_grader, redact
from .db import iso, parse_iso
from .tenant import Tenant

ROOT = bench_data.ROOT
PROMPTS = ROOT / "prompts"
SCALING = ROOT / "docs" / "scaling.md"
COST_ROWS = 40
# The window and row cap for the Questions page's reads of per-line rows (ASSUMED: a week is what
# a walkthrough asks about, and 2,000 lines is about 200 calls).
WINDOW_DAYS = 7
REPLY_ROWS = 2000
# A booking that asked for no hurry: the shape of a maintenance visit, in any trade.
ROUTINE = "b.priority IN ('Low', 'Normal')"


def _one(conn: sqlite3.Connection, sql: str, params=()) -> int:
    return conn.execute(sql, params).fetchone()[0] or 0


def _latest(conn: sqlite3.Connection, condition: str, params=()) -> str | None:
    """The bench key of the newest call that meets `condition` (on calls `c`)."""
    row = conn.execute(
        f"SELECT c.id FROM calls c WHERE {condition} ORDER BY c.started_at DESC LIMIT 1", params
    ).fetchone()
    return redact.call_key(row[0]) if row else None


def _call_link(key: str | None, tab: str) -> str | None:
    return f"/bench/calls/{key}#{tab}" if key else None


def _safety(conn: sqlite3.Connection) -> str:
    took = [
        (parse_iso(r[1]) - parse_iso(r[0])).total_seconds()
        for r in conn.execute(
            "SELECT safety_signal_at, safety_lines_at FROM calls WHERE safety_lines_at IS NOT NULL "
            "AND safety_signal_at IS NOT NULL"
        )
    ]
    open_ = _one(conn, "SELECT count(*) FROM calls WHERE safety_signal_at IS NOT NULL "
                       "AND safety_lines_at IS NULL")
    if not took and not open_:
        return "No safety call on record"
    parts = [f"{len(took)} calls, lines in {median(took):.1f} s median, {max(took):.1f} s slowest"
             ] if took else []
    if open_:
        parts.append(f"{open_} never said")
    return "; ".join(parts)


def _since(now: datetime) -> str:
    return iso(now - timedelta(days=WINDOW_DAYS))


def _replies(conn: sqlite3.Connection, now: datetime) -> list[float]:
    """Reply times of agent lines on the last WINDOW_DAYS of calls, at most REPLY_ROWS lines,
    newest calls first: this runs on the live tool server, so a busy week can't make it slow."""
    out = []
    for (timings,) in conn.execute(
        "SELECT t.timings_json FROM calls c JOIN turns t ON t.call_id = c.id "
        "WHERE c.started_at >= ? AND t.speaker = 'agent' ORDER BY c.started_at DESC LIMIT ?",
        (_since(now), REPLY_ROWS),
    ):
        reply = json.loads(timings).get("reply_ms")
        if reply is not None:
            out.append(reply)
    return sorted(out)


def questions(conn: sqlite3.Connection, tenant: Tenant, now: datetime) -> list[dict]:
    """The walkthrough's scenarios and questions in four groups. Each item: a short name, the
    evidence from the recorded calls (counted now), and where on the bench it shows. The two
    reads that scan per-line rows (reply times, the booked-out sentence) cover the last
    WINDOW_DAYS only; the rest are counts over the small per-call tables."""
    count = lambda sql, *p: _one(conn, sql, p)
    routine = f"SELECT count(*) FROM bookings b WHERE b.kind = 'book' AND {ROUTINE}"
    vulnerable = "EXISTS (SELECT 1 FROM bookings b WHERE b.call_id = c.id AND json_extract(b.request_json, '$.priority_rules') LIKE '%vulnerable%')"
    ended = {r[0]: r[1] for r in conn.execute(
        "SELECT end_reason, count(*) FROM calls WHERE ended_at IS NOT NULL GROUP BY end_reason")}
    by_who: dict[str, int] = {}
    for reason, n in ended.items():
        who = bench_call.ENDED_BY.get(reason or "", ("Other", ""))[0]
        by_who[who] = by_who.get(who, 0) + n
    replies = _replies(conn, now)
    p50 = replies[len(replies) // 2] if replies else None
    p95 = replies[min(len(replies) - 1, int(0.95 * len(replies)))] if replies else None
    after = tenant.after_hours_cutoffs
    booked_out_line = tenant.line("fully_booked")
    booked_out = count(
        "SELECT count(*) FROM calls c JOIN tool_calls k ON k.call_id = c.id WHERE c.started_at >= ? "
        "AND k.tool = 'find_slots' AND k.say LIKE ?", _since(now), f"%{booked_out_line[:40]}%",
    ) if booked_out_line else 0
    runs = bench_data.runs()
    # The newest run big enough to say something: a one-scenario rerun isn't the suite's result.
    sized = [bench_data.run_summary(r) for r in runs[:40]]
    latest_run = next((r for r in sized if r["judged"] >= 10), sized[0] if sized else None)
    mismatch = count("SELECT count(*) FROM flags WHERE reason LIKE ?", "Booking doesn't match%")
    any_key = _latest(conn, "c.ended_at IS NOT NULL")
    retell_key = _latest(conn, "c.id LIKE 'retell_%'") or any_key

    def item(name, evidence, link, view):
        return {"name": name, "evidence": evidence, "link": link, "view": view}

    return [
        {"group": "Live calls", "items": [
            item("Member tune-up", f"{count(routine)} routine visits booked",
                 _call_link(_latest(conn, f"EXISTS (SELECT 1 FROM bookings b WHERE b.call_id = c.id AND {ROUTINE})"), "summary"),
                 "Summary"),
            item("No heat, elderly", f"{count(f'SELECT count(*) FROM calls c WHERE {vulnerable}')} calls raised for a person at risk",
                 _call_link(_latest(conn, vulnerable), "why"), "Why"),
            item("Hazard smell", _safety(conn), "/bench?outcome=safety", "Calls, Why"),
            item("Commercial caller", f"{count('SELECT count(*) FROM calls WHERE commercial = 1')} turned away, none booked"
                 if not count("SELECT count(*) FROM calls c WHERE c.commercial = 1 AND EXISTS (SELECT 1 FROM bookings b WHERE b.call_id = c.id AND b.status = 'booked')")
                 else "A commercial call has a booking", "/bench?signal=commercial", "Calls"),
            item("Curveball", "Search any call by id or phone", "/bench", "Transcript"),
        ]},
        {"group": "Owner", "items": [
            item("Wrong booking", f"{mismatch} read-back mismatches flagged", "/bench?signal=person", "Summary"),
            item("Wants a human", f"{count('SELECT count(*) FROM transfers') + count('SELECT count(*) FROM handoffs')} transfers or handoffs, "
                 f"{count('SELECT count(*) FROM flags')} person flags", "/bench?signal=transfer", "Summary, Why"),
            item("Hangs up on callers", ", ".join(f"{who} {n}" for who, n in sorted(by_who.items())) or "No ended calls",
                 "/bench?ended=caller", "Calls"),
            item("Older callers", f"{count('SELECT count(*) FROM pace_changes')} pace changes, "
                 f"{count('SELECT count(*) FROM caller_pace')} numbers remembered", _call_link(any_key, "summary"), "Summary"),
            item("Bad line, accents", "Retell's words beside the server's, talked-over marked",
                 _call_link(retell_key, "transcript"), "Transcript"),
            item("Spanish", "Not supported: English only, no language setting", None, "System"),
            item("Cost per call", f"${cost.VOICE_PER_MINUTE:.3f} a minute plus ${cost.MODEL_PER_CALL:.3f} a call, MEASURED",
                 "/bench/cost", "Cost"),
            item("2am Sunday", f"{count('SELECT count(*) FROM calls WHERE after_hours = 1')} after hours, "
                 f"{after.get('on_call_technicians', '?')} on call", "/bench?signal=after_hours", "Calls, System"),
            item("200 calls at once", "About 3 at once today; voice streams run out first", "/bench/settings#capacity", "System"),
            item("Booked out", f"{booked_out} searches found nothing open, last {WINDOW_DAYS} days", "/bench/schedule", "Why, Schedule"),
            item("Quotes a price", "No price tool; the reply filter and prompt hold it", "/bench/settings#prompt", "System"),
            item("Tech's job details", f"{count('SELECT count(*) FROM summaries')} summaries, {count('SELECT count(*) FROM notes')} notes",
                 _call_link(_latest(conn, "EXISTS (SELECT 1 FROM summaries s WHERE s.call_id = c.id)"), "summary"), "Summary"),
            item("Membership, records", f"{count('SELECT count(*) FROM call_customers')} customers matched, "
                 f"{count('SELECT count(*) FROM identity_checks')} identity checks",
                 _call_link(_latest(conn, "EXISTS (SELECT 1 FROM call_customers m WHERE m.call_id = c.id)"), "summary"), "Summary"),
        ]},
        {"group": "Skeptic", "items": [
            item("Why not a dispatcher", "docs/considerations.md", None, "Repo"),
            item("What was skipped", "docs/later.md", None, "Repo"),
            item("Not trusted yet", "docs/later.md", None, "Repo"),
        ]},
        {"group": "Engineering", "items": [
            item("Stack and latency", f"Reply {p50:,.0f} ms p50, {p95:,.0f} ms p95, {len(replies)} lines in {WINDOW_DAYS} days"
                 if replies else "No timed lines", _call_link(any_key, "timing"), "Timing, System"),
            item("Prompt structure", f"{len(prompt_files())} prompt files", "/bench/settings#prompt", "System"),
            item("Invented bookings", "Server gates; rule proof per call", _call_link(any_key, "steps"), "Steps"),
            item("Barge-in", "Talked-over lines per call; interruption knobs", _call_link(retell_key, "transcript"), "Transcript"),
            item("How it was tested", f"{len(runs)} eval runs" + (f", latest {latest_run['suite']} {latest_run['passed']}/{latest_run['judged']}" if latest_run else ""),
                 "/bench/evals", "Evals"),
            item("Two more weeks", "docs/later.md", None, "Repo"),
            item("Debug it live", "Steps, then the JSON log", _call_link(any_key, "steps"), "Steps"),
        ]},
    ]


def cost_rows(conn: sqlite3.Connection, tenants) -> dict:
    """The newest ended calls at the measured rates, and their averages."""
    rows = []
    for r in conn.execute(
        "SELECT c.id, c.tenant, c.started_at, c.ended_at, g.cost_usd AS grade, s.cost_usd AS summary "
        "FROM calls c LEFT JOIN grades g ON g.call_id = c.id LEFT JOIN summaries s ON s.call_id = c.id "
        "WHERE c.ended_at IS NOT NULL ORDER BY c.started_at DESC LIMIT ?", (COST_ROWS,)
    ):
        tenant = tenants.by_slug.get(r["tenant"]) or tenants.default
        minutes = (parse_iso(r["ended_at"]) - parse_iso(r["started_at"])).total_seconds() / 60
        voice = minutes * cost.VOICE_PER_MINUTE
        after = (r["grade"] or 0) + (r["summary"] or 0)
        rows.append({"id": r["id"], "key": redact.call_key(r["id"]),
                     "local": tenant.local(parse_iso(r["started_at"])), "minutes": minutes,
                     "voice": voice, "model": cost.MODEL_PER_CALL, "after": after,
                     "total": voice + cost.MODEL_PER_CALL + after})
    n = len(rows) or 1
    return {
        "rows": rows,
        "average": {k: sum(r[k] for r in rows) / n for k in ("minutes", "voice", "model", "after", "total")},
        "rates": [
            ("Voice, per minute", f"${cost.VOICE_PER_MINUTE:.3f}", "MEASURED", "docs/cost.md"),
            ("Models, per call", f"${cost.MODEL_PER_CALL:.3f}", "MEASURED", "docs/cost.md"),
            ("Grading, per call", f"${failure_grader.per_call_usd(conn):.3f}", "MEASURED"
             if _one(conn, "SELECT count(*) FROM grades WHERE cost_usd > 0") else "ASSUMED",
             "app/failure_grader.py"),
            ("Server and numbers, a month", f"${cost.FIXED_MONTHLY:.0f}", "ASSUMED", "app/cost.py"),
        ],
    }


def prompt_files() -> list[dict]:
    """The prompt files the agent is built from, with their headings."""
    out = []
    for path in sorted(PROMPTS.glob("*.md")):
        text = path.read_text()
        heads = [m.group(1).strip() for m in re.finditer(r"^#{2,3}\s+(.+)$", text, re.MULTILINE)]
        out.append({"name": path.name, "lines": text.count("\n") + 1, "heads": heads})
    return out


def capacity(path: Path = SCALING) -> list[dict]:
    """The rows of docs/scaling.md's "Every limit in the chain" table: part, limit, source."""
    if not path.exists():
        return []
    text = path.read_text()
    start = text.find("## Every limit in the chain")
    if start < 0:
        return []
    rows = []
    for line in text[start:].splitlines()[1:]:
        if line.startswith("## "):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or cells[0] in ("Part", "") or set(cells[0]) <= {"-"}:
            continue
        rows.append({"part": cells[0].strip("*"), "on": cells[1],
                     "limit": re.sub(r"\*\*", "", cells[2]), "source": cells[3]})
    return rows


def after_hours(tenant: Tenant) -> list[tuple[str, str]]:
    """The tenant's after-hours settings as label and value."""
    cfg = tenant.after_hours_cutoffs
    labels = {
        "on_call_technicians": "On-call techs",
        "on_call_job_minutes": "Minutes per dispatch",
        "on_call_confirms_within_minutes": "Confirms within, min",
        "tonight_below_f": "Dispatch tonight below, F",
        "morning_above_f": "Morning above, F",
    }
    return [(label, str(cfg[k])) for k, label in labels.items() if k in cfg]
