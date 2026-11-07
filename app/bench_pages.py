"""The bench's Questions, Cost and System additions (app/bench.py). Questions is the owner's likely
questions on the walkthrough, each with a one-line answer and the evidence the recorded calls hold
for it. Cost is each call at the measured rates and the stacks we tested, side by side. System
adds the after-hours roster. All read-only.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from statistics import median

from . import bench_call, bench_data, cost, failure_grader, redact, tools
from .db import iso, parse_iso
from .tenant import Tenant

ROOT = bench_data.ROOT
COST_ROWS = 40
# The calls the "most at once" count looks over (ASSUMED: a few weeks of calls).
CALL_ROWS = 5000
TOOL_NAMES = tuple(tools.TOOLS)
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


def _most_at_once(conn: sqlite3.Connection) -> int:
    """The most calls that were open at the same moment, over the newest CALL_ROWS calls."""
    edges = []
    for start, end in conn.execute(
        "SELECT started_at, coalesce(ended_at, started_at) FROM calls ORDER BY started_at DESC "
        "LIMIT ?", (CALL_ROWS,)
    ):
        edges += [(start, 1), (end, -1)]
    best = now = 0
    for _, step in sorted(edges, key=lambda e: (e[0], e[1])):
        now += step
        best = max(best, now)
    return best


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else ('es' if word.endswith('ch') else 's')}"


def questions(conn: sqlite3.Connection, tenant: Tenant, now: datetime) -> list[dict]:
    """The questions the owner is likely to ask on the walkthrough, each with a one-line answer, the
    evidence the recorded calls hold for it (counted now) and the bench view that shows it. The
    two reads that scan per-line rows (reply times, the booked-out sentence) cover the last
    WINDOW_DAYS only; the rest are counts over the small per-call tables."""
    count = lambda sql, *p: _one(conn, sql, p)
    routine = f"SELECT count(*) FROM bookings b WHERE b.kind = 'book' AND {ROUTINE}"
    vulnerable = ("EXISTS (SELECT 1 FROM bookings b WHERE b.call_id = c.id AND "
                  "json_extract(b.request_json, '$.priority_rules') LIKE '%vulnerable%')")
    ended = {r[0]: r[1] for r in conn.execute(
        "SELECT end_reason, count(*) FROM calls WHERE ended_at IS NOT NULL GROUP BY end_reason")}
    by_who: dict[str, int] = {}
    for reason, n in ended.items():
        who = bench_call.ENDED_BY.get(reason or "", ("Other", ""))[0]
        by_who[who] = by_who.get(who, 0) + n
    replies = _replies(conn, now)
    p50 = replies[len(replies) // 2] if replies else None
    p95 = replies[min(len(replies) - 1, int(0.95 * len(replies)))] if replies else None
    booked_out_line = tenant.line("fully_booked")
    booked_out = count(
        "SELECT count(*) FROM calls c JOIN tool_calls k ON k.call_id = c.id WHERE c.started_at >= ? "
        "AND k.tool = 'find_slots' AND k.say LIKE ?", _since(now), f"%{booked_out_line[:40]}%",
    ) if booked_out_line else 0
    runs = bench_data.runs()
    sized = [bench_data.run_summary(r) for r in runs[:40]]
    latest_run = next((r for r in sized if r["judged"] >= 10), sized[0] if sized else None)
    floor = (bench_data.baseline() or {}).get("min_eval_pass_rate")
    mismatch = count("SELECT count(*) FROM flags WHERE reason LIKE ?", "Booking doesn't match%")
    confirmed = count("SELECT count(*) FROM calls WHERE readback_confirmed_at IS NOT NULL")
    held = count("SELECT count(*) FROM tool_calls WHERE ok = 0 AND gate IS NOT NULL")
    commercial = count("SELECT count(*) FROM calls WHERE commercial = 1")
    commercial_booked = count(
        "SELECT count(*) FROM calls c WHERE c.commercial = 1 AND EXISTS (SELECT 1 FROM bookings b "
        "WHERE b.call_id = c.id AND b.status IN ('booked', 'done'))")
    members = count("SELECT count(DISTINCT m.call_id) FROM call_customers m")
    people = count("SELECT count(*) FROM transfers") + count("SELECT count(*) FROM handoffs")
    callbacks = count("SELECT count(*) FROM callbacks")
    after = count("SELECT count(*) FROM calls WHERE after_hours = 1")
    dispatched = count("SELECT count(*) FROM dispatches WHERE replaced_at IS NULL")
    average = cost_rows(conn, None, tenant)["average"]["total"]
    any_key = _latest(conn, "c.ended_at IS NOT NULL")
    retell_key = _latest(conn, "c.id LIKE 'retell_%'") or any_key
    after_cfg = tenant.after_hours_cutoffs

    def item(q, answer, evidence, link, view):
        return {"q": q, "answer": answer, "evidence": evidence, "link": link, "view": view}

    return [
        {"group": "Calls like mine", "items": [
            item("A member wants a tune-up?",
                 "Found by their number, thanked as a member, booked as a routine visit.",
                 f"{count(routine)} routine visits booked; {members} calls matched to a customer",
                 _call_link(_latest(conn, f"EXISTS (SELECT 1 FROM bookings b WHERE b.call_id = c.id AND {ROUTINE})"), "summary"),
                 "Summary"),
            item("No heat, elderly parent at home?",
                 "Priority goes up and the reason is written on the job.",
                 f"{count(f'SELECT count(*) FROM calls c WHERE {vulnerable}')} calls raised for a person at risk",
                 _call_link(_latest(conn, vulnerable), "why"), "Why"),
            item("A caller reports a hazard?",
                 "The server says the fixed safety lines first, before anything is booked.",
                 _safety(conn), "/bench?outcome=safety", "Calls"),
            item("A business calls?", "Homes only: the server won't book a commercial caller.",
                 f"{commercial} turned away, {commercial_booked} booked", "/bench?signal=commercial", "Calls"),
            item("2am on a Sunday, freezing?",
                 "After hours, no heat in the cold sends the on-call tech now; the rest waits for morning.",
                 f"{_plural(after, 'after-hours call')}, {_plural(dispatched, 'dispatch')}, "
                 f"{after_cfg.get('on_call_technicians', '?')} on call",
                 "/bench?signal=after_hours", "Calls"),
            item("Every tech is booked?", "It says so, offers the next opening, and can flag a callback.",
                 f"{booked_out} searches found nothing open in {WINDOW_DAYS} days", "/bench/schedule",
                 "Schedule"),
        ]},
        {"group": "Can I trust it", "items": [
            item("Wrong address or time?",
                 "It reads both back and books only after a yes; a mismatch is flagged.",
                 f"{confirmed} read-backs confirmed, {mismatch} mismatches flagged",
                 _call_link(any_key, "summary"), "Summary"),
            item("It makes up a booking?",
                 "The server books only a window it offered, after a confirmed read-back.",
                 f"{held} requests held back by a server rule", _call_link(any_key, "why"), "Why"),
            item("The caller wants a person?", "It puts them through, or leaves a callback with the reason.",
                 f"{_plural(people, 'transfer')}, {_plural(callbacks, 'callback')}",
                 "/bench?signal=transfer", "Summary"),
            item("Does it hang up on people?", "It ends only after a goodbye or a long silence.",
                 ", ".join(f"{who} {n}" for who, n in sorted(by_who.items())) or "No ended calls",
                 "/bench?ended=agent", "Calls"),
            item("A bad line or an accent?",
                 "Accurate transcription with noise cancellation; the transcript shows what Retell heard.",
                 "Retell's words beside the server's, talked-over lines marked",
                 _call_link(retell_key, "transcript"), "Transcript"),
            item("Spanish?", "English only for now; Spanish is planned after V1.", "docs/later.md", None, ""),
        ]},
        {"group": "Running it", "items": [
            item("What does a call cost?", f"About ${average:.2f} a call at today's rates.",
                 f"${cost.VOICE_PER_MINUTE:.3f} a minute for voice, ${cost.MODEL_PER_CALL:.3f} a call for models",
                 "/bench/cost", "Cost"),
            item("Will it quote a price?", "No. It has no price tool; price questions go to a callback.",
                 f"{len(TOOL_NAMES)} tools, none for prices", "/bench/rules", "Rules"),
            item("What does my tech see?", "A summary on the job in ServiceTitan, written after the call.",
                 f"{count('SELECT count(*) FROM summaries')} summaries, {count('SELECT count(*) FROM notes')} notes",
                 _call_link(_latest(conn, "EXISTS (SELECT 1 FROM summaries s WHERE s.call_id = c.id)"), "summary"),
                 "Summary"),
            item("A heat-wave rush?", "Retell takes the phone side; each call is a few short requests to our server.",
                 f"Most calls at once on record: {_most_at_once(conn)}", "/bench/settings", "System"),
            item("How fast does it answer?",
                 f"About {p50 / 1000:.1f} s to the first word." if replies else "Not measured on these calls.",
                 f"{p50:,.0f} ms typical, {p95:,.0f} ms slowest 5%, {len(replies)} lines in {WINDOW_DAYS} days"
                 if replies else "No timed lines", _call_link(any_key, "timing"), "Timing"),
            item("How was it tested?",
                 f"Simulated callers on every change; the suite must stay above {floor:.0%}."
                 if floor else "Simulated callers on every change.",
                 f"{len(runs)} eval runs" + (f", latest {latest_run['passed']} of {latest_run['judged']}" if latest_run else ""),
                 "/bench/evals", "Evals"),
            item("Can you debug a call live?", "Every step, prompt and tool result is on the call's Steps tab.",
                 "Steps, then the log", _call_link(any_key, "steps"), "Steps"),
        ]},
    ]


# The stacks and reply models we tested, for the Cost page's chart: each measure in seconds or
# dollars, with where it was measured. Rig rows are 12 simulated calls per stack on the audio rig,
# timed from the end of the caller's speech to the agent's first sound; the live row is 12 phone
# calls; model rows are the text harness's first word on the standard suite.
_RIG = "evals/rounds/bakeoff-audio-01.md, audio rig, 12 calls"
_FLOW = "evals/rounds/retell-flow-01.md, audio rig, 20 calls"
_LIVE = "evals/rounds/socket-buildout-01.md, 12 phone calls, caller's last word to Brian's first"
_MODELS = "evals/rounds/model-matrix-01.md, standard suite, text harness"
MEASURES = (
    ("wait", "Typical wait", "s"),
    ("slow", "Slowest 10%", "s"),
    ("lookup", "With a lookup", "s"),
    ("cost", "Cost per call", "$"),
)
STACKS = (
    {"group": "Voice stacks", "name": "Retell + our server (live)", "live": True,
     "wait": (1.83, _LIVE), "slow": (2.92, _LIVE),
     "cost": (2.39 / 12, "socket-buildout-01.md, Retell's bill for the socket leg over 12 calls")},
    {"group": "Voice stacks", "name": "Retell, one prompt",
     "wait": (1.57, _RIG), "slow": (2.60, _RIG), "lookup": (2.27, _RIG), "cost": (0.180, _RIG)},
    {"group": "Voice stacks", "name": "Retell flow",
     "wait": (2.63, _FLOW), "slow": (3.60, _FLOW), "lookup": (3.09, _FLOW), "cost": (0.150, _FLOW)},
    {"group": "Voice stacks", "name": "LiveKit, our own",
     "wait": (1.89, _RIG), "slow": (2.49, _RIG), "lookup": (1.96, _RIG), "cost": (0.044, _RIG)},
    {"group": "Voice stacks", "name": "ElevenLabs on our model",
     "wait": (1.45, _RIG), "slow": (2.06, _RIG), "lookup": (2.00, _RIG), "cost": (0.151, _RIG)},
    {"group": "Voice stacks", "name": "OpenAI Realtime",
     "wait": (2.47, _RIG), "slow": (3.38, _RIG), "lookup": (2.06, _RIG), "cost": (0.208, _RIG)},
    {"group": "Voice stacks", "name": "Gemini Live",
     "wait": (1.29, _RIG), "slow": (2.50, _RIG), "lookup": (1.94, _RIG), "cost": (0.109, _RIG)},
    {"group": "Reply models", "name": "Claude Haiku 5.5 (live)", "live": True,
     "wait": (0.568, _MODELS), "slow": (0.722, _MODELS), "lookup": (1.288, _MODELS),
     "cost": (0.0027, _MODELS)},
    {"group": "Reply models", "name": "Claude Sonnet 5.5",
     "wait": (1.591, _MODELS), "slow": (2.324, _MODELS), "lookup": (1.987, _MODELS),
     "cost": (0.0705, _MODELS)},
)


def stack_chart() -> dict:
    """The chart's data as the page's script reads it: the measures, and each stack's values with
    their sources. A measure a stack wasn't timed on is left out, not drawn as zero."""
    rows = []
    for stack in STACKS:
        values = {key: {"v": stack[key][0], "src": stack[key][1]} for key, _, _ in MEASURES
                  if key in stack}
        rows.append({"group": stack["group"], "name": stack["name"],
                     "live": bool(stack.get("live")), "values": values})
    return {"measures": [{"key": k, "label": label, "unit": unit} for k, label, unit in MEASURES],
            "groups": list(dict.fromkeys(s["group"] for s in STACKS)), "rows": rows}

def cost_rows(conn: sqlite3.Connection, tenants, default: Tenant | None = None) -> dict:
    """The newest ended calls at the measured rates, and their averages. With no tenants, every
    call is read at `default`'s clock."""
    rows = []
    for r in conn.execute(
        "SELECT c.id, c.tenant, c.started_at, c.ended_at, g.cost_usd AS grade, s.cost_usd AS summary "
        "FROM calls c LEFT JOIN grades g ON g.call_id = c.id LEFT JOIN summaries s ON s.call_id = c.id "
        "WHERE c.ended_at IS NOT NULL ORDER BY c.started_at DESC LIMIT ?", (COST_ROWS,)
    ):
        tenant = (tenants.by_slug.get(r["tenant"]) or tenants.default) if tenants else default
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
