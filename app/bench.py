"""The debug bench at /bench: an engineer's view of the same server the owner view runs on. Each
call as a node graph with the prompts and decisions at every step (app/nodes.py), a call in
progress as it happens, the rule proof, a call turned into a draft eval scenario, the live
settings with their documented measurements, and the eval runs (app/bench_data.py). Read-only.

Admin only (an admin account, app/accounts.py), through require_admin. Admins see everything a call
recorded, phone numbers and addresses included (DECISIONS.md 064); only a replay draft, which is
meant to be committed to the suite, is redacted (app/redact.py).
"""

import json
import os
import re
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from . import bench_call, bench_data, bench_pages, failure_grader, nodes, redact, rules, trace
from .db import parse_iso
from .owner_view import _common, _need_admin, _render

RECENT = 60
STATIC = Path(__file__).resolve().parent / "static" / "bench"
SEARCH_MAX_CHARS = 80
# A live view's stream closes after this long; the browser opens it again by itself (the
# EventSource retry), so a forgotten tab never holds a server thread for the length of a call.
STREAM_SECONDS = 5 * 60
RETRY_MS = 1000
STREAM_POLL_SECONDS = 1.0
KEEPALIVE_SECONDS = 15


def require_admin(request: Request) -> Response | None:
    """None when an admin is signed in (app/accounts.py); otherwise the sign-in form, or the
    "admins only" page for a staff user. Every bench route starts here."""
    return _need_admin(request)


def _shown(value):
    """Bench pages show values as recorded; admins see everything (DECISIONS.md 064)."""
    return "" if value is None else value


def _context(request: Request, conn, tab: str) -> dict:
    services = request.app.state.services
    tenant = services.tenants.default
    return {
        **_common(request, tenant, "", tab, conn),
        "bench": True,
        "mask": _shown,
    }


# The call list's filters: each group is one choice, and every group's choice must hold. Each value
# is a SQL condition on the calls table `c`.
FILTERS = {
    "outcome": {
        "booked": "c.outcome IN ('booked', 'changed_booking')",
        "callback": "c.outcome = 'callback'",
        "safety": "c.safety_kind IS NOT NULL",
        "declined": "c.outcome LIKE 'declined%'",
        "none": "c.outcome = 'no_booking'",
    },
    "label": {
        "emergency": "EXISTS (SELECT 1 FROM call_labels l WHERE l.call_id = c.id AND l.label = 'Emergency dispatch')",
        "scheduled": "EXISTS (SELECT 1 FROM call_labels l WHERE l.call_id = c.id AND l.label = 'Scheduled')",
        "no_visit": "EXISTS (SELECT 1 FROM call_labels l WHERE l.call_id = c.id AND l.label = 'No visit')",
    },
    "ended": {
        "agent": "c.end_reason IN ('agent_end', 'stepped_away')",
        "caller": "c.end_reason = 'caller_hangup'",
        "line": "c.end_reason IN ('dropped', 'error')",
        "transfer": "c.end_reason = 'transfer'",
    },
    "signal": {
        "refused": "EXISTS (SELECT 1 FROM tool_calls k WHERE k.call_id = c.id AND k.ok = 0)",
        "person": "EXISTS (SELECT 1 FROM flags f WHERE f.call_id = c.id)",
        "transfer": "(EXISTS (SELECT 1 FROM transfers t WHERE t.call_id = c.id) "
                    "OR EXISTS (SELECT 1 FROM handoffs h WHERE h.call_id = c.id))",
        "hold": "EXISTS (SELECT 1 FROM holds h WHERE h.call_id = c.id)",
        "after_hours": "c.after_hours = 1",
        "commercial": "c.commercial = 1",
    },
}
FILTER_LABELS = {
    "outcome": ("Outcome", {"booked": "Booked", "callback": "Callback", "safety": "Safety",
                            "declined": "Declined", "none": "No booking"}),
    "label": ("Label", {"emergency": "Emergency", "scheduled": "Scheduled", "no_visit": "No visit"}),
    "ended": ("Ended by", {"agent": "Agent", "caller": "Caller", "line": "Line", "transfer": "Transfer"}),
    "signal": ("Signal", {"refused": "Refused", "person": "Person", "transfer": "Transfer",
                          "hold": "Hold", "after_hours": "After hours", "commercial": "Commercial"}),
}


def picked_filters(params) -> dict[str, str]:
    """The filters a request asks for, only the known ones."""
    return {group: params[group] for group in FILTERS if params.get(group) in FILTERS[group]}


def _calls(conn, q: str, tenants, picked: dict[str, str] | None = None) -> list[dict]:
    """Recent calls, or the ones whose id or phone matches q and that pass every picked filter,
    newest first."""
    digits = re.sub(r"\D", "", q)
    conditions, params = [], []
    if len(digits) >= 4:
        conditions.append("c.caller_key LIKE ?")
        params.append(f"%{digits}%")
    elif q:
        conditions.append("c.id LIKE ?")
        params.append(f"{q}%")
    for group, value in (picked or {}).items():
        conditions.append(FILTERS[group][value])
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"""SELECT c.id, c.tenant, c.caller_id, c.started_at, c.ended_at, c.outcome, c.safety_kind,
                   c.end_reason, c.after_hours,
                   (SELECT label FROM call_labels l WHERE l.call_id = c.id) AS label,
                   (SELECT count(*) FROM turns t WHERE t.call_id = c.id) AS turns,
                   (SELECT count(*) FROM tool_calls k WHERE k.call_id = c.id AND k.tool NOT LIKE 'calls.%'
                      AND k.tool != 'turn') AS tools,
                   (SELECT count(*) FROM tool_calls k WHERE k.call_id = c.id AND k.ok = 0) AS refused
            FROM calls c {where} ORDER BY c.started_at DESC LIMIT ?""",
        [*params, RECENT],
    ).fetchall()
    ids = [r["id"] for r in rows]
    delays: dict[str, list[float]] = {i: [] for i in ids}
    if ids:
        marks = ",".join("?" * len(ids))
        for t in conn.execute(
            f"SELECT call_id, timings_json FROM turns WHERE speaker = 'agent' AND call_id IN ({marks})",
            ids,
        ):
            reply = json.loads(t["timings_json"]).get("reply_ms")
            if reply is not None:
                delays[t["call_id"]].append(reply)
    out = []
    for r in rows:
        tenant = tenants.by_slug.get(r["tenant"]) or tenants.default
        d = sorted(delays[r["id"]])
        ended = bench_call.ENDED_BY.get(r["end_reason"] or "", ("", ""))[0] if r["ended_at"] else ""
        length = bench_call.seconds(dict(r))
        out.append({**dict(r), "key": redact.call_key(r["id"]),
                    "local": tenant.local(parse_iso(r["started_at"])),
                    "reply_p50": d[len(d) // 2] if d else None, "live": r["ended_at"] is None,
                    "ended_by": ended, "seconds": length,
                    "cost": bench_call.estimate(r["started_at"], r["ended_at"])})
    return out


def call_for(conn, key: str) -> str | None:
    """The call id behind a bench key (redact.call_key). Read by scanning ids, which at thousands
    of calls is a few milliseconds and keeps the key out of the schema."""
    if not re.fullmatch(r"[0-9a-f]{12}", key):
        return None
    for (call_id,) in conn.execute("SELECT id FROM calls"):
        if redact.call_key(call_id) == key:
            return call_id
    return None


def _summary_lines(conn, call_id: str) -> list[str]:
    """The technician's summary as written (app/summary.py), one line each."""
    row = conn.execute("SELECT lines_json FROM summaries WHERE call_id = ?", (call_id,)).fetchone()
    if not row or not row["lines_json"]:
        return []
    lines = json.loads(row["lines_json"])
    return [str(x) for x in lines] if isinstance(lines, list) else [str(lines)]


def retell_calls(request: Request) -> bench_call.RetellCalls:
    """The app's reader of Retell call objects, made on first use from RETELL_API_KEY."""
    state = request.app.state
    if getattr(state, "bench_retell", None) is None:
        state.bench_retell = bench_call.RetellCalls(failure_grader.transcripts_from_env())
    return state.bench_retell


def add_routes(app: FastAPI) -> None:
    # The bench's own stylesheet and script, apart from the owner view's (/owner/static).
    app.mount("/bench/static", StaticFiles(directory=STATIC), name="bench-static")

    @app.get("/bench", response_class=HTMLResponse)
    def bench_index(request: Request, q: str = ""):
        denied = require_admin(request)
        if denied:
            return denied
        services = request.app.state.services
        q = q.strip()[:SEARCH_MAX_CHARS]
        conn = services.connect()
        try:
            picked = picked_filters(request.query_params)
            found = _calls(conn, q, services.tenants, picked)
            if q and not picked and len(found) == 1:
                return RedirectResponse(f"/bench/calls/{found[0]['key']}", 303)
            context = {**_context(request, conn, "bench"), "bench_query": q, "calls": found,
                       "live": [c for c in found if c["live"]], "picked": picked,
                       "filter_labels": FILTER_LABELS}
        finally:
            conn.close()
        return _render(request, "bench.html", context)

    def call_context(request: Request, key: str) -> dict | None:
        services = request.app.state.services
        conn = services.connect()
        try:
            call_id = call_for(conn, key)
            found = trace.timeline(conn, call_id) if call_id else None
            if found is None:
                return None
            graph = nodes.build(conn, found, services.settings.log_path)
            tenant = services.tenants.by_slug.get(found["call"]["tenant"]) or services.tenants.default
            stages = [dict(r) for r in conn.execute(
                "SELECT stage, version_id, at FROM call_stages WHERE call_id = ? ORDER BY at", (call_id,))]
            log_path = services.settings.log_path
            return {
                **_context(request, conn, "bench"),
                "trace": found,
                "ended": bench_call.ended_by(found),
                "seconds": bench_call.seconds(found["call"]),
                "decisions": bench_call.decisions(conn, found),
                "safety": bench_call.safety_summary(found),
                "timing": bench_call.timing(found),
                "stage_labels": dict(bench_call.STAGES),
                "customers": bench_call.customers(conn, found, log_path),
                "readback": bench_call.readback(conn, found),
                "cost": bench_call.call_cost(conn, found),
                "label": conn.execute("SELECT label, reason FROM call_labels WHERE call_id = ?",
                                      (call_id,)).fetchone(),
                "flow": (conn.execute("SELECT flow FROM call_flows WHERE call_id = ?",
                                      (call_id,)).fetchone() or {"flow": ""})["flow"],
                "summary_lines": _summary_lines(conn, call_id),
                "grade": conn.execute(
                    "SELECT failure, kind, severity, reason, error FROM grades WHERE call_id = ?",
                    (call_id,)).fetchone(),
                "retell_connected": retell_calls(request).connected,
                "is_retell": call_id.startswith(bench_call.RETELL_PREFIX),
                "heard": bench_call.heard(found, None),
                "call": found["call"],
                "call_id": call_id,
                "key": key,
                "local": tenant.local(parse_iso(found["call"]["started_at"])),
                "graph": graph,
                "bookings": found["bookings"],
                "flags": found["flags"],
                "stages": stages,
                "live_rules": rules.live(conn, tenant.slug),
                "draft": bench_data.scenario_draft(found, redact, key),
            }
        finally:
            conn.close()

    @app.get("/bench/calls/{key}", response_class=HTMLResponse)
    def bench_call_page(request: Request, key: str):
        denied = require_admin(request)
        if denied:
            return denied
        context = call_context(request, key)
        if context is None:
            return HTMLResponse("No such call.", 404)
        return _render(request, "bench_call.html", context)

    @app.get("/bench/calls/{key}/graph", response_class=HTMLResponse)
    def bench_call_graph(request: Request, key: str):
        """The graph and its detail alone, for the live view to swap in as the call goes on."""
        denied = require_admin(request)
        if denied:
            return denied
        context = call_context(request, key)
        if context is None:
            return HTMLResponse("No such call.", 404)
        return _render(request, "_bench_graph.html", context)

    @app.get("/bench/calls/{key}/heard", response_class=HTMLResponse)
    def bench_call_heard(request: Request, key: str):
        """The transcript pane with Retell's side filled in: Retell's call object is read here, on
        demand, never while the page itself loads."""
        denied = require_admin(request)
        if denied:
            return denied
        services = request.app.state.services
        conn = services.connect()
        try:
            call_id = call_for(conn, key)
            found = trace.timeline(conn, call_id) if call_id else None
            if found is None:
                return HTMLResponse("No such call.", 404)
            retell, why_not = retell_calls(request).get(call_id)
            context = {
                **_context(request, conn, "bench"),
                "heard": bench_call.heard(found, retell),
                "retell": bench_call.retell_facts(retell),
                "retell_missing": why_not,
                "ended": bench_call.ended_by(found, retell),
                "billed": bench_call.call_cost(conn, found, retell)["retell_billed"],
            }
        finally:
            conn.close()
        return _render(request, "_bench_heard.html", context)

    @app.get("/bench/calls/{key}/stream")
    def bench_call_stream(request: Request, key: str):
        """Server-sent events while a call is in progress: `changed` when a turn or a tool call is
        recorded, `ended` when the call ends. After STREAM_SECONDS the stream closes without
        `ended` and the browser reconnects."""
        denied = require_admin(request)
        if denied:
            return denied
        services = request.app.state.services
        conn = services.connect()
        try:
            call_id = call_for(conn, key)
        finally:
            conn.close()
        if call_id is None:
            return HTMLResponse("No such call.", 404)

        def state():
            conn = services.connect()
            try:
                return conn.execute(
                    """SELECT ended_at,
                              (SELECT count(*) FROM turns WHERE call_id = ?),
                              (SELECT count(*) FROM tool_calls WHERE call_id = ?)
                       FROM calls WHERE id = ?""",
                    (call_id, call_id, call_id),
                ).fetchone()
            finally:
                conn.close()

        def events():
            last = None
            opened = quiet = time.monotonic()
            yield f"retry: {RETRY_MS}\n\n"
            while True:
                row = state()
                if row is not None and tuple(row) != last:
                    last, quiet = tuple(row), time.monotonic()
                    yield f"event: changed\ndata: {json.dumps({'turns': row[1], 'requests': row[2]})}\n\n"
                if row is None or row[0]:
                    yield "event: ended\ndata: {}\n\n"
                    return
                if time.monotonic() - opened >= STREAM_SECONDS:
                    return  # the browser reconnects; the call is still going
                if time.monotonic() - quiet > KEEPALIVE_SECONDS:
                    quiet = time.monotonic()
                    yield ": still open\n\n"
                time.sleep(STREAM_POLL_SECONDS)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/bench/calls/{key}/scenario.yaml")
    def bench_call_scenario(request: Request, key: str):
        denied = require_admin(request)
        if denied:
            return denied
        conn = request.app.state.services.connect()
        try:
            call_id = call_for(conn, key)
            found = trace.timeline(conn, call_id) if call_id else None
        finally:
            conn.close()
        if found is None:
            return HTMLResponse("No such call.", 404)
        return PlainTextResponse(
            bench_data.scenario_draft(found, redact, key),
            media_type="application/yaml",
            headers={"Content-Disposition": f'attachment; filename="replay-{key}.yaml"'},
        )

    @app.get("/bench/settings", response_class=HTMLResponse)
    def bench_settings(request: Request):
        denied = require_admin(request)
        if denied:
            return denied
        services = request.app.state.services
        conn = services.connect()
        try:
            tenant = services.tenants.default
            context = {
                **_context(request, conn, "bench-settings"),
                "knobs": bench_data.knobs(),
                "unset": bench_data.UNSET,
                "profiles": bench_data.profiles(os.environ.get("MODEL_PROFILE", "")),
                "stack_note": bench_data.STACK_NOTE,
                "switches": bench_data.switches(services.settings, os.environ),
                "live_rules": rules.live(conn, tenant.slug),
                "prompts": bench_pages.prompt_files(),
                "capacity": bench_pages.capacity(),
                "after_hours": bench_pages.after_hours(tenant),
            }
        finally:
            conn.close()
        return _render(request, "bench_settings.html", context)

    @app.get("/bench/questions", response_class=HTMLResponse)
    def bench_questions(request: Request):
        denied = require_admin(request)
        if denied:
            return denied
        services = request.app.state.services
        conn = services.connect()
        try:
            context = {**_context(request, conn, "bench-questions"),
                       "groups": bench_pages.questions(conn, services.tenants.default)}
        finally:
            conn.close()
        return _render(request, "bench_questions.html", context)

    @app.get("/bench/cost", response_class=HTMLResponse)
    def bench_cost(request: Request):
        denied = require_admin(request)
        if denied:
            return denied
        services = request.app.state.services
        conn = services.connect()
        try:
            context = {**_context(request, conn, "bench-cost"),
                       **bench_pages.cost_rows(conn, services.tenants)}
        finally:
            conn.close()
        return _render(request, "bench_cost.html", context)

    @app.get("/bench/evals", response_class=HTMLResponse)
    def bench_evals(request: Request, suite: str = "all"):
        denied = require_admin(request)
        if denied:
            return denied
        runs = [bench_data.run_summary(r) for r in bench_data.runs()]
        suites = sorted({r["suite"] for r in runs})
        suite = suite if suite in suites else "all"
        shown = [r for r in runs if suite == "all" or r["suite"] == suite]
        baseline = json.loads(bench_data.BASELINE.read_text())
        conn = request.app.state.services.connect()
        try:
            context = {
                **_context(request, conn, "bench-evals"),
                "runs": shown,
                "suites": suites,
                "suite": suite,
                "floor": baseline.get("min_eval_pass_rate"),
                "reference": baseline.get("eval_results", "").rsplit("/", 1)[-1].removesuffix(".json"),
                "rounds": bench_data.rounds(),
            }
        finally:
            conn.close()
        return _render(request, "bench_evals.html", context)

    def find_run(run_id: str):
        return next((r for r in bench_data.runs() if r.run_id == run_id), None)

    @app.get("/bench/evals/{run_id}", response_class=HTMLResponse)
    def bench_run(request: Request, run_id: str):
        denied = require_admin(request)
        if denied:
            return denied
        run = find_run(run_id)
        if run is None:
            return HTMLResponse("No such run.", 404)
        baseline = json.loads(bench_data.BASELINE.read_text())
        conn = request.app.state.services.connect()
        try:
            context = {
                **_context(request, conn, "bench-evals"),
                "s": bench_data.run_summary(run),
                "run": run,
                "failures": bench_data.failures(run),
                "floor": baseline.get("min_eval_pass_rate"),
                "check_rows": bench_data.check_rows,
                "langsmith": request.app.state.services.settings.langsmith_url,
            }
        finally:
            conn.close()
        return _render(request, "bench_run.html", context)

    @app.get("/bench/evals/{run_id}/{scenario}", response_class=HTMLResponse)
    def bench_scenario(request: Request, run_id: str, scenario: str):
        denied = require_admin(request)
        if denied:
            return denied
        run = find_run(run_id)
        result = next((s for s in run.scenarios if s.key == scenario), None) if run else None
        if result is None:
            return HTMLResponse("No such scenario in that run.", 404)
        conn = request.app.state.services.connect()
        try:
            context = {
                **_context(request, conn, "bench-evals"),
                "run": run,
                "r": result,
                "checks": bench_data.check_rows(result),
                "transcript": [{"role": t.role, "text": t.text} for t in result.transcript],
                "tools": [{"name": t.name, "args": json.dumps(t.arguments, ensure_ascii=False)}
                          for t in result.tool_calls],
                "langsmith": request.app.state.services.settings.langsmith_url,
            }
        finally:
            conn.close()
        return _render(request, "bench_scenario.html", context)

    @app.get("/bench/rounds/{slug}", response_class=HTMLResponse)
    def bench_round(request: Request, slug: str):
        denied = require_admin(request)
        if denied:
            return denied
        path = bench_data.round_path(slug)
        if path is None:
            return HTMLResponse("No such round.", 404)
        title, _, body = path.read_text().partition("\n")
        conn = request.app.state.services.connect()
        try:
            context = {
                **_context(request, conn, "bench-evals"),
                "doc": bench_data.markdown(body),
                "title": title.lstrip("# ").strip(),
                "source": str(path.relative_to(bench_data.ROOT)),
            }
        finally:
            conn.close()
        return _render(request, "bench_round.html", context)
