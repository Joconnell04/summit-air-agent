"""The debug bench at /bench: an engineer's view of the same server the owner view runs on. Each
call as a node graph with the prompts and decisions at every step (app/nodes.py), a call in
progress as it happens, the rule proof, a call turned into a draft eval scenario, the live
settings with their documented measurements, and the eval runs (app/bench_data.py). Read-only.

Admin only (an admin account, app/accounts.py), through require_admin. Every value shown passes through app/redact.py.
"""

import json
import os
import re
import time

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from . import bench_data, nodes, redact, rules, trace
from .db import parse_iso
from .owner_view import _common, _need_admin, _render

RECENT = 60
SEARCH_MAX_CHARS = 80
# A live view keeps its stream open this long, then the page offers to reconnect.
STREAM_SECONDS = 30 * 60
STREAM_POLL_SECONDS = 1.0
KEEPALIVE_SECONDS = 15


def require_admin(request: Request) -> Response | None:
    """None when an admin is signed in (app/accounts.py); otherwise the sign-in form, or the
    "admins only" page for a staff user. Every bench route starts here."""
    return _need_admin(request)


def _context(request: Request, conn, tab: str) -> dict:
    services = request.app.state.services
    tenant = services.tenants.default
    return {
        **_common(request, tenant, "", tab, conn),
        "bench": True,
        "mask": redact.text,
    }


def _calls(conn, q: str, tenants) -> list[dict]:
    """Recent calls, or the ones whose id or phone matches q, newest first."""
    digits = re.sub(r"\D", "", q)
    if len(digits) >= 4:
        where, params = "WHERE c.caller_key LIKE ?", [f"%{digits}%"]
    elif q:
        where, params = "WHERE c.id LIKE ?", [f"{q}%"]
    else:
        where, params = "", []
    rows = conn.execute(
        f"""SELECT c.id, c.tenant, c.caller_id, c.started_at, c.ended_at, c.outcome, c.safety_kind,
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
        out.append({**dict(r), "local": tenant.local(parse_iso(r["started_at"])),
                    "reply_p50": d[len(d) // 2] if d else None, "live": r["ended_at"] is None})
    return out


def add_routes(app: FastAPI) -> None:
    @app.get("/bench", response_class=HTMLResponse)
    def bench_index(request: Request, q: str = ""):
        denied = require_admin(request)
        if denied:
            return denied
        services = request.app.state.services
        q = q.strip()[:SEARCH_MAX_CHARS]
        conn = services.connect()
        try:
            found = _calls(conn, q, services.tenants)
            if q and len(found) == 1:
                return RedirectResponse(f"/bench/calls/{found[0]['id']}", 303)
            context = {**_context(request, conn, "bench"), "bench_query": q, "calls": found,
                       "live": [c for c in found if c["live"]]}
        finally:
            conn.close()
        return _render(request, "bench.html", context)

    def call_context(request: Request, call_id: str) -> dict | None:
        services = request.app.state.services
        conn = services.connect()
        try:
            found = trace.timeline(conn, call_id)
            if found is None:
                return None
            graph = nodes.build(conn, found, services.settings.log_path)
            tenant = services.tenants.by_slug.get(found["call"]["tenant"]) or services.tenants.default
            stages = [dict(r) for r in conn.execute(
                "SELECT stage, version_id, at FROM call_stages WHERE call_id = ? ORDER BY at", (call_id,))]
            return {
                **_context(request, conn, "bench"),
                "call": redact.value(found["call"]),
                "call_id": call_id,
                "local": tenant.local(parse_iso(found["call"]["started_at"])),
                "graph": graph,
                "bookings": redact.value(found["bookings"]),
                "flags": redact.value(found["flags"]),
                "stages": stages,
                "live_rules": rules.live(conn, tenant.slug),
                "draft": bench_data.scenario_draft(found, redact),
            }
        finally:
            conn.close()

    @app.get("/bench/calls/{call_id}", response_class=HTMLResponse)
    def bench_call(request: Request, call_id: str):
        denied = require_admin(request)
        if denied:
            return denied
        context = call_context(request, call_id)
        if context is None:
            return HTMLResponse("No such call.", 404)
        return _render(request, "bench_call.html", context)

    @app.get("/bench/calls/{call_id}/graph", response_class=HTMLResponse)
    def bench_call_graph(request: Request, call_id: str):
        """The graph and its detail alone, for the live view to swap in as the call goes on."""
        denied = require_admin(request)
        if denied:
            return denied
        context = call_context(request, call_id)
        if context is None:
            return HTMLResponse("No such call.", 404)
        return _render(request, "_bench_graph.html", context)

    @app.get("/bench/calls/{call_id}/stream")
    def bench_call_stream(request: Request, call_id: str):
        """Server-sent events while a call is in progress: `changed` when a turn or a tool call is
        recorded, `ended` when the call ends or the stream has been open STREAM_SECONDS."""
        denied = require_admin(request)
        if denied:
            return denied
        services = request.app.state.services

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

        if state() is None:
            return HTMLResponse("No such call.", 404)

        def events():
            last = None
            opened = quiet = time.monotonic()
            while True:
                row = state()
                if row is not None and tuple(row) != last:
                    last, quiet = tuple(row), time.monotonic()
                    yield f"event: changed\ndata: {json.dumps({'turns': row[1], 'requests': row[2]})}\n\n"
                if row is None or row[0] or time.monotonic() - opened >= STREAM_SECONDS:
                    yield "event: ended\ndata: {}\n\n"
                    return
                if time.monotonic() - quiet > KEEPALIVE_SECONDS:
                    quiet = time.monotonic()
                    yield ": still open\n\n"
                time.sleep(STREAM_POLL_SECONDS)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/bench/calls/{call_id}/scenario.yaml")
    def bench_call_scenario(request: Request, call_id: str):
        denied = require_admin(request)
        if denied:
            return denied
        conn = request.app.state.services.connect()
        try:
            found = trace.timeline(conn, call_id)
        finally:
            conn.close()
        if found is None:
            return HTMLResponse("No such call.", 404)
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", call_id)[:60]
        return PlainTextResponse(
            bench_data.scenario_draft(found, redact),
            media_type="application/yaml",
            headers={"Content-Disposition": f'attachment; filename="replay-{name}.yaml"'},
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
            }
        finally:
            conn.close()
        return _render(request, "bench_settings.html", context)

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

    @app.get("/bench/evals/{run_id}/{key}", response_class=HTMLResponse)
    def bench_scenario(request: Request, run_id: str, key: str):
        denied = require_admin(request)
        if denied:
            return denied
        run = find_run(run_id)
        result = next((s for s in run.scenarios if s.key == key), None) if run else None
        if result is None:
            return HTMLResponse("No such scenario in that run.", 404)
        conn = request.app.state.services.connect()
        try:
            context = {
                **_context(request, conn, "bench-evals"),
                "run": run,
                "r": result,
                "checks": bench_data.check_rows(result),
                "transcript": [{"role": t.role, "text": redact.text(t.text)} for t in result.transcript],
                "tools": [{"name": t.name, "args": json.dumps(redact.value(t.arguments), ensure_ascii=False)}
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
                "doc": bench_data.markdown(redact.text(body)),
                "title": title.lstrip("# ").strip(),
                "source": str(path.relative_to(bench_data.ROOT)),
            }
        finally:
            conn.close()
        return _render(request, "bench_round.html", context)
