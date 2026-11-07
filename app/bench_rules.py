"""The Rules page on the debug bench (exploration 018, DECISIONS.md 055 and 091): the owner's own
rules, the fixed responses they can't override (app/fixed.py), and the system prompt a call gets,
section by section from prompts/assemble.py. An admin drafts the rules and the few lines the owner
may reword (app/rules.py); publishing runs the must-pass eval gate (evals/gate.py) by default. An
override publishes at once without the gate; the version records who skipped it, when and why, and
the server log gets a line for it. The owner view has no rules page.

Every route needs an admin (app/bench.py require_admin).
"""

import subprocess
import sys
import uuid

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from prompts import assemble

from . import bench_data, fixed, logs, rules
from .bench import require_admin
from .db import parse_iso
from .owner_view import _common, _render, _tenant_q, _user

EDITOR = "/bench/rules"
# What each part of the prompt is for, from prompts/README.md.
JOBS = {
    "identity.md": "Who it is and how it sounds",
    "spine.md": "The steps every call shares",
    "journeys.md": "Each kind of caller",
    "house-guidance.md": "Your opening and whole-call rules",
    "tool-use.md": "Reading the tools' answers",
    "safety.md": "Around the safety lines",
    "Company knowledge": "The company's own facts",
    "This call": "A sample caller, not a real one",
}


def start_gate(services) -> None:
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


def edit_form(form) -> tuple[dict, str]:
    """A draft's content and note from the page: line.<key> for a reworded line, and rule.<n> with
    when.<n> (its stage) for each of the owner's rules, less the one named by remove. Any field
    name is read, so a locked key sent by hand reaches validation and is refused there."""
    content: dict = {"lines": {}, "guidance": {}}
    stages = {}
    texts = {}
    for name, value in form.multi_items():
        kind, _, rest = name.partition(".")
        if kind == "line" and rest:
            content["lines"][rest] = value
        elif kind == "rule" and rest:
            texts[rest] = str(value)
        elif kind == "when" and rest:
            stages[rest] = str(value)
        elif kind == "set" and rest:
            # A raw setting, such as set.priority: there is no field for one, so it is refused.
            content[rest] = value
    removed = str(form.get("remove", ""))
    for n, text in texts.items():
        if n != removed and text.strip():
            content["guidance"].setdefault(stages.get(n, ""), []).append(text)
    return content, str(form.get("note", ""))


def sample_call(tenant, content: dict) -> dict:
    """What /calls/start returns for a new caller with no history, with the draft's rules: the
    call context the page's prompt is built on, and labelled there as a sample."""
    return {
        "tenant": {"slug": tenant.slug, "name": tenant.name, "timezone": str(tenant.tz),
                   "arrival_question": tenant.arrival_question},
        "now": None,  # the page's own clock
        "after_hours": False,
        "caller": {"phone": "+12125550100", "known_number": False, "do_not_call": False},
        "customers": [],
        "fsm_available": True,
        "linked_call": None,
        "preferred_name": None,
        "offer_callback": False,
        "knowledge": tenant.knowledge,
        "house_rules": {"version": None, "guidance": rules.applied(tenant, content)["guidance"],
                        "lines": {}},
        "pace": None,
        "recording": None,
        "recording_notice": None,
    }


def log_override(services, now):
    """What writes the server's log line (and its /debug/logs row) for a version published
    without the check. rules.publish_now calls it inside its transaction."""

    def record(conn, version: dict) -> None:
        write_override_line(services, conn, version, now)

    return record


def write_override_line(services, conn, version: dict, now) -> None:
    gate = version["gate"] or {}
    line = logs.record(
        now=now,
        request_id=uuid.uuid4().hex[:12],
        call_id=None,
        tool="rules.override",
        args={"tenant": version["tenant"], "version": version["number"],
              "by": gate.get("by"), "reason": gate.get("reason")},
        body={"ok": True, "say": "", "data": {"published_without_check": True}, "refused": None},
        gate="override",
        ms=0.0,
        fsm=[],
        error=None,
        replayed=False,
    )
    logs.write(services.settings.log_path, conn, line)


def add_routes(app: FastAPI) -> None:
    def tenant_for(request: Request, slug: str | None):
        tenants = request.app.state.services.tenants
        return tenants.by_slug.get(slug) if slug else tenants.default

    def edit_page(request: Request, found, slug: str, conn, content=None, errors=None,
                  status: int = 200) -> Response:
        rules.expire_checks(conn, found.slug, request.app.state.services.clock())
        versions = rules.history(conn, found.slug)
        live = rules.live(conn, found.slug)
        draft = rules.open_draft(conn, found.slug)
        newest = versions[0] if versions else None
        blocked = newest if newest and newest["status"] == "blocked" else None
        if content is None:
            base = draft or blocked or live
            content = base["content"] if base else {}
        lines = content.get("lines") or {}
        guidance = content.get("guidance") or {}
        if not isinstance(lines, dict) or not isinstance(guidance, dict):
            lines, guidance = {}, {}
        errors = errors or {}
        owner_rules = [
            (stage, n, text, errors.get(f"guidance.{stage}.{n}"))
            for stage in rules.GUIDANCE_STAGES
            if isinstance(guidance.get(stage), list)
            for n, text in enumerate(guidance[stage])
        ]
        # Errors with no field on the page, such as a locked rule sent by hand, show at the top.
        shown = {f"lines.{k}" for k in rules.EDITABLE_LINES} | {
            f"guidance.{stage}.{n}" for stage, n, _text, _err in owner_rules
        }
        general = [message for key, message in errors.items() if key not in shown]
        topics = fixed.responses(found, content)
        # The tab to open: the first topic with a refused line in it, else the first topic.
        picked = next(
            (key for key, _name, rows in topics
             if any(r.editable and f"lines.{r.editable}" in errors for r in rows)),
            topics[0][0],
        )
        services = request.app.state.services
        context = {
            **_common(request, found, slug, "bench-rules", conn),
            "bench": True,
            "live": live,
            "versions": versions,
            "local": lambda value: found.local(parse_iso(value)),
            "draft": draft,
            "blocked": blocked,
            "errors": errors,
            "general": general,
            "owner_rules": owner_rules,
            "room": len(owner_rules) < rules.GUIDANCE_PER_STAGE * len(rules.GUIDANCE_STAGES),
            "stages": [(s, rules.STAGES[s]) for s in rules.GUIDANCE_STAGES],
            "topics": topics,
            "picked": picked,
            "values": {
                key: lines.get(key) or rules.default_line(found, key) for key in rules.EDITABLE_LINES
            },
            "defaults": {key: rules.default_line(found, key) for key in rules.EDITABLE_LINES},
            "sections": assemble.sections(sample_call(found, content), services.clock()),
            "prompt_versions": bench_data.prompt_versions(),
            "jobs": JOBS,
            "max_line": rules.LINE_MAX_CHARS,
            "max_guide": rules.GUIDANCE_MAX_CHARS,
            "max_reason": rules.OVERRIDE_MAX_CHARS,
        }
        return _render(request, "bench_rules.html", context, status)

    def back(slug: str) -> Response:
        return RedirectResponse(f"{EDITOR}{_tenant_q(slug)}", 303)

    @app.get(EDITOR, response_class=HTMLResponse)
    def bench_rules(request: Request, tenant: str = ""):
        denied = require_admin(request)
        if denied:
            return denied
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        conn = request.app.state.services.connect()
        try:
            return edit_page(request, found, tenant, conn)
        finally:
            conn.close()

    @app.post(f"{EDITOR}/draft")
    async def bench_rules_draft(request: Request, tenant: str = ""):
        """Save the draft; with publish=1 send it to the eval gate, and with override=1 publish it
        at once (a reason is required). Validation and the guidance check run on every save, on
        the server, whatever the page sent."""
        denied = require_admin(request)
        if denied:
            return denied
        author = _user(request)["name"]
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        form = await request.form()
        content, note = edit_form(form)
        conn = services.connect()
        try:
            if not note.strip():
                current = rules.live(conn, found.slug)
                note = rules.describe(found, content, current["content"] if current else None)
            _saved, errors = rules.save_draft(
                conn, found, content, author, note, services.clock(), services.guidance_check
            )
            if errors:
                return edit_page(request, found, tenant, conn, content, errors, 422)
            try:
                if form.get("override"):
                    now = services.clock()
                    rules.publish_now(conn, found.slug, author, str(form.get("reason", "")), now,
                                      record=log_override(services, now))
                elif form.get("publish"):
                    rules.request_publish(conn, found.slug, author, services.clock())
                    start_gate(services)
            except rules.RulesError as err:
                return edit_page(request, found, tenant, conn, content,
                                 {"content": str(err)}, 422)
            except OSError:
                return edit_page(request, found, tenant, conn, content,
                                 {"content": "The override couldn't be logged, so nothing was "
                                             "published. The draft is saved."}, 503)
        finally:
            conn.close()
        return back(tenant)

    @app.post(f"{EDITOR}/discard")
    def bench_rules_discard(request: Request, tenant: str = ""):
        denied = require_admin(request)
        if denied:
            return denied
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        conn = services.connect()
        try:
            rules.discard_draft(conn, found.slug, services.clock())
        finally:
            conn.close()
        return back(tenant)

    @app.post(f"{EDITOR}/cancel")
    def bench_rules_cancel(request: Request, tenant: str = ""):
        """Stop waiting for a check and get the draft back; a late result is refused."""
        denied = require_admin(request)
        if denied:
            return denied
        found = tenant_for(request, tenant)
        if found is None:
            return HTMLResponse("No such company.", 404)
        services = request.app.state.services
        conn = services.connect()
        try:
            rules.cancel_check(conn, found.slug, services.clock())
        except rules.RulesError as err:
            return edit_page(request, found, tenant, conn, errors={"content": str(err)}, status=422)
        finally:
            conn.close()
        return back(tenant)

    @app.post(f"{EDITOR}/rollback")
    def bench_rules_rollback(request: Request, number: int = Form(...), tenant: str = ""):
        """Make an earlier live version live again, as a new version (nothing is deleted)."""
        denied = require_admin(request)
        if denied:
            return denied
        author = _user(request)["name"]
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
        return back(tenant)
