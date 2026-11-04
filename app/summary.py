"""The technician's summary (spec S-16): a few short lines pinned to the top of a booked job's notes,
written after the call by the post_call.summary model profile (config/models.yaml), with thinking
on because nobody is waiting for it.

It runs in the background jobs (app/jobs.py), never on the call. The model drafts the lines from
the call record; the server decides what reaches the job:
- a line about risk, access or recent work is dropped when the record holds nothing it could come
  from, whatever the model wrote;
- a line with a number that is not in the record (a gate code, a temperature, a time) is dropped;
- every line is cut to MAX_LINE_CHARS.
The summary goes to the FSM as a new pinned job note, so the intake in the job summary and the
notes already on the job are never changed. A model call that fails or times out is tried once more
on the next run; after that the job keeps the notes it has and the failure is logged.
"""

import heapq
import itertools
import json
import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

import yaml

from . import cards, lookup, owner_settings, records
from .db import iso, parse_iso, tx
from .fsm import FsmAdapter, FsmRejected, FsmUnavailable
from .settings import QUEUE_MAX_ATTEMPTS
from .tenant import Tenant

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "post_call.summary"
PROMPT = ROOT / "prompts" / "post-call-summary.md"
# The first try and one retry, on the next run of the jobs.
MODEL_ATTEMPTS = 2
# One model call, and the time after which a run starts no new summary. The jobs run on one
# thread, so together they bound how long a run can hold the next run's alerts and callbacks: at
# most RUN_SECONDS plus one TIMEOUT_SECONDS (ASSUMED values; a summary call is usually seconds).
TIMEOUT_SECONDS = 30
RUN_SECONDS = 20
# Summaries per run, whatever the time.
SUMMARIES_PER_RUN = 5
# How many waiting calls one run reads; most wait on a queued booking and cost nothing.
ROWS_PER_RUN = 200
# A call older than this is not summarized: the visit may be over, and a backlog after an outage
# (or after summaries are turned on) should not write notes onto old jobs.
MAX_AGE = timedelta(hours=24)
MAX_LINE_CHARS = 120
HISTORY_MONTHS = 24
HEADER = "Summary from the call:"

FIELDS = {
    "problem": "Problem",
    "watch_for": "Watch for",
    "access": "Access",
    "recent_work": "Recent work",
    "told": "Told",
}
SCHEMA = {
    "type": "object",
    "properties": {field: {"type": "string"} for field in FIELDS},
    "required": list(FIELDS),
    "additionalProperties": False,
}
# The record a line may come from. A line whose source is empty is the model filling a field.
SOURCES = {
    "watch_for": lambda r: (
        r["priority"] in ("High", "Urgent") or r["priority_reasons"] or r["safety"]
    ),
    "access": lambda r: (
        r["arrival_notes"] or r["who_home"] or r["customer_notes"] or r["notes_from_this_call"]
    ),
    "recent_work": lambda r: r["completed_jobs"] or r["equipment"],
}
NUMBER = re.compile(r"\d+")
PHONE = re.compile(r"\d{3}\D{0,3}\d{3}\D{0,2}\d{4}")


class SummaryModel(Protocol):
    def write(self, system: str, record: str) -> tuple[dict, float]:
        """The draft fields and what the call cost in USD."""
        ...


class AnthropicSummaryModel:
    def __init__(self, cfg: dict, key_env: str, client=None):
        self.cfg = cfg
        if client is None:
            import anthropic

            client = anthropic.Anthropic(
                api_key=os.environ.get(key_env), timeout=TIMEOUT_SECONDS, max_retries=0
            )
        self.client = client

    def write(self, system: str, record: str) -> tuple[dict, float]:
        off = {"type": self.cfg.get("thinking_off", "disabled")}
        thinking = {"type": "adaptive"} if self.cfg["thinking"] else off
        message = self.client.messages.create(
            model=self.cfg["model"],
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": record}],
            thinking=thinking,
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if message.stop_reason != "end_turn":
            raise RuntimeError(f"the summary model stopped with {message.stop_reason}")
        text = "".join(b.text for b in message.content if b.type == "text")
        price, usage = self.cfg["price"], message.usage
        cost = (usage.input_tokens * price["input"] + usage.output_tokens * price["output"]) / 1e6
        return json.loads(text), cost


def model_from_config(path: Path = ROOT / "config" / "models.yaml") -> SummaryModel | None:
    """The post_call.summary profile, or None when its provider's key is not set."""
    config = yaml.safe_load(path.read_text())
    section, _, name = PROFILE.partition(".")
    cfg = config[section][name]
    if cfg["provider"] != "anthropic":
        raise ValueError(f"{PROFILE} uses {cfg['provider']}, which the summary can't call yet")
    key_env = config["providers"][cfg["provider"]]["key_env"]
    if not os.environ.get(key_env):
        log.warning("%s is not set, so technician summaries are off", key_env)
        return None
    return AnthropicSummaryModel(cfg, key_env)


def record(
    conn: sqlite3.Connection,
    tenant: Tenant,
    fsm: FsmAdapter,
    call: sqlite3.Row,
    booking: sqlite3.Row,
    now: datetime,
) -> dict:
    """What the model may draw on: the booking as the agent sent it, what the caller was told, the
    transcript and the customer's history. The caller's phone number is left out."""
    request = json.loads(booking["request_json"])
    details = request["details"]
    window = lookup.window_text(tenant, booking["window_start"], booking["window_end"])
    customer_id = booking["customer_id"]
    jobs = lookup.history(fsm, tenant, customer_id, "jobs", now, HISTORY_MONTHS)
    callbacks = conn.execute(
        "SELECT due_at, reason FROM callbacks WHERE call_id = ? ORDER BY due_at", (call["id"],)
    ).fetchall()
    turns = conn.execute(
        "SELECT speaker, text FROM turns WHERE call_id = ? ORDER BY seq", (call["id"],)
    ).fetchall()
    return {
        "job_type": request["job_type"].replace("_", " "),
        "issue": details["issue"],
        "since": details["since"],
        "system": details.get("system_type") or "",
        "tried": details["tried"],
        "inside_temp_f": details.get("inside_temp_f"),
        "who_home": details["who_home"],
        "approver": details["approver"],
        "arrival_notes": details.get("arrival_notes") or "",
        "priority": booking["priority"],
        "priority_reasons": request["priority_reasons"],
        "raised_after_wait": bool(request.get("raised_after_wait")),
        "safety": {"kind": call["safety_kind"], "booked_anyway": bool(booking["safety_override"])}
        if call["safety_kind"]
        else None,
        "arrival_window": f"{window['weekday']} {window['date']}, {window['label']}",
        "callbacks_promised": [
            {"by": tenant.local(parse_iso(c["due_at"])).strftime("%-I:%M %p"), "why": c["reason"]}
            for c in callbacks
        ],
        "notes_from_this_call": request.get("notes", [])
        + [
            r["text"]
            for r in conn.execute("SELECT text FROM notes WHERE call_id = ?", (call["id"],))
        ],
        "customer_notes": [n["text"] for n in fsm.notes(customer_id)],
        "completed_jobs": [
            {"completed": j["completed"], "job_type": j["job_type"], "summary": j["summary"]}
            for j in jobs
            if j["status"] == "Completed" and j["job_id"] != booking["fsm_job_id"]
        ][:5],
        "equipment": lookup.history(fsm, tenant, customer_id, "equipment", now, HISTORY_MONTHS)[:5],
        "transcript": [
            f"{'Caller' if t['speaker'] == 'caller' else 'Agent'}: {t['text']}" for t in turns
        ],
    }


def clip(text: str) -> str:
    if len(text) <= MAX_LINE_CHARS:
        return text
    return text[: MAX_LINE_CHARS - 3].rsplit(" ", 1)[0].rstrip(" ,;.") + "..."


def lines(fields: dict, rec: dict) -> tuple[list[list[str]], list[dict]]:
    """The lines that go to the job, as [label, text], and the ones dropped, with why. Problem and
    Told are always there: when the model's line is missing or dropped, the record's own words
    stand in."""
    known = set(NUMBER.findall(json.dumps(rec)))
    kept: dict[str, str] = {}
    dropped = []
    for field in FIELDS:
        text = " ".join(str(fields.get(field) or "").split())
        if not text:
            continue
        source = SOURCES.get(field)
        unknown = [n for n in NUMBER.findall(text) if n not in known]
        if source and not source(rec):
            reason = "nothing in the record to say it from"
        elif unknown:
            reason = f"a number not in the record ({', '.join(unknown)})"
        elif PHONE.search(text):
            reason = "a phone number"
        else:
            kept[field] = clip(text)
            continue
        dropped.append({"field": field, "text": text, "reason": reason})
    kept.setdefault("problem", clip(f"{rec['issue']}, since {rec['since']}"))
    told = [f"Arrival window {rec['arrival_window']}"]
    told += [f"call back by {c['by']} ({c['why']})" for c in rec["callbacks_promised"]]
    kept.setdefault("told", clip("; ".join(told)))
    return [[FIELDS[f], kept[f]] for f in FIELDS if f in kept], dropped


def note_text(summary_lines: list[list[str]]) -> str:
    return "\n".join([HEADER] + [f"{label}: {text}" for label, text in summary_lines])


def run(
    conn: sqlite3.Connection,
    tenant: Tenant,
    fsm: FsmAdapter,
    model: SummaryModel | None,
    now: datetime,
    timer: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
) -> dict:
    """Summarize each ended call with a booked job, oldest first. A call whose booking is still
    queued waits for it; a call with no booking (a safety call, a callback, a turned-away caller)
    gets no summary. Calls left when the run's time or count is used up wait for the next run.
    `deadline` is a `timer()` value shared across tenants, so one jobs run spends at most
    RUN_SECONDS on summaries in all; without it the run gets RUN_SECONDS from now."""
    counts = {"done": 0, "skipped": 0, "waiting": 0, "retry": 0, "failed": 0}
    if deadline is None:
        deadline = timer() + RUN_SECONDS
    # Read a bounded slice, inside the budget, so a large backlog can't stall the jobs thread
    # before the first check. The rest are reached on later runs.
    # One indexed read per status, each already in order, so SQLite stops at the limit instead of
    # sorting the whole backlog; the two short lists are then merged oldest first.
    rows = heapq.merge(
        *(
            conn.execute(
                "SELECT * FROM calls WHERE tenant = ? AND summary_status = ? "
                "ORDER BY ended_at, id LIMIT ?",
                (tenant.slug, status, ROWS_PER_RUN),
            ).fetchall()
            for status in ("pending", "written")
        ),
        key=lambda call: (call["ended_at"] or "", call["id"]),
    )
    rows = list(itertools.islice(rows, ROWS_PER_RUN))
    budget = SUMMARIES_PER_RUN
    for index, call in enumerate(rows):
        if budget == 0 or timer() >= deadline:
            # Out of time or count: the rest wait for the next run unread, so a backlog can't keep
            # the jobs thread busy past the budget.
            counts["waiting"] += len(rows) - index
            break
        booking = conn.execute(
            "SELECT * FROM bookings WHERE call_id = ? AND kind = 'book' ORDER BY id DESC LIMIT 1",
            (call["id"],),
        ).fetchone()
        old = call["ended_at"] and parse_iso(call["ended_at"]) < now - MAX_AGE
        # The owner's technician summaries setting, as it was when the call started.
        off = not owner_settings.for_call(conn, tenant, call["id"])["technician_summaries"]
        if booking is None or booking["status"] == "failed" or old or off:
            with tx(conn):
                records.update_call(conn, call["id"], summary_status="skipped")
            counts["skipped"] += 1
            continue
        # A summary already written only needs sending, which needs no model.
        needs_model = call["summary_status"] == "pending"
        if booking["status"] != "booked" or (needs_model and model is None):
            counts["waiting"] += 1
            continue
        budget -= 1
        counts[_summarize(conn, tenant, fsm, model, call, booking, now)] += 1
    return counts


def _summarize(conn, tenant, fsm, model, call, booking, now) -> str:
    row = conn.execute("SELECT * FROM summaries WHERE call_id = ?", (call["id"],)).fetchone()
    if row is None:
        with tx(conn):
            conn.execute(
                "INSERT INTO summaries (call_id, booking_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (call["id"], booking["id"], iso(now), iso(now)),
            )
        row = conn.execute("SELECT * FROM summaries WHERE call_id = ?", (call["id"],)).fetchone()
    if row["lines_json"] is None:
        try:
            rec = record(conn, tenant, fsm, call, booking, now)
        except FsmUnavailable as exc:
            # The history couldn't be read; the model hasn't been asked, so nothing is used up.
            _note_error(conn, call["id"], f"history: {exc}", now)
            return "retry"
        except FsmRejected as exc:
            return _give_up(conn, call["id"], f"history: {exc.code}: {exc.detail}", now)
        try:
            fields, cost = model.write(PROMPT.read_text(), json.dumps(rec, indent=1))
        except Exception as exc:  # noqa: BLE001
            # Whatever went wrong (a timeout, an API error, a reply that isn't the schema), the job
            # keeps its notes and the model is asked once more on the next run.
            return _model_failed(conn, call["id"], row["model_attempts"] + 1, exc, now)
        kept, dropped = lines(fields, rec)
        # The record it was written from has no card digits; a slip by the model still never
        # reaches the technician's note (DECISIONS.md 080).
        kept = cards.scrub(kept)
        for d in dropped:
            log.warning("summary for call %s: dropped %s, %s", call["id"], d["field"], d["reason"])
        with tx(conn):
            conn.execute(
                "UPDATE summaries SET lines_json = ?, dropped_json = ?, cost_usd = ?, "
                "model_attempts = model_attempts + 1, last_error = NULL, updated_at = ? "
                "WHERE call_id = ?",
                (json.dumps(kept), json.dumps(dropped), cost, iso(now), call["id"]),
            )
            records.update_call(conn, call["id"], summary_status="written")
        row = conn.execute("SELECT * FROM summaries WHERE call_id = ?", (call["id"],)).fetchone()
    text = note_text(json.loads(row["lines_json"]))
    try:
        # A note that reached the FSM before its answer was lost is not added twice.
        if not any(n["text"] == text for n in fsm.job_notes(booking["fsm_job_id"])):
            fsm.add_note(
                f"summary:{call['id']}",
                booking["customer_id"],
                text,
                booking["fsm_job_id"],
                pin=True,
            )
    except FsmUnavailable as exc:
        if row["post_attempts"] + 1 >= QUEUE_MAX_ATTEMPTS:
            return _give_up(conn, call["id"], f"ServiceTitan: {exc}", now, post=True)
        _note_error(conn, call["id"], f"ServiceTitan: {exc}", now, post=True)
        return "retry"
    except FsmRejected as exc:
        return _give_up(conn, call["id"], f"ServiceTitan: {exc.code}: {exc.detail}", now, post=True)
    with tx(conn):
        conn.execute(
            "UPDATE summaries SET post_attempts = post_attempts + 1, last_error = NULL, "
            "updated_at = ? WHERE call_id = ?",
            (iso(now), call["id"]),
        )
        records.update_call(conn, call["id"], summary_status="done")
    return "done"


def _note_error(conn, call_id: str, error: str, now: datetime, post: bool = False) -> None:
    log.warning("summary for call %s failed, will retry: %s", call_id, error)
    bump = "post_attempts = post_attempts + 1, " if post else ""
    with tx(conn):
        conn.execute(
            f"UPDATE summaries SET {bump}last_error = ?, updated_at = ? WHERE call_id = ?",
            (error, iso(now), call_id),
        )


def _model_failed(conn, call_id: str, attempts: int, exc: Exception, now: datetime) -> str:
    error = f"model: {type(exc).__name__}: {exc}"
    if attempts >= MODEL_ATTEMPTS:
        with tx(conn):
            conn.execute(
                "UPDATE summaries SET model_attempts = ? WHERE call_id = ?", (attempts, call_id)
            )
        return _give_up(conn, call_id, error, now)
    log.warning("summary for call %s failed, will retry: %s", call_id, error)
    with tx(conn):
        conn.execute(
            "UPDATE summaries SET model_attempts = ?, last_error = ?, updated_at = ? "
            "WHERE call_id = ?",
            (attempts, error, iso(now), call_id),
        )
    return "retry"


def _give_up(conn, call_id: str, error: str, now: datetime, post: bool = False) -> str:
    log.error("summary for call %s not written, the job keeps its notes: %s", call_id, error)
    bump = "post_attempts = post_attempts + 1, " if post else ""
    with tx(conn):
        conn.execute(
            f"UPDATE summaries SET {bump}last_error = ?, updated_at = ? WHERE call_id = ?",
            (error, iso(now), call_id),
        )
        records.update_call(conn, call_id, summary_status="failed")
    return "failed"


def for_call(conn: sqlite3.Connection, call: sqlite3.Row) -> dict | None:
    """The summary as the owner view shows it, or None for a call that gets none."""
    if call["summary_status"] in ("none", "skipped"):
        return None
    row = conn.execute("SELECT * FROM summaries WHERE call_id = ?", (call["id"],)).fetchone()
    summary_lines = json.loads(row["lines_json"]) if row and row["lines_json"] else []
    return {"status": call["summary_status"], "lines": summary_lines}
