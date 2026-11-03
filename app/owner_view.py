"""The owner view: three read-only pages for the owner and dispatcher (Today, Calls, Results), each
call's page, the one write (a report to Revin, story O-8), the Rules page and its editor, and the
design system's live specimen. The numbers come from app/owner.py, the plain notes from
app/plain.py, the rulebook from app/rulebook.py and owner edits from app/rules.py; this module
handles the login tokens and renders HTML. Design: docs/design-system.md.

The read token is OWNER_TOKEN. Opening /owner?token=... once sets a cookie and drops the token from
the address bar; without either, the page shows a one-field form. An empty OWNER_TOKEN refuses all.
Editing rules takes a separate code per person (RULES_EDITORS); the owner token stays read-only,
and an edit code also opens the read-only pages.
"""

import hmac
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import owner, plain, reports, rulebook, rules, search, trace
from .db import parse_iso
from .settings import REPORT_MAX_CHARS, WORKER_STALE_SECONDS

COOKIE = "owner_token"
EDIT_COOKIE = "owner_edit"
RULE_FILTERS = ("all", "editable", "locked")
COOKIE_DAYS = 30
SEARCH_MAX_CHARS = 80
HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


def _valid(request: Request, given: str | None) -> bool:
    token = request.app.state.services.settings.owner_token
    return bool(token and given and hmac.compare_digest(given.encode(), token.encode()))


def _editor(request: Request) -> str | None:
    """The name behind the edit code in the cookie, or None. Compared against every code so the
    time taken says nothing about which one matched."""
    given = request.cookies.get(EDIT_COOKIE) or ""
    found = None
    for code, name in request.app.state.services.settings.editors().items():
        if given and hmac.compare_digest(given.encode(), code.encode()):
            found = name
    return found


def _remember(request: Request, response: Response, token: str, cookie: str = COOKIE) -> Response:
    # Secure when served over HTTPS, so the cookie never travels in clear text there.
    response.set_cookie(
        cookie,
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
    return _valid(request, request.cookies.get(COOKIE)) or _editor(request) is not None


def _login(request: Request) -> Response:
    return _render(request, "login.html", {"failed": False}, 401)


def _edit_login(request: Request, failed: bool = False) -> Response:
    context = {
        "failed": failed,
        "title": "Edit rules",
        "label": "Edit code",
        "action": "/owner/rules/login",
    }
    return _render(request, "login.html", context, 401)


def _start_gate(services) -> None:
    """Run the eval gate in its own process, so a publish answers at once and the check takes as
    long as it takes. Its output goes beside the server's log."""
    if not services.settings.rules_gate:
        return
    log = open(services.settings.log_path.with_suffix(".gate.log"), "a")  # noqa: SIM115
    subprocess.Popen(
        [sys.executable, "-m", "evals.gate", "--db", str(services.settings.db_path)],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()


def _edit_form(form) -> tuple[dict, str]:
    """A draft's content and note from the edit form: line.<key> and guide.<stage>.<n> fields.
    Any field name is read, so a locked key sent by hand reaches validation and is refused there."""
    content: dict = {"lines": {}, "guidance": {}}
    for name, value in form.multi_items():
        kind, _, rest = name.partition(".")
        if kind == "line" and rest:
            content["lines"][rest] = value
        elif kind == "guide" and rest:
            stage = rest.split(".")[0]
            content["guidance"].setdefault(stage, []).append(value)
        elif kind == "set" and rest:
            # A raw setting, such as set.priority: there is no field for one, so it is refused.
            content[rest] = value
    return content, str(form.get("note", ""))


def _tenant_q(slug: str) -> str:
    return f"?tenant={slug}" if slug else ""


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
            }
        finally:
            conn.close()
        return _render(request, "results.html", context)

    def rules_context(request: Request, tenant, slug: str, conn) -> dict:
        found = rules.live(conn, tenant.slug)
        return {
            **_common(request, tenant, slug, "rules", conn),
            "live": found,
            "versions": rules.history(conn, tenant.slug),
            "local": lambda value: tenant.local(parse_iso(value)),
            "editor": _editor(request),
        }

    @app.get("/owner/rules", response_class=HTMLResponse)
    def owner_rules(request: Request, show: str = "all", tenant: str = ""):
        """Every rule the agent follows, by call stage, each locked or editable (exploration 009)."""
        if not _signed_in(request):
            return _login(request)
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        show = show if show in RULE_FILTERS else "all"
        conn = request.app.state.services.connect()
        try:
            context = rules_context(request, found, tenant, conn)
            book = rulebook.rulebook(found, context["live"]["content"] if context["live"] else {})
            shown = [
                r for r in book
                if show == "all" or (show == "editable") == r.editable
            ]
            context |= {
                "show": show,
                "stages": [s for s in rulebook.by_stage(shown) if s[2]],
                "counts": {
                    "all": len(book),
                    "editable": sum(r.editable for r in book),
                    "locked": sum(not r.editable for r in book),
                },
            }
        finally:
            conn.close()
        return _render(request, "rules.html", context)

    def edit_page(request: Request, found, slug: str, conn, content=None, note: str = "",
                  errors=None, status: int = 200) -> Response:
        context = rules_context(request, found, slug, conn)
        draft = rules.open_draft(conn, found.slug)
        newest = context["versions"][0] if context["versions"] else None
        blocked = newest if newest and newest["status"] == "blocked" else None
        if content is None:
            base = draft or blocked or context["live"]
            content = base["content"] if base else {}
            note = base["note"] if (draft or blocked) else ""
        lines = content.get("lines") or {}
        guidance = content.get("guidance") or {}
        errors = errors or {}
        # Errors with no field on the page, such as a locked rule sent by hand, show at the top.
        fields = {f"lines.{k}" for k in rules.EDITABLE_LINES}
        general = [
            message for key, message in errors.items()
            if key not in fields
            and not any(key.startswith(f"guidance.{s}") for s in rules.GUIDANCE_STAGES)
        ]
        context |= {
            "draft": draft,
            "blocked": blocked,
            "note": note,
            "errors": errors,
            "general": general,
            "lines": [
                (key, label, stage, lines.get(key) or rules.default_line(found, key),
                 rules.default_line(found, key))
                for key, (_where, stage, label) in rules.EDITABLE_LINES.items()
            ],
            "guidance": [
                (stage, rules.STAGES[stage],
                 (list(guidance.get(stage) or []) + [""] * rules.GUIDANCE_PER_STAGE)[
                     : rules.GUIDANCE_PER_STAGE])
                for stage in rules.GUIDANCE_STAGES
            ],
            "max_line": rules.LINE_MAX_CHARS,
            "max_guide": rules.GUIDANCE_MAX_CHARS,
        }
        return _render(request, "rules_edit.html", context, status)

    @app.get("/owner/rules/edit", response_class=HTMLResponse)
    def owner_rules_edit(request: Request, tenant: str = ""):
        if _editor(request) is None:
            return _edit_login(request)
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        conn = request.app.state.services.connect()
        try:
            return edit_page(request, found, tenant, conn)
        finally:
            conn.close()

    @app.post("/owner/rules/login")
    def owner_rules_login(request: Request, token: str = Form("")):
        editors = request.app.state.services.settings.editors()
        if not any(token and hmac.compare_digest(token.encode(), c.encode()) for c in editors):
            return _edit_login(request, failed=True)
        return _remember(request, RedirectResponse("/owner/rules/edit", 303), token, EDIT_COOKIE)

    @app.post("/owner/rules/draft")
    async def owner_rules_draft(request: Request, tenant: str = ""):
        """Save the draft, and with publish=1 send it to the eval gate. Validation is here, on the
        server, whatever the page sent."""
        author = _editor(request)
        if author is None:
            return _edit_login(request)
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        form = await request.form()
        content, note = _edit_form(form)
        conn = services.connect()
        try:
            _saved, errors = rules.save_draft(conn, found, content, author, note, services.clock())
            if errors:
                return edit_page(request, found, tenant, conn, content, note, errors, 422)
            if form.get("publish"):
                try:
                    rules.request_publish(conn, found.slug, author, services.clock())
                except rules.RulesError as err:
                    return edit_page(request, found, tenant, conn, content, note,
                                     {"content": str(err)}, 422)
                _start_gate(services)
        finally:
            conn.close()
        return RedirectResponse(f"/owner/rules/edit{_tenant_q(tenant)}", 303)

    @app.post("/owner/rules/discard")
    def owner_rules_discard(request: Request, tenant: str = ""):
        if _editor(request) is None:
            return _edit_login(request)
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        conn = services.connect()
        try:
            rules.discard_draft(conn, found.slug, services.clock())
        finally:
            conn.close()
        return RedirectResponse(f"/owner/rules/edit{_tenant_q(tenant)}", 303)

    @app.post("/owner/rules/rollback")
    def owner_rules_rollback(request: Request, number: int = Form(...), tenant: str = ""):
        """Make an earlier live version live again, as a new version (nothing is deleted)."""
        author = _editor(request)
        if author is None:
            return _edit_login(request)
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        conn = services.connect()
        try:
            rules.rollback(conn, found.slug, number, author, services.clock())
        except rules.RulesError as err:
            return edit_page(request, found, tenant, conn, errors={"content": str(err)}, status=422)
        finally:
            conn.close()
        return RedirectResponse(f"/owner/rules/edit{_tenant_q(tenant)}", 303)

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
