"""The owner view: three read-only pages for the owner and dispatcher (Today, Calls, Results), each
call's page, the report to Revin (story O-8), the Settings page with its users, and the design
system's live specimen. The rules live on the debug bench only (app/bench_rules.py, DECISIONS.md
091); the old Rules address redirects there. The numbers come from app/owner.py, the plain notes
from app/plain.py, settings from app/owner_settings.py and accounts from app/accounts.py; this
module checks who is signed in and renders HTML. Design: docs/design-system.md.

Every page needs a signed-in user. Staff can open every page and send a report; changing a setting
or a user needs an admin, and each of those routes checks the role here, on the server, whatever
the page showed.
"""

import json
import os
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import (
    accounts,
    ambient,
    cost,
    failure_grader,
    owner,
    owner_settings,
    plain,
    recordings,
    reports,
    search,
    trace,
    transfer,
)
from .db import parse_iso
from .settings import REPORT_MAX_CHARS, WORKER_STALE_SECONDS

COOKIE = "owner_session"
# The cookies of the shared read token and the rules edit code, from before accounts. Cleared at
# sign-in; nothing reads them.
OLD_COOKIES = ("owner_token", "owner_edit")
SEARCH_MAX_CHARS = 80
HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


def _user(request: Request):
    """The signed-in user, read once per request."""
    if not hasattr(request.state, "user"):
        services = request.app.state.services
        conn = services.connect()
        try:
            request.state.user = accounts.session_user(
                conn, request.cookies.get(COOKIE), services.clock()
            )
        finally:
            conn.close()
    return request.state.user


def _signed_in(request: Request) -> bool:
    return _user(request) is not None


def _is_admin(request: Request) -> bool:
    user = _user(request)
    return user is not None and user["role"] == "admin"


def _render(request: Request, name: str, context: dict, status: int = 200) -> Response:
    return templates.TemplateResponse(request, name, context, status_code=status)


def _next_path(value: str) -> str:
    """Where to go after signing in: a path on the owner view or the bench, never another site."""
    if value.startswith(("/owner", "/bench")) and not value.startswith("//") and "\\" not in value:
        return value
    return "/owner"


def _login(request: Request, failed: bool = False, username: str = "") -> Response:
    # An old link may still carry the shared token from before accounts; it opens nothing and is
    # dropped, so it doesn't follow the user around.
    rest = request.url.remove_query_params("token")
    here = rest.path + (f"?{rest.query}" if rest.query else "")
    nxt = here if request.method == "GET" and request.url.path != "/owner/login" else "/owner"
    context = {"failed": failed, "next": nxt, "username": username}
    return _render(request, "login.html", context, 401)


def _denied(request: Request) -> Response:
    """A signed-in staff user asked for something only an admin can do."""
    services = request.app.state.services
    conn = services.connect()
    try:
        context = _common(request, services.tenants.default, "", "", conn)
    finally:
        conn.close()
    return _render(request, "denied.html", context, 403)


def _need_admin(request: Request) -> Response | None:
    """None when an admin is signed in; otherwise the response to send instead."""
    if not _signed_in(request):
        return _login(request)
    if not _is_admin(request):
        return _denied(request)
    return None


def _tenant_q(slug: str) -> str:
    return f"?tenant={slug}" if slug else ""


def _amp(slug: str) -> str:
    """What joins one more query field onto _tenant_q."""
    return "&" if slug else "?"


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
        "user": _user(request),
        "is_admin": _is_admin(request),
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
    def owner_today(request: Request, day: date | None = None, tenant: str = ""):
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
    def owner_login(
        request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("")
    ):
        services = request.app.state.services
        conn = services.connect()
        try:
            user = accounts.authenticate(conn, username, password)
            if user is None:
                return _render(
                    request,
                    "login.html",
                    {"failed": True, "next": _next_path(next), "username": username[:40]},
                    401,
                )
            token = accounts.start_session(conn, user["id"], services.clock())
        finally:
            conn.close()
        # A relative URL, so it stays on https behind a TLS proxy.
        response = RedirectResponse(_next_path(next), 303)
        # Secure when served over HTTPS, so the cookie never travels in clear text there.
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=accounts.SESSION_DAYS * 86400,
        )
        for old in OLD_COOKIES:
            response.delete_cookie(old)
        return response

    @app.post("/owner/logout")
    def owner_logout(request: Request):
        conn = request.app.state.services.connect()
        try:
            accounts.end_session(conn, request.cookies.get(COOKIE))
        finally:
            conn.close()
        response = RedirectResponse("/owner", 303)
        response.delete_cookie(COOKIE)
        return response

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
                "recorded": recordings.stored_path(conn, call_id, request.app.state.services.settings.recordings_dir) is not None,
                "reports": reports.for_call(conn, call_id),
                "sent": bool(sent),
                "max_chars": REPORT_MAX_CHARS,
                "check": failure_grader.grade_for(conn, tenant, call_id),
                "check_run": failure_grader.run_for_call(conn, call_id),
                "checks_on": services.grader_model is not None,
            }
        finally:
            conn.close()
        return _render(request, "call.html", context)

    @app.get("/owner/calls/{call_id}/log")
    def owner_call_log(request: Request, call_id: str):
        """The call's full record as JSON, for whoever wants the technical detail behind a page."""
        if not _signed_in(request):
            return _login(request)
        conn = request.app.state.services.connect()
        try:
            found = trace.timeline(conn, call_id)
        finally:
            conn.close()
        if found is None:
            return HTMLResponse("No such call.", 404)
        return Response(
            json.dumps(found, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="call-{call_id}.json"'},
        )

    @app.get("/owner/calls/{call_id}/recording")
    def owner_call_recording(request: Request, call_id: str):
        """The call's audio, for the player on the call page (story O-6)."""
        if not _signed_in(request):
            return _login(request)
        conn = request.app.state.services.connect()
        try:
            path = recordings.stored_path(conn, call_id, request.app.state.services.settings.recordings_dir)
        finally:
            conn.close()
        if path is None:
            return HTMLResponse("No recording for this call.", 404)
        return FileResponse(path, media_type=recordings.MEDIA_TYPE)

    @app.get("/owner/search", response_class=HTMLResponse)
    def owner_search(request: Request, q: str = "", tenant: str = ""):
        if not _signed_in(request):
            return _login(request)
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        q = q.strip()[:SEARCH_MAX_CHARS]
        conn = services.connect()
        try:
            context = {
                **_common(request, found, tenant, "search", conn),
                "query": q,
                "found": search.find(conn, found, q),
                "kinds": search.KINDS,
            }
        finally:
            conn.close()
        return _render(request, "search.html", context)

    @app.get("/owner/search/suggest")
    def owner_search_suggest(request: Request, q: str = "", tenant: str = ""):
        """The search box's matches as it is typed, as JSON."""
        if not _signed_in(request):
            return JSONResponse({"items": []}, 401)
        found = tenant_for(request, tenant)
        if found is None:
            return JSONResponse({"items": []}, 404)
        services = request.app.state.services
        conn = services.connect()
        try:
            items = search.suggest(conn, found, q.strip()[:SEARCH_MAX_CHARS], services.clock())
        finally:
            conn.close()
        return JSONResponse({"items": items})

    @app.post("/owner/flags/{flag_id}/done")
    def owner_flag_done(request: Request, flag_id: int, tenant: str = "", day: date | None = None):
        """Check a row off the Needs attention list: the flag closes, with who did it."""
        if not _signed_in(request):
            return _login(request)
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        conn = services.connect()
        try:
            owner.check_off(conn, found, flag_id, _user(request)["name"], services.clock())
        finally:
            conn.close()
        back = f"{_tenant_q(tenant)}{_amp(tenant)}day={day.isoformat()}" if day else _tenant_q(tenant)
        return RedirectResponse(f"/owner{back}#attention", 303)

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
    def owner_results(
        request: Request,
        days: int = 1,
        day: date | None = None,
        start: date | None = None,
        tenant: str = "",
    ):
        """A day, 7 or 30 days ending with day, or a custom range from start to day."""
        if not _signed_in(request):
            return _login(request)
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        today = found.local(services.clock()).date()
        day = min(day_of(found, day), today)
        custom = start is not None
        capped = False
        if custom:
            start, day = sorted((min(start, today), day))
            days = min((day - start).days + 1, owner.MAX_DAYS)
            capped = days < (day - start).days + 1
        else:
            days = days if days in owner.PERIODS else 1
        conn = services.connect()
        try:
            results = owner.results(conn, found, day, days, services.clock())
            context = {
                **_common(request, found, tenant, "results", conn),
                "day": day,
                "is_today": day == today,
                "today": today,
                "custom": custom,
                "capped": capped,
                "max_days": owner.MAX_DAYS,
                "results": results,
                "ledger": owner.ledger(results["now"], results["before"]),
                "refused_label": lambda code: plain.REFUSED.get(code, (code, ""))[0],
                "reports": reports.recent(conn, found),
                "cost": cost.recommended(conn, found, services.clock()),
            }
            context["cost_now"] = cost.monthly(**{
                k: context["cost"][k] for k in (
                    "calls_per_month", "minutes_per_call", "booked_share", "voice_per_minute",
                    "fixed_monthly", "model_per_call",
                )
            })
        finally:
            conn.close()
        return _render(request, "results.html", context)

    @app.get("/owner/rules")
    def owner_rules(tenant: str = ""):
        """The rules moved to the debug bench (DECISIONS.md 091); old links and bookmarks land there,
        where the bench asks for an admin."""
        return RedirectResponse(f"/bench/rules{_tenant_q(tenant)}", 308)

    def settings_page(request: Request, found, slug: str, conn, errors=None, added=None,
                      remove: int | None = None, saved: str = "", status: int = 200) -> Response:
        values = owner_settings.current(conn, found)
        local = lambda value: found.local(parse_iso(value))
        users = accounts.live_users(conn)
        names = {s.key: s.label for s in owner_settings.SETTINGS}
        changes = [
            {
                "at": row["created_at"],
                "by": row["author"],
                "what": f"{names.get(row['key'], row['key'])} {'on' if row['value'] else 'off'}",
            }
            for row in owner_settings.history(conn, found)
        ]
        changes += [
            {
                "at": row["created_at"],
                "by": row["author"],
                "what": f"Daily call check at {row['at_time']}" if row["enabled"]
                else "Daily call check off",
            }
            for row in failure_grader.schedule_history(conn, found)
        ]
        verbs = {
            "added": lambda c: f"Added {c['user_name']} as {c['detail']}",
            "removed": lambda c: f"Removed {c['user_name']}",
            "role": lambda c: f"Made {c['user_name']} {c['detail']}",
            "password": lambda c: f"New password for {c['user_name']}",
        }
        changes += [
            {"at": c["at"], "by": c["by_name"], "what": verbs[c["action"]](c)}
            for c in accounts.changes(conn)
        ]
        changes.sort(key=lambda c: c["at"], reverse=True)
        context = {
            **_common(request, found, slug, "settings", conn),
            "settings": [(s, values[s.key]) for s in owner_settings.shown(found)],
            "users": users,
            "admins": sum(u["role"] == "admin" for u in users),
            "roles": accounts.ROLE_LABELS,
            "changes": changes[:50],
            "local": local,
            "errors": errors or {},
            "added": added or {},
            "remove": remove,
            "saved": saved,
            "min_password": accounts.PASSWORD_MIN_CHARS,
            "check_schedule": failure_grader.schedule(conn, found),
            "routes": transfer.routes(found, request.app.state.services.settings.transfer_numbers
                                      or os.environ),
        }
        return _render(request, "settings.html", context, status)

    @app.get("/owner/settings", response_class=HTMLResponse)
    def owner_settings_view(request: Request, tenant: str = "", remove: int | None = None,
                            saved: str = ""):
        """The company's settings and its users. Staff see them; only an admin changes them."""
        if not _signed_in(request):
            return _login(request)
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        conn = request.app.state.services.connect()
        try:
            return settings_page(request, found, tenant, conn,
                                 remove=remove if _is_admin(request) else None,
                                 saved=saved if saved in ("settings", "users", "checks") else "")
        finally:
            conn.close()

    @app.post("/owner/settings")
    async def owner_settings_save(request: Request, tenant: str = ""):
        refused = _need_admin(request)
        if refused:
            return refused
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        form = await request.form()
        wanted = {
            name.removeprefix("s."): value == "1"
            for name, value in form.multi_items()
            if name.startswith("s.")
        }
        services = request.app.state.services
        conn = services.connect()
        try:
            changed = owner_settings.change(conn, found, wanted, _user(request)["name"],
                                            services.clock())
            if "background_noise" in changed:
                try:
                    ambient.apply(found, wanted["background_noise"])
                except ambient.AmbientError as err:
                    # Put the switch back, so the page never shows a sound the calls don't have.
                    owner_settings.change(conn, found,
                                          {"background_noise": not wanted["background_noise"]},
                                          _user(request)["name"], services.clock())
                    return settings_page(request, found, tenant, conn, {"settings": str(err)},
                                         status=502)
        except owner_settings.SettingsError as err:
            return settings_page(request, found, tenant, conn, {"settings": str(err)}, status=422)
        finally:
            conn.close()
        return RedirectResponse(f"/owner/settings{_tenant_q(tenant)}{_amp(tenant)}saved=settings",
                                303)

    @app.post("/owner/users")
    def owner_users_add(
        request: Request,
        tenant: str = "",
        username: str = Form(""),
        name: str = Form(""),
        role: str = Form("staff"),
        password: str = Form(""),
    ):
        refused = _need_admin(request)
        if refused:
            return refused
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        conn = services.connect()
        try:
            accounts.add(conn, username, name, role, password, _user(request)["name"],
                         services.clock())
        except accounts.AccountError as err:
            kept = {"username": username[:40], "name": name[:accounts.NAME_MAX_CHARS], "role": role}
            return settings_page(request, found, tenant, conn, err.errors, kept, status=422)
        finally:
            conn.close()
        return RedirectResponse(
            f"/owner/settings{_tenant_q(tenant)}{_amp(tenant)}saved=users#users", 303
        )

    @app.post("/owner/users/{user_id}/role")
    def owner_users_role(request: Request, user_id: int, tenant: str = "", role: str = Form("")):
        refused = _need_admin(request)
        if refused:
            return refused
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        conn = services.connect()
        try:
            accounts.set_role(conn, user_id, role, _user(request)["name"], services.clock())
        except accounts.AccountError as err:
            return settings_page(request, found, tenant, conn, err.errors, status=422)
        finally:
            conn.close()
        return RedirectResponse(
            f"/owner/settings{_tenant_q(tenant)}{_amp(tenant)}saved=users#users", 303
        )

    @app.post("/owner/users/{user_id}/remove")
    def owner_users_remove(request: Request, user_id: int, tenant: str = ""):
        """The second step of Remove; the first only shows Confirm on the row, with no dialog."""
        refused = _need_admin(request)
        if refused:
            return refused
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        me = _user(request)
        conn = services.connect()
        try:
            accounts.remove(conn, user_id, me["id"], me["name"], services.clock())
        except accounts.AccountError as err:
            return settings_page(request, found, tenant, conn, err.errors, status=422)
        finally:
            conn.close()
        return RedirectResponse(
            f"/owner/settings{_tenant_q(tenant)}{_amp(tenant)}saved=users#users", 303
        )

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
