"""The failure grader's pages (app/failure_grader.py): the owner view's Checks page and its "Check
this call" button, the daily schedule on the Settings page, and the bench's Checks page with the
raw model output.

Any signed-in user can check one call and read the results. A range check spends more, so it needs
an admin, and so does changing the schedule; each route checks the role here, on the server. A
range that would cost more than failure_grader.CONFIRM_USD, or cover more than CONFIRM_CALLS calls,
shows its estimate and starts only on a second press.
"""

from datetime import datetime, timedelta

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import failure_grader as grader
from . import redact
from .owner_view import _amp, _common, _need_admin, _render, _signed_in, _tenant_q, _user

SPANS = {"hour": timedelta(hours=1), "day": timedelta(days=1)}
CUSTOM_MAX = timedelta(days=31)


def _tenant(request: Request, slug: str):
    tenants = request.app.state.services.tenants
    return tenants.by_slug.get(slug) if slug else tenants.default


def span_of(tenant, now: datetime, span: str, start: str, end: str) -> tuple[datetime, datetime]:
    """The range asked for: the past hour or day, or a custom local start and end (datetime-local
    inputs, read in the company's time zone). Raises ValueError for a custom range that isn't one."""
    if span in SPANS:
        return now - SPANS[span], now
    try:
        since = datetime.fromisoformat(start).replace(tzinfo=tenant.tz)
        until = datetime.fromisoformat(end).replace(tzinfo=tenant.tz)
    except ValueError:
        raise ValueError("Pick a start and an end.") from None
    if until <= since:
        raise ValueError("The end has to be after the start.")
    if until - since > CUSTOM_MAX:
        raise ValueError("Pick 31 days or fewer.")
    return since, min(until, now)


def _page_context(request: Request, tenant, slug: str, conn, tab: str) -> dict:
    services = request.app.state.services
    runs = grader.recent_runs(conn, tenant)
    active = next((r for r in runs if not r["finished"]), None)
    return {
        **_common(request, tenant, slug, tab, conn),
        "on": services.grader_model is not None,
        "runs": runs,
        "active": active,
        "flagged": grader.flagged(conn, tenant),
        "kinds": grader.KINDS,
        "schedule": grader.schedule(conn, tenant),
        "local": tenant.local,
        "now_local": tenant.local(services.clock()),
        "confirm_usd": grader.CONFIRM_USD,
        "confirm_calls": grader.CONFIRM_CALLS,
    }


def _start_range(request: Request, tenant, slug: str, span: str, start: str, end: str,
                 confirm: str, force: bool, page: str, tab: str) -> Response:
    """Shared by the owner view and the bench: estimate, ask when it is big, then start."""
    services = request.app.state.services
    now = services.clock()
    conn = services.connect()
    try:
        context = _page_context(request, tenant, slug, conn, tab)
        if not context["on"]:
            return _render(request, page, {**context, "error": "Checks are off on this server."}, 409)
        try:
            since, until = span_of(tenant, now, span, start, end)
        except ValueError as err:
            return _render(request, page, {**context, "error": str(err), "asked": {
                "span": span, "start": start, "end": end}}, 422)
        call_ids = grader.calls_between(conn, tenant, since, until)
        est = grader.cost_of(conn, call_ids, force)
        if est["ask"] and confirm != "1":
            return _render(request, page, {**context, "cost_check": est, "asked": {
                "span": span, "start": start, "end": end, "since": tenant.local(since),
                "until": tenant.local(until), "force": force}})
        run_id = grader.create_run(conn, tenant, call_ids, now, _user(request)["name"],
                                   scope="range", since=since, until=until, force=force)
    finally:
        conn.close()
    grader.launch(services, run_id)
    path = "/bench/checks" if tab == "bench-checks" else "/owner/checks"
    return RedirectResponse(f"{path}{_tenant_q(slug)}{_amp(slug)}run={run_id}", 303)


def add_routes(app: FastAPI) -> None:
    @app.get("/owner/checks", response_class=HTMLResponse)
    def owner_checks(request: Request, tenant: str = "", run: int | None = None):
        if not _signed_in(request):
            return _need_admin(request)
        found = _tenant(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        conn = request.app.state.services.connect()
        try:
            context = _page_context(request, found, tenant, conn, "checks")
            if run is not None:
                shown = grader.get_run(conn, run)
                if shown and shown["tenant"] == found.slug:
                    context["shown_run"] = shown
                    context["run_flagged"] = grader.flagged(conn, found, shown["call_ids"])
        finally:
            conn.close()
        return _render(request, "checks.html", context)

    @app.post("/owner/checks")
    def owner_checks_start(request: Request, tenant: str = "", span: str = Form("day"),
                           start: str = Form(""), end: str = Form(""), confirm: str = Form("")):
        refused = _need_admin(request)
        if refused:
            return refused
        found = _tenant(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        return _start_range(request, found, tenant, span, start, end, confirm, False,
                            "checks.html", "checks")

    @app.get("/owner/checks/runs/{run_id}")
    def owner_checks_run(request: Request, run_id: int):
        """A run's progress as JSON, polled by the pages while it works."""
        if not _signed_in(request):
            return JSONResponse({}, 401)
        conn = request.app.state.services.connect()
        try:
            run = grader.get_run(conn, run_id)
        finally:
            conn.close()
        if run is None:
            return JSONResponse({}, 404)
        keys = ("id", "status", "total", "done", "flagged", "errors", "percent", "finished",
                "cost_usd", "estimate_usd")
        return JSONResponse({k: run[k] for k in keys})

    @app.post("/owner/calls/{call_id}/check")
    def owner_call_check(request: Request, call_id: str, again: str = Form("")):
        """Check one call; a call already checked is checked again only with again=1."""
        if not _signed_in(request):
            return _need_admin(request)
        services = request.app.state.services
        conn = services.connect()
        try:
            call = conn.execute(
                "SELECT tenant, ended_at FROM calls WHERE id = ?", (call_id,)
            ).fetchone()
            if call is None:
                return HTMLResponse("No such call.", 404)
            if services.grader_model is None or call["ended_at"] is None:
                return RedirectResponse(f"/owner/calls/{call_id}#check", 303)
            tenant = services.tenants.by_slug[call["tenant"]]
            if grader.run_for_call(conn, call_id) is None:
                run_id = grader.create_run(conn, tenant, [call_id], services.clock(),
                                           _user(request)["name"], scope="call",
                                           force=again == "1")
                grader.launch(services, run_id)
        finally:
            conn.close()
        return RedirectResponse(f"/owner/calls/{call_id}#check", 303)

    @app.post("/owner/checks/schedule")
    def owner_checks_schedule(request: Request, tenant: str = "", enabled: str = Form("0"),
                              at: str = Form("06:00")):
        refused = _need_admin(request)
        if refused:
            return refused
        found = _tenant(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        conn = services.connect()
        try:
            grader.set_schedule(conn, found, enabled == "1", at, _user(request)["name"],
                                services.clock())
        except ValueError:
            pass
        finally:
            conn.close()
        return RedirectResponse(
            f"/owner/settings{_tenant_q(tenant)}{_amp(tenant)}saved=checks#checks", 303
        )

    # ---- bench ----------------------------------------------------------------------------------

    @app.get("/bench/checks", response_class=HTMLResponse)
    def bench_checks(request: Request, run: int | None = None):
        refused = _need_admin(request)
        if refused:
            return refused
        services = request.app.state.services
        tenant = services.tenants.default
        conn = services.connect()
        try:
            context = {**_page_context(request, tenant, "", conn, "bench-checks"), "bench": True}
            rows = conn.execute(
                "SELECT g.*, c.started_at, c.caller_id FROM grades g JOIN calls c ON c.id = g.call_id "
                "ORDER BY g.graded_at DESC LIMIT 100"
            ).fetchall()
            context["grades"] = [
                {**grader.shown(tenant, r), "key": redact.call_key(r["call_id"])} for r in rows
            ]
            if run is not None:
                context["shown_run"] = grader.get_run(conn, run)
        finally:
            conn.close()
        return _render(request, "bench_checks.html", context)

    @app.post("/bench/checks")
    def bench_checks_start(request: Request, span: str = Form("day"), start: str = Form(""),
                           end: str = Form(""), confirm: str = Form(""), force: str = Form("")):
        refused = _need_admin(request)
        if refused:
            return refused
        tenant = request.app.state.services.tenants.default
        return _start_range(request, tenant, "", span, start, end, confirm, force == "1",
                            "bench_checks.html", "bench-checks")

    @app.post("/bench/checks/calls/{call_id}")
    def bench_check_call(request: Request, call_id: str):
        """Grade one call again, whatever is stored."""
        refused = _need_admin(request)
        if refused:
            return refused
        services = request.app.state.services
        conn = services.connect()
        try:
            call = conn.execute(
                "SELECT tenant, ended_at FROM calls WHERE id = ?", (call_id,)
            ).fetchone()
            if call is None:
                return HTMLResponse("No such call.", 404)
            if services.grader_model is None or call["ended_at"] is None:
                return RedirectResponse("/bench/checks", 303)
            tenant = services.tenants.by_slug[call["tenant"]]
            run_id = grader.create_run(conn, tenant, [call_id], services.clock(),
                                       _user(request)["name"], scope="call", force=True)
        finally:
            conn.close()
        grader.launch(services, run_id)
        return RedirectResponse(f"/bench/checks?run={run_id}", 303)
