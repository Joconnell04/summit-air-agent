"""The owner view: three read-only pages for the owner and dispatcher (Today, Calls, Results), each
call's page, the one write (a report to Revin, story O-8), and the design system's live specimen.
The numbers come from app/owner.py and the plain notes from app/plain.py; this module handles the
login token and renders HTML. Design: docs/design-system.md.

The token is OWNER_TOKEN. Opening /owner?token=... once sets a cookie and drops the token from the
address bar; without either, the page shows a one-field form. An empty OWNER_TOKEN refuses all.
"""

import hmac
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import owner, plain, reports, trace
from .settings import REPORT_MAX_CHARS, WORKER_STALE_SECONDS

COOKIE = "owner_token"
COOKIE_DAYS = 30
HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


def _valid(request: Request, given: str | None) -> bool:
    token = request.app.state.services.settings.owner_token
    return bool(token and given and hmac.compare_digest(given.encode(), token.encode()))


def _remember(request: Request, response: Response, token: str) -> Response:
    # Secure when served over HTTPS, so the cookie never travels in clear text there.
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=COOKIE_DAYS * 86400,
    )
    return response


def _render(request: Request, name: str, context: dict, status: int = 200) -> Response:
    return templates.TemplateResponse(request, name, context, status_code=status)


def _signed_in(request: Request) -> bool:
    return _valid(request, request.cookies.get(COOKIE))


def _login(request: Request) -> Response:
    return _render(request, "login.html", {"failed": False}, 401)


def _common(request: Request, tenant, slug: str, tab: str, conn) -> dict:
    """What every page's header needs: the company, the tab, and the status line (story O-5)."""
    services = request.app.state.services
    now = services.clock()
    return {
        "tenant": tenant.name,
        "slug": slug,
        "tab": tab,
        "now": now,
        "status": owner.status(conn, tenant, now, WORKER_STALE_SECONDS),
        "clock": lambda value: tenant.local(value).strftime("%-I:%M %p"),
        "type_label": lambda code: owner.type_label(tenant, code),
        "position": owner.position,
        "minute_clock": lambda m: (
            f"{(m // 60) % 12 or 12}:{m % 60:02d} {'AM' if m % 1440 < 720 else 'PM'}"
        ),
        "timedelta": timedelta,
    }


def add_routes(app: FastAPI) -> None:
    app.mount(
        "/owner/static", StaticFiles(directory=HERE / "static" / "owner"), name="owner-static"
    )

    def tenant_for(request: Request, slug: str | None):
        tenants = request.app.state.services.tenants
        return tenants.by_slug.get(slug) if slug else tenants.default

    def day_of(tenant, given: date | None) -> date:
        return given or tenant.local(app.state.services.clock()).date()

    @app.get("/owner", response_class=HTMLResponse)
    def owner_today(
        request: Request, token: str | None = None, day: date | None = None, tenant: str = ""
    ):
        if token is not None:
            if not _valid(request, token):
                return _render(request, "login.html", {"failed": True}, 401)
            # Keep the token out of the address bar and the browser history. A relative URL, so
            # it stays on https behind a TLS proxy.
            rest = request.url.remove_query_params("token")
            target = rest.path + (f"?{rest.query}" if rest.query else "")
            return _remember(request, RedirectResponse(target, 303), token)
        if not _signed_in(request):
            return _login(request)
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        day = day_of(found, day)
        conn = services.connect()
        try:
            data = owner.page(conn, found, day, services.clock())
            context = {**_common(request, found, tenant, "today", conn), **data}
        finally:
            conn.close()
        return _render(request, "today.html", context)

    @app.post("/owner/login")
    def owner_login(request: Request, token: str = Form("")):
        if not _valid(request, token):
            return _render(request, "login.html", {"failed": True}, 401)
        return _remember(request, RedirectResponse("/owner", 303), token)

    @app.get("/owner/calls", response_class=HTMLResponse)
    def owner_calls(request: Request, day: date | None = None, tenant: str = ""):
        if not _signed_in(request):
            return _login(request)
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        day = day_of(found, day)
        conn = services.connect()
        try:
            data = owner.page(conn, found, day, services.clock())
            context = {**_common(request, found, tenant, "calls", conn), **data}
        finally:
            conn.close()
        return _render(request, "calls.html", context)

    @app.get("/owner/calls/{call_id}", response_class=HTMLResponse)
    def owner_call(request: Request, call_id: str, sent: int = 0):
        if not _signed_in(request):
            return _login(request)
        services = request.app.state.services
        conn = services.connect()
        try:
            found = trace.timeline(conn, call_id)
            if found is None:
                return HTMLResponse("No such call.", 404)
            tenant = services.tenants.by_slug[found["call"]["tenant"]]
            slug = "" if tenant is services.tenants.default else tenant.slug
            context = {
                **_common(request, tenant, slug, "calls", conn),
                "trace": found,
                "call": found["call"],
                "page": owner.call_page(conn, tenant, found, services.clock()),
                "thread": plain.conversation(found, tenant),
                "reports": reports.for_call(conn, call_id),
                "sent": bool(sent),
                "max_chars": REPORT_MAX_CHARS,
            }
        finally:
            conn.close()
        return _render(request, "call.html", context)

    @app.post("/owner/calls/{call_id}/report")
    def owner_report(request: Request, call_id: str, text: str = Form(""), turn: str = Form("")):
        """Store what the owner says went wrong on the call (story O-8). The jobs loop files it."""
        if not _signed_in(request):
            return _login(request)
        services = request.app.state.services
        conn = services.connect()
        try:
            found = trace.timeline(conn, call_id)
            if found is None:
                return HTMLResponse("No such call.", 404)
            text = text.strip()[:REPORT_MAX_CHARS]
            if not text:
                return RedirectResponse(f"/owner/calls/{call_id}#report", 303)
            # Keep the line only if the call has it, so a stray value can't fail the write.
            seqs = {e["seq"] for e in found["events"] if e["type"] == "turn"}
            seq = int(turn) if turn.isascii() and turn.isdigit() and len(turn) < 6 else None
            reports.add(conn, call_id, seq if seq in seqs else None, text, services.clock())
        finally:
            conn.close()
        return RedirectResponse(f"/owner/calls/{call_id}?sent=1#report", 303)

    @app.get("/owner/results", response_class=HTMLResponse)
    def owner_results(request: Request, days: int = 1, day: date | None = None, tenant: str = ""):
        if not _signed_in(request):
            return _login(request)
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        days = days if days in owner.PERIODS else 1
        services = request.app.state.services
        day = day_of(found, day)
        conn = services.connect()
        try:
            results = owner.results(conn, found, day, days, services.clock())
            context = {
                **_common(request, found, tenant, "results", conn),
                "day": day,
                "is_today": day == found.local(services.clock()).date(),
                "results": results,
                "ledger": owner.ledger(results["now"], results["before"]),
                "refused_label": lambda code: plain.REFUSED.get(code, (code, ""))[0],
                "reports": reports.recent(conn, found),
            }
        finally:
            conn.close()
        return _render(request, "results.html", context)

    @app.get("/owner/design", response_class=HTMLResponse)
    def owner_design(request: Request):
        """The design system's live specimen: every token and component, from the real CSS."""
        if not _signed_in(request):
            return _login(request)
        services = request.app.state.services
        tenant = services.tenants.default
        conn = services.connect()
        try:
            context = _common(request, tenant, "", "design", conn)
        finally:
            conn.close()
        return _render(request, "design.html", context)
