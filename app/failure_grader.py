"""The failure grader (issue #178, brief docs/ai-process/briefs/failure-grader.md): after a call has
ended, the offline.grader model profile (config/models.yaml) reads it and answers one question,
did a true failure happen? Only five things count (prompts/failure-grader.md): the caller hung up
on, a wrong or missing booking or callback that was promised, safety advice not spoken for a real
hazard, false or unsafe information, and records that contradict what the caller was told.

The model gives an answer; the server decides what it becomes. A failure is kept only when it names
one of the five kinds and every evidence line it quotes is found in the call's transcript or rows,
so a flag can always be traced to words in the call. A kept failure adds a flag of kind `grader`,
which shows wherever flags already show and is never emailed (jobs.alert_on_call).

Each call is graded once: the answer is stored in `grades` and a later run skips the call unless it
is forced. A run (`grader_runs`) covers one call or a time range and works on a background thread,
one run at a time, so the pages answer at once and show its progress. The daily run is started by
the jobs loop when the owner's schedule says it is due (`grader_schedule`).
"""

import json
import logging
import os
import re
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Protocol

import httpx
import yaml

from . import records
from .db import iso, parse_iso, tx
from .tenant import Tenant

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "offline.grader"
PROMPT = ROOT / "prompts" / "failure-grader.md"
# Our id for a call that came through Retell is this prefix and Retell's id (voice/retell/bridge.py).
RETELL_PREFIX = "retell_"
FLAG_KIND = "grader"
TIMEOUT_SECONDS = 180
MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"

KINDS = {
    "hung_up": "Cut off",
    "booking": "Booking wrong",
    "callback": "Callback wrong",
    "safety": "Safety advice missed",
    "false_info": "Told something wrong",
    "records": "Records don't match",
}
SEVERITY_PRIORITY = {"critical": "Urgent", "serious": "High"}
SCHEMA = {
    "type": "object",
    "properties": {
        "failure": {"type": "boolean"},
        "kind": {"type": "string", "enum": [*KINDS, "none"]},
        "severity": {"type": "string", "enum": [*SEVERITY_PRIORITY, "none"]},
        "reason": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["failure", "kind", "severity", "reason", "evidence"],
    "additionalProperties": False,
}
MAX_EVIDENCE = 3
REASON_MAX_CHARS = 240
EVIDENCE_MAX_CHARS = 300
# A tool result is cut to this many characters in the record; the say line and the refusal are what
# matter, and a whole slot list would only add tokens.
TOOL_RESULT_CHARS = 600

# What one call costs to grade when there is nothing measured yet. ASSUMED: about 15k input tokens
# and 3k output tokens with thinking, at the profile's $4 and $20 per million (config/models.yaml).
DEFAULT_CALL_USD = 0.12
# A range run asks before it starts when it would cost more than this or cover more calls.
CONFIRM_USD = 1.00
CONFIRM_CALLS = 20
MAX_CALLS_PER_RUN = 200
# The daily run, until the owner sets one: on, at 6 AM local time, for calls since the last one.
DEFAULT_SCHEDULE = {"enabled": True, "at": "06:00"}
DAILY_SPAN = timedelta(hours=24)


class GraderModel(Protocol):
    def grade(self, system: str, record: str) -> tuple[dict, float]:
        """The model's answer as JSON and what the call cost in USD."""
        ...


class Transcripts(Protocol):
    def get(self, retell_id: str) -> dict:
        """Retell's call object (GET /v2/get-call/<id>)."""
        ...


class AnthropicGraderModel:
    def __init__(self, cfg: dict, key_env: str, client=None):
        self.cfg = cfg
        if client is None:
            import anthropic

            client = anthropic.Anthropic(
                api_key=os.environ.get(key_env), timeout=TIMEOUT_SECONDS, max_retries=2
            )
        self.client = client

    def grade(self, system: str, record: str) -> tuple[dict, float]:
        off = {"type": self.cfg.get("thinking_off", "disabled")}
        thinking = {"type": "adaptive"} if self.cfg["thinking"] else off
        output = {"format": {"type": "json_schema", "schema": SCHEMA}}
        if self.cfg.get("effort"):
            output["effort"] = self.cfg["effort"]
        # A safety-classifier decline is retried on the API's default fallback model, so a call
        # about a gas leak is still graded (server-side fallback, beta).
        message = self.client.beta.messages.create(
            model=self.cfg["model"],
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": record}],
            thinking=thinking,
            output_config=output,
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        price, usage = self.cfg["price"], message.usage
        cost = (usage.input_tokens * price["input"] + usage.output_tokens * price["output"]) / 1e6
        if message.stop_reason != "end_turn":
            raise RuntimeError(f"the grader stopped with {message.stop_reason}")
        text = "".join(b.text for b in message.content if b.type == "text")
        return json.loads(text), cost


class RetellTranscripts:
    """Reads one call from Retell. Only the versioned GET /v2/get-call/<id> is used (CLAUDE.md);
    shared server code doesn't import a voice layer, so this is its own small client."""

    BASE_URL = "https://api.retellai.com"

    def __init__(self, api_key: str, http: httpx.Client | None = None):
        self.http = http or httpx.Client(base_url=self.BASE_URL, timeout=30)
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def get(self, retell_id: str) -> dict:
        if not re.fullmatch(r"[\w-]+", retell_id):
            raise ValueError(f"not a Retell call id: {retell_id!r}")
        response = self.http.get(f"/v2/get-call/{retell_id}", headers=self.headers)
        response.raise_for_status()
        return response.json()


def model_from_config(path: Path = ROOT / "config" / "models.yaml") -> GraderModel | None:
    """The offline.grader profile, or None when its provider's key is not set."""
    config = yaml.safe_load(path.read_text())
    section, _, name = PROFILE.partition(".")
    cfg = config[section][name]
    if cfg["provider"] != "anthropic":
        raise ValueError(f"{PROFILE} uses {cfg['provider']}, which the grader can't call yet")
    key_env = config["providers"][cfg["provider"]]["key_env"]
    if not os.environ.get(key_env):
        log.warning("%s is not set, so the failure grader is off", key_env)
        return None
    return AnthropicGraderModel(cfg, key_env)


def transcripts_from_env() -> Transcripts | None:
    """Retell transcripts when RETELL_API_KEY is set; without it the server's own turns are used."""
    key = os.environ.get("RETELL_API_KEY")
    return RetellTranscripts(key) if key else None


# ---- what the model reads ----------------------------------------------------------------------


def _local(tenant: Tenant, value: str | None) -> str | None:
    if not value:
        return value
    return tenant.local(parse_iso(value)).strftime("%a %b %-d, %-I:%M %p")


def retell_lines(call: dict) -> list[str]:
    """Retell's transcript_with_tool_calls as plain numbered-ready lines."""
    out = []
    for item in call.get("transcript_with_tool_calls") or []:
        role, content = item.get("role"), (item.get("content") or "").strip()
        if role == "user" and content:
            out.append(f"caller: {content}")
        elif role == "agent" and content:
            out.append(f"agent: {content}")
        elif role == "tool_call_invocation":
            out.append(f"tool: {item.get('name', '')} {item.get('arguments', '')}".strip())
        elif role == "tool_call_result":
            out.append(f"result: {content[:TOOL_RESULT_CHARS]}")
        elif role == "injected" and content:
            out.append(f"note: {content}")
    return out


def server_lines(conn: sqlite3.Connection, call_id: str) -> list[str]:
    """The server's own record of what was said, for a call that didn't come through Retell."""
    rows = conn.execute(
        "SELECT speaker, text FROM turns WHERE call_id = ? ORDER BY seq", (call_id,)
    ).fetchall()
    return [f"{r['speaker']}: {r['text']}" for r in rows]


def call_rows(conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row) -> dict:
    """The server's records for the call, with times in the company's local time."""
    call_id = call["id"]
    bookings = []
    for b in conn.execute("SELECT * FROM bookings WHERE call_id = ? ORDER BY id", (call_id,)):
        request = json.loads(b["request_json"])
        request.pop("call_id", None)
        bookings.append({
            "kind": b["kind"],
            "status": b["status"],
            "window_start": _local(tenant, b["window_start"]),
            "window_end": _local(tenant, b["window_end"]),
            "priority": b["priority"],
            "request": request,
        })
    callbacks = [
        {"due": _local(tenant, c["due_at"]), "phone": c["phone"], "owner": c["owner"],
         "reason": c["reason"], "status": c["status"]}
        for c in conn.execute("SELECT * FROM callbacks WHERE call_id = ? ORDER BY id", (call_id,))
    ]
    flags = [
        {"kind": f["kind"], "reason": f["reason"], "priority": f["priority"]}
        for f in conn.execute(
            "SELECT * FROM flags WHERE call_id = ? AND kind != ? ORDER BY id", (call_id, FLAG_KIND)
        )
    ]
    leads = [
        {"kind": r["kind"], "summary": r["summary"], "status": r["status"]}
        for r in conn.execute("SELECT * FROM leads WHERE call_id = ? ORDER BY id", (call_id,))
    ]
    notes = [
        r["text"] for r in conn.execute("SELECT text FROM notes WHERE call_id = ? ORDER BY id", (call_id,))
    ]
    on_dnc = conn.execute(
        "SELECT 1 FROM do_not_call WHERE phone_key = ?", (call["caller_key"],)
    ).fetchone()
    return {
        "bookings": bookings,
        "callbacks": callbacks,
        "flags": flags,
        "leads": leads,
        "notes": notes,
        "caller_on_do_not_call_list": bool(on_dnc),
    }


def build_record(
    conn: sqlite3.Connection,
    tenant: Tenant,
    call: sqlite3.Row,
    transcripts: Transcripts | None,
) -> tuple[dict, str]:
    """What the model reads, and where the transcript came from (retell or server)."""
    lines, source = None, "server"
    if transcripts is not None and call["id"].startswith(RETELL_PREFIX):
        retell = transcripts.get(call["id"].removeprefix(RETELL_PREFIX))
        lines = retell_lines(retell) or None
        source = "retell" if lines else source
    if lines is None:
        lines = server_lines(conn, call["id"])
    started = parse_iso(call["started_at"])
    record = {
        "call": {
            "started": _local(tenant, call["started_at"]),
            "ended": _local(tenant, call["ended_at"]),
            "how_it_ended": call["end_reason"],
            "direction": call["direction"],
            "company_closed": not tenant.is_open(started),
            "caller_number": call["caller_id"],
        },
        "transcript": lines,
        "records": call_rows(conn, tenant, call),
    }
    return record, source


# ---- what the server keeps ---------------------------------------------------------------------


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


LABEL = re.compile(r"^\s*(caller|agent|tool|result|note)\s*:\s*", re.IGNORECASE)


def evidence_found(line: str, corpus: str) -> bool:
    """Whether a quoted line is in the call: compared word for word, ignoring case, punctuation and
    a leading speaker label. A quote cut with an ellipsis must have every piece in the call."""
    pieces = [_words(LABEL.sub("", p)) for p in re.split(r"\.\.\.|…", line)]
    pieces = [p for p in pieces if p]
    return bool(pieces) and all(p in corpus for p in pieces)


def verdict(answer: dict, record: dict) -> dict:
    """The server's decision on the model's answer: a failure is kept only with a known kind and
    severity, a reason, and evidence that is all found in the call."""
    corpus = _words(" ".join(record["transcript"]) + " " + json.dumps(record["records"]))
    evidence = [str(e)[:EVIDENCE_MAX_CHARS] for e in answer.get("evidence") or []][:MAX_EVIDENCE]
    out = {
        "failure": False,
        "kind": None,
        "severity": None,
        "reason": str(answer.get("reason") or "")[:REASON_MAX_CHARS],
        "evidence": evidence,
        "dropped": None,
    }
    if not answer.get("failure"):
        return out
    if answer.get("kind") not in KINDS or answer.get("severity") not in SEVERITY_PRIORITY:
        out["dropped"] = "no kind or severity"
    elif not out["reason"].strip():
        out["dropped"] = "no reason"
    elif not evidence:
        out["dropped"] = "no evidence"
    else:
        missing = [e for e in evidence if not evidence_found(e, corpus)]
        if missing:
            out["dropped"] = f"evidence not found in the call: {missing[0][:80]}"
    if out["dropped"] is None:
        out.update(failure=True, kind=answer["kind"], severity=answer["severity"])
    return out


def cached(conn: sqlite3.Connection, call_id: str) -> sqlite3.Row | None:
    """The stored grade, if the call was graded without an error."""
    return conn.execute(
        "SELECT * FROM grades WHERE call_id = ? AND error IS NULL", (call_id,)
    ).fetchone()


def grade_call(
    conn: sqlite3.Connection,
    tenant: Tenant,
    model: GraderModel,
    transcripts: Transcripts | None,
    call_id: str,
    now: datetime,
    force: bool = False,
) -> dict:
    """Grade one ended call, or return its stored grade. Returns the grade as a dict with `fresh`
    saying whether the model was asked this time."""
    if not force:
        row = cached(conn, call_id)
        if row is not None:
            return {**dict(row), "fresh": False}
    call = records.get_call(conn, call_id)
    if call is None or call["tenant"] != tenant.slug:
        raise LookupError(f"no call {call_id} for {tenant.slug}")
    if call["ended_at"] is None:
        raise ValueError("the call hasn't ended")
    system = PROMPT.read_text()
    answer, cost, error, source = None, 0.0, None, "server"
    try:
        record, source = build_record(conn, tenant, call, transcripts)
        answer, cost = model.grade(system, json.dumps(record, ensure_ascii=False))
        kept = verdict(answer, record)
    except Exception as exc:
        log.exception("grading %s failed", call_id)
        error = f"{type(exc).__name__}: {exc}"[:300]
        kept = {"failure": False, "kind": None, "severity": None, "reason": "", "evidence": [],
                "dropped": None}
    with tx(conn):
        flag_id = None
        if kept["failure"]:
            open_flag = conn.execute(
                "SELECT id FROM flags WHERE call_id = ? AND kind = ? AND status = 'open'",
                (call_id, FLAG_KIND),
            ).fetchone()
            flag_id = open_flag["id"] if open_flag else records.add_flag(
                conn,
                call_id,
                FLAG_KIND,
                f"{KINDS[kept['kind']]}: {kept['reason']}",
                now,
                SEVERITY_PRIORITY[kept["severity"]],
            )
        conn.execute(
            "INSERT INTO grades (call_id, tenant, failure, kind, severity, reason, evidence_json, "
            "dropped, raw_json, source, model_profile, cost_usd, flag_id, error, graded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (call_id) DO UPDATE SET failure = excluded.failure, kind = excluded.kind, "
            "severity = excluded.severity, reason = excluded.reason, "
            "evidence_json = excluded.evidence_json, dropped = excluded.dropped, "
            "raw_json = excluded.raw_json, source = excluded.source, "
            "model_profile = excluded.model_profile, cost_usd = grades.cost_usd + excluded.cost_usd, "
            "flag_id = excluded.flag_id, error = excluded.error, graded_at = excluded.graded_at",
            (
                call_id,
                tenant.slug,
                int(kept["failure"]),
                kept["kind"],
                kept["severity"],
                kept["reason"] if kept["failure"] or kept["dropped"] else "",
                json.dumps(kept["evidence"]),
                kept["dropped"],
                json.dumps(answer) if answer is not None else None,
                source,
                PROFILE,
                cost,
                flag_id,
                error,
                iso(now),
            ),
        )
    row = conn.execute("SELECT * FROM grades WHERE call_id = ?", (call_id,)).fetchone()
    return {**dict(row), "fresh": True, "cost_now": cost}


# ---- runs ----------------------------------------------------------------------------------------


def calls_between(
    conn: sqlite3.Connection, tenant: Tenant, since: datetime, until: datetime
) -> list[str]:
    """Ended calls that started from since up to and including until, oldest first. A call on the
    boundary of two daily runs is in both, and the cache grades it once."""
    rows = conn.execute(
        "SELECT id FROM calls WHERE tenant = ? AND ended_at IS NOT NULL AND started_at >= ? "
        "AND started_at <= ? ORDER BY started_at, id",
        (tenant.slug, iso(since), iso(until)),
    ).fetchall()
    return [r["id"] for r in rows]


def per_call_usd(conn: sqlite3.Connection) -> float:
    """What a call has cost to grade, from the last graded calls, or the ASSUMED default."""
    row = conn.execute(
        "SELECT AVG(cost_usd) AS avg FROM (SELECT cost_usd FROM grades WHERE cost_usd > 0 "
        "AND error IS NULL ORDER BY graded_at DESC LIMIT 50)"
    ).fetchone()
    return row["avg"] if row and row["avg"] else DEFAULT_CALL_USD


def cost_of(
    conn: sqlite3.Connection, call_ids: list[str], force: bool = False
) -> dict:
    """How many calls a run would send to the model and roughly what it costs, and whether the
    pages should ask before starting it."""
    to_grade = [c for c in call_ids if force or cached(conn, c) is None]
    usd = round(len(to_grade) * per_call_usd(conn), 2)
    return {
        "calls": len(call_ids),
        "to_grade": len(to_grade),
        "already": len(call_ids) - len(to_grade),
        "usd": usd,
        "ask": usd > CONFIRM_USD or len(to_grade) > CONFIRM_CALLS,
        "capped": len(call_ids) > MAX_CALLS_PER_RUN,
    }


def create_run(
    conn: sqlite3.Connection,
    tenant: Tenant,
    call_ids: list[str],
    now: datetime,
    by: str,
    *,
    scope: str,
    since: datetime | None = None,
    until: datetime | None = None,
    force: bool = False,
) -> int:
    """Store a run, queued. scope is call, range or daily."""
    call_ids = call_ids[:MAX_CALLS_PER_RUN]
    est = cost_of(conn, call_ids, force)
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO grader_runs (tenant, scope, since, until, call_ids_json, force, status, "
            "total, estimate_usd, requested_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
            (
                tenant.slug,
                scope,
                iso(since) if since else None,
                iso(until) if until else None,
                json.dumps(call_ids),
                int(force),
                len(call_ids),
                est["usd"],
                by,
                iso(now),
            ),
        )
    return cur.lastrowid


_one_at_a_time = threading.Lock()


def work(services, run_id: int) -> None:
    """Grade every call in a run, recording progress as it goes. Runs one at a time."""
    with _one_at_a_time:
        conn = services.connect()
        try:
            run = conn.execute("SELECT * FROM grader_runs WHERE id = ?", (run_id,)).fetchone()
            if run is None or run["status"] != "queued":
                return
            tenant = services.tenants.by_slug[run["tenant"]]
            model = services.grader_model
            with tx(conn):
                conn.execute(
                    "UPDATE grader_runs SET status = 'running', started_at = ? WHERE id = ?",
                    (iso(services.clock()), run_id),
                )
            if model is None:
                _finish(conn, run_id, services.clock(), "failed", "The grader has no model key.")
                return
            for call_id in json.loads(run["call_ids_json"]):
                try:
                    grade = grade_call(conn, tenant, model, services.grader_transcripts, call_id,
                                       services.clock(), bool(run["force"]))
                except (LookupError, ValueError) as exc:
                    grade = {"failure": 0, "error": str(exc)}
                with tx(conn):
                    conn.execute(
                        "UPDATE grader_runs SET done = done + 1, flagged = flagged + ?, "
                        "errors = errors + ?, cost_usd = cost_usd + ? WHERE id = ?",
                        (
                            int(bool(grade["failure"])),
                            int(bool(grade.get("error"))),
                            grade.get("cost_now", 0.0),
                            run_id,
                        ),
                    )
            _finish(conn, run_id, services.clock(), "done")
        except Exception as exc:
            log.exception("grader run %s failed", run_id)
            _finish(conn, run_id, services.clock(), "failed", f"{type(exc).__name__}: {exc}"[:300])
        finally:
            conn.close()


def _finish(conn, run_id: int, now: datetime, status: str, error: str | None = None) -> None:
    with tx(conn):
        conn.execute(
            "UPDATE grader_runs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, error, iso(now), run_id),
        )


def in_background(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True, name="failure-grader").start()


def launch(services, run_id: int) -> None:
    """Start a stored run without waiting for it (services.grader_runner, a thread by default)."""
    services.grader_runner(lambda: work(services, run_id))


def get_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM grader_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    run = dict(row)
    run["call_ids"] = json.loads(run.pop("call_ids_json"))
    run["finished"] = run["status"] in ("done", "failed")
    run["created"] = parse_iso(run["created_at"])
    run["percent"] = round(100 * run["done"] / run["total"]) if run["total"] else 100
    return run


def recent_runs(conn: sqlite3.Connection, tenant: Tenant, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT id FROM grader_runs WHERE tenant = ? ORDER BY id DESC LIMIT ?", (tenant.slug, limit)
    ).fetchall()
    return [get_run(conn, r["id"]) for r in rows]


def flagged(
    conn: sqlite3.Connection, tenant: Tenant, call_ids: list[str] | None = None, limit: int = 50
) -> list[dict]:
    """Calls the grader kept as failures, newest call first: one run's calls, or the latest."""
    params: list = [tenant.slug]
    where = ""
    if call_ids is not None:
        if not call_ids:
            return []
        where = f" AND g.call_id IN ({','.join('?' * len(call_ids))})"
        params += call_ids
    rows = conn.execute(
        "SELECT g.*, c.started_at, c.caller_id FROM grades g JOIN calls c ON c.id = g.call_id "
        f"WHERE g.tenant = ? AND g.failure = 1 AND g.error IS NULL{where} "
        "ORDER BY c.started_at DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    return [shown(tenant, r) for r in rows]


def shown(tenant: Tenant, row: sqlite3.Row) -> dict:
    """A grade as the pages show it."""
    grade = dict(row)
    grade["evidence"] = json.loads(grade.pop("evidence_json") or "[]")
    grade["label"] = KINDS.get(grade["kind"] or "", "")
    grade["raw"] = json.loads(grade["raw_json"]) if grade.get("raw_json") else None
    if grade.get("started_at"):
        grade["local"] = tenant.local(parse_iso(grade["started_at"]))
    return grade


def grade_for(conn: sqlite3.Connection, tenant: Tenant, call_id: str) -> dict | None:
    row = conn.execute(
        "SELECT g.*, c.started_at, c.caller_id FROM grades g JOIN calls c ON c.id = g.call_id "
        "WHERE g.call_id = ?",
        (call_id,),
    ).fetchone()
    return shown(tenant, row) if row else None


def run_for_call(conn: sqlite3.Connection, call_id: str) -> dict | None:
    """The newest unfinished single-call run for a call, so its page can show progress."""
    row = conn.execute(
        "SELECT id FROM grader_runs WHERE scope = 'call' AND status IN ('queued', 'running') "
        "AND call_ids_json = ? ORDER BY id DESC LIMIT 1",
        (json.dumps([call_id]),),
    ).fetchone()
    return get_run(conn, row["id"]) if row else None


# ---- the daily schedule --------------------------------------------------------------------------


def schedule(conn: sqlite3.Connection, tenant: Tenant) -> dict:
    """The owner's choice: on or off, and the local time of day."""
    row = conn.execute(
        "SELECT enabled, at_time FROM grader_schedule WHERE tenant = ? ORDER BY id DESC LIMIT 1",
        (tenant.slug,),
    ).fetchone()
    if row is None:
        return dict(DEFAULT_SCHEDULE)
    return {"enabled": bool(row["enabled"]), "at": row["at_time"]}


TIME_OF_DAY = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")


def set_schedule(
    conn: sqlite3.Connection, tenant: Tenant, enabled: bool, at: str, author: str, now: datetime
) -> bool:
    """Store a new choice when it differs from the current one. The time is HH:MM, 24-hour."""
    if not TIME_OF_DAY.fullmatch(at):
        raise ValueError("Pick a time of day.")
    with tx(conn):
        if schedule(conn, tenant) == {"enabled": enabled, "at": at}:
            return False
        conn.execute(
            "INSERT INTO grader_schedule (tenant, enabled, at_time, author, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (tenant.slug, int(enabled), at, author, iso(now)),
        )
    return True


def schedule_history(conn: sqlite3.Connection, tenant: Tenant, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM grader_schedule WHERE tenant = ? ORDER BY id DESC LIMIT ?",
        (tenant.slug, limit),
    ).fetchall()


def daily_due(conn: sqlite3.Connection, tenant: Tenant, now: datetime) -> tuple[datetime, datetime] | None:
    """The range the daily run should cover, when one is due now: the schedule is on, today's time
    has passed, and no daily run was made today. It covers calls since the last daily run, or the
    past day for the first one."""
    chosen = schedule(conn, tenant)
    if not chosen["enabled"]:
        return None
    local = tenant.local(now)
    hour, minute = (int(x) for x in chosen["at"].split(":"))
    at = datetime.combine(local.date(), time(hour, minute), tenant.tz)
    if local < at:
        return None
    last = conn.execute(
        "SELECT created_at, until FROM grader_runs WHERE tenant = ? AND scope = 'daily' "
        "ORDER BY id DESC LIMIT 1",
        (tenant.slug,),
    ).fetchone()
    if last is not None and parse_iso(last["created_at"]) >= at:
        return None
    since = parse_iso(last["until"]) if last is not None else now - DAILY_SPAN
    return max(since, now - DAILY_SPAN * 7), now


def start_daily(services, conn: sqlite3.Connection, tenant: Tenant, now: datetime) -> int | None:
    """Called by the jobs loop: store and start the daily run when it is due. Returns its id."""
    if services.grader_model is None:
        return None
    span = daily_due(conn, tenant, now)
    if span is None:
        return None
    run_id = create_run(conn, tenant, calls_between(conn, tenant, *span), now, "schedule",
                        scope="daily", since=span[0], until=span[1])
    launch(services, run_id)
    return run_id
