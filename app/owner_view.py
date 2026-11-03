"""The owner view: one read-only page for the owner and dispatcher, and each call's timeline. The
numbers come from app/owner.py; this module only handles the login token and renders HTML.

The token is OWNER_TOKEN. Opening /owner?token=... once sets a cookie and drops the token from the
address bar; without either, the page shows a one-field form. An empty OWNER_TOKEN refuses all.
"""

import hmac
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import owner, trace

COOKIE = "owner_token"
templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))


def _valid(request: Request, given: str | None) -> bool:
    token = request.app.state.services.settings.owner_token
    return bool(token and given and hmac.compare_digest(given, token))


def _clock(value: datetime, tenant) -> str:
    return tenant.local(value).strftime("%-I:%M %p")


def _render(request: Request, name: str, context: dict, status: int = 200) -> Response:
    return templates.TemplateResponse(request, name, context, status_code=status)


def add_routes(app: FastAPI) -> None:
    def tenant_for(request: Request, slug: str | None):
        tenants = request.app.state.services.tenants
        return tenants.by_slug.get(slug) if slug else tenants.default

    @app.get("/owner", response_class=HTMLResponse)
    def owner_page(
        request: Request, token: str | None = None, day: date | None = None, tenant: str = ""
    ):
        if token is not None:
            if not _valid(request, token):
                return _render(request, "login.html", {"failed": True}, 401)
            # Keep the token out of the address bar and the browser history.
            response = RedirectResponse(request.url.remove_query_params("token"), 303)
            response.set_cookie(COOKIE, token, httponly=True, samesite="strict")
            return response
        if not _valid(request, request.cookies.get(COOKIE)):
            return _render(request, "login.html", {"failed": False}, 401)
        services = request.app.state.services
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        now = services.clock()
        day = day or found.local(now).date()
        conn = services.connect()
        try:
            data = owner.page(conn, found, day, now)
        finally:
            conn.close()
        context = {
            **data,
            "slug": tenant,
            "now": now,
            "timedelta": timedelta,
            "clock": lambda value: _clock(value, found),
            "type_label": lambda code: owner.type_label(found, code),
        }
        return _render(request, "owner.html", context)

    @app.post("/owner/login")
    def owner_login(request: Request, token: str = Form("")):
        if not _valid(request, token):
            return _render(request, "login.html", {"failed": True}, 401)
        response = RedirectResponse("/owner", 303)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict")
        return response

    @app.get("/owner/calls/{call_id}", response_class=HTMLResponse)
    def owner_call(request: Request, call_id: str):
        if not _valid(request, request.cookies.get(COOKIE)):
            return _render(request, "login.html", {"failed": False}, 401)
        services = request.app.state.services
        conn = services.connect()
        try:
            found = trace.timeline(conn, call_id)
        finally:
            conn.close()
        if found is None:
            return HTMLResponse("No such call.", 404)
        tenant = services.tenants.by_slug[found["call"]["tenant"]]
        started = tenant.local(datetime.fromisoformat(found["call"]["started_at"]))
        context = {
            "trace": found,
            "tenant": tenant.name,
            "day": started.date().isoformat(),
            "clock": lambda value: tenant.local(datetime.fromisoformat(value)).strftime(
                "%-I:%M:%S %p"
            ),
        }
        return _render(request, "call.html", context)
