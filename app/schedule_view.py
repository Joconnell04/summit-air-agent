"""The schedule pages: the owner view's Schedule tab and job page, and the bench's schedule with
the list of what changed in ServiceTitan. The reads and writes are app/schedule.py; this module
checks who is signed in, reads the form and renders HTML.

Every route needs a signed-in user. Staff may block a window, add an event or a job and change a
job; changing a customer's name or phone number needs an admin, checked here and again in
app/schedule.py. The bench pages need an admin, and so does cancelling a job, which only the bench
offers. A form posts to the owner routes from either surface and says where to go back to; the
edit is logged with the surface it came from. An edit made from the bench also logs what
find_slots offered that day before and after it, which is the proof that a block changes what the
agent can offer.
"""

import json
from datetime import date

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import schedule
from .db import parse_iso, tx
from .fsm import FsmRejected, FsmUnavailable
from .owner_view import _common, _login, _need_admin, _next_path, _render, _signed_in, _user
from .text import phone_key


def _who(request: Request, next_path: str) -> schedule.Who:
    user = _user(request)
    surface = "bench" if next_path.startswith("/bench") else "owner"
    return schedule.Who(user["id"], user["name"], user["role"], surface)


def _result(conn, tenant, user, edit_id: int | None) -> dict | None:
    """The edit a page came back from, read from the edit log, so its outcome never travels in the
    URL: only its id does, and only its author sees it."""
    if user is None or edit_id is None:
        return None
    row = conn.execute(
        "SELECT * FROM schedule_edits WHERE id = ? AND tenant = ? AND user_id = ?",
        (edit_id, tenant.slug, user["id"]),
    ).fetchone()
    if row is None:
        return None
    return {
        "ok": bool(row["ok"]),
        "text": row["detail"] if row["ok"] else row["error"],
        "code": row["code"],
        "pending": json.loads(row["pending_json"]) if row["pending_json"] else None,
    }


def _with_edit(path: str, edit_id: int | None) -> str:
    if edit_id is None:
        return path
    base, _, fragment = path.partition("#")
    joined = f"{base}{'&' if '?' in base else '?'}edit={edit_id}"
    return f"{joined}#{fragment}" if fragment else joined


def _changes(conn, tenant) -> list[dict]:
    """What changed in ServiceTitan, with times in the company's clock to the second."""
    local = lambda value: tenant.local(parse_iso(value)).strftime("%-I:%M:%S %p")
    return [
        {**c, "at_local": local(c["at"]), "landed_local": local(c["landed"]) if c["landed"] else ""}
        for c in schedule.changes(conn, tenant)
    ]


def _day(value: str, fallback: date) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return fallback


def _offer_change(on: date, before: dict, after: dict) -> str:
    """The log's note on what an edit did to the agent's offers that day."""
    return (
        f"; find_slots for {on.strftime('%a %b %-d')}: {before['openings']} openings in "
        f"{before['windows']} windows before, {after['openings']} in {after['windows']} after"
    )


def _int(value: str) -> int | None:
    return int(value) if value.isascii() and value.isdigit() and len(value) < 10 else None


def add_routes(app: FastAPI) -> None:
    def services(request: Request):
        return request.app.state.services

    def page_context(request, tenant, tab, conn) -> dict:
        return {
            **_common(request, tenant, "", tab, conn),
            "result": _result(conn, tenant, _user(request), _int(request.query_params.get("edit", ""))),
            "job_types": [(code, jt["name"]) for code, jt in tenant.job_types.items()],
            "default_type": (schedule.typical_job_type(tenant, "Normal") or {}).get("code", ""),
            "type_name": lambda code: tenant.job_types.get(code, {}).get("name", code),
        }

    def grid(request, tenant, on: date) -> tuple[dict | None, str]:
        svc = services(request)
        try:
            return (
                schedule.day(
                    svc.fsm[tenant.slug], tenant, svc.caches[tenant.slug], on, svc.clock()
                ),
                "",
            )
        except (FsmUnavailable, FsmRejected):
            return None, "ServiceTitan isn't answering, so the schedule can't be shown."

    @app.get("/owner/schedule", response_class=HTMLResponse)
    def owner_schedule(request: Request, day: date | None = None):
        if not _signed_in(request):
            return _login(request)
        svc = services(request)
        tenant = svc.tenants.default
        today = tenant.local(svc.clock()).date()
        on = day or today
        found, problem = grid(request, tenant, on)
        conn = svc.connect()
        try:
            context = {
                **page_context(request, tenant, "schedule", conn),
                "day": on,
                "is_today": on == today,
                "sched": found,
                "problem": problem,
                "here": f"/owner/schedule?day={on.isoformat()}",
            }
        finally:
            conn.close()
        return _render(request, "schedule.html", context)

    @app.get("/bench/schedule", response_class=HTMLResponse)
    def bench_schedule(
        request: Request, day: date | None = None, customer: int | None = None, missing: int = 0
    ):
        refused = _need_admin(request)
        if refused:
            return refused
        svc = services(request)
        tenant = svc.tenants.default
        today = tenant.local(svc.clock()).date()
        on = day or today
        found, problem = grid(request, tenant, on)
        shown = None
        if customer is not None:
            try:
                shown = schedule.customer_detail(svc.fsm[tenant.slug], customer)
            except FsmRejected:
                missing = 1
            except FsmUnavailable:
                problem = problem or "ServiceTitan isn't answering."
        conn = svc.connect()
        try:
            context = {
                **page_context(request, tenant, "bench-schedule", conn),
                "bench": True,
                "day": on,
                "is_today": on == today,
                "sched": found,
                "problem": problem,
                "here": f"/bench/schedule?day={on.isoformat()}",
                "changes": _changes(conn, tenant),
                "offers": schedule.offers(found) if found else None,
                "customer": shown,
                "not_found": bool(missing),
            }
        finally:
            conn.close()
        return _render(request, "bench_schedule.html", context)

    @app.get("/bench/schedule/changes")
    def bench_schedule_changes(request: Request):
        """The list of what changed, as JSON, for the bench page to refresh in place."""
        refused = _need_admin(request)
        if refused:
            return refused
        svc = services(request)
        tenant = svc.tenants.default
        conn = svc.connect()
        try:
            items = _changes(conn, tenant)
        finally:
            conn.close()
        return JSONResponse({"items": items})

    @app.post("/bench/schedule/find")
    def bench_find_customer(request: Request, day: str = Form(""), phone: str = Form("")):
        """Find a customer by phone, then show them by id, so the number stays out of the URL."""
        refused = _need_admin(request)
        if refused:
            return refused
        svc = services(request)
        tenant = svc.tenants.default
        on = _day(day, tenant.local(svc.clock()).date())
        digits = phone_key(phone)
        found = []
        if len(digits) == 10:
            try:
                found = svc.fsm[tenant.slug].customers_by_phone(digits)
            except (FsmUnavailable, FsmRejected):
                found = []
        tail = f"&customer={found[0]['id']}" if found else "&missing=1"
        return RedirectResponse(f"/bench/schedule?day={on.isoformat()}{tail}#customer", 303)

    def offered(request, tenant, on: date | None) -> dict | None:
        if on is None or on == date.min:
            return None
        found, _ = grid(request, tenant, on)
        return schedule.offers(found) if found else None

    def edit(request: Request, next_path: str, run, on: date | None = None) -> Response:
        """Run one edit as the signed-in user, then go back to the page it came from. From the
        bench, with the day the edit is on, the log line also says what find_slots offered that
        day before and after."""
        if not _signed_in(request):
            return _login(request)
        svc = services(request)
        tenant = svc.tenants.default
        target = _next_path(next_path)
        before = offered(request, tenant, on) if target.startswith("/bench") else None
        conn = svc.connect()
        try:
            try:
                run(
                    conn, svc.fsm[tenant.slug], svc.caches[tenant.slug], tenant,
                    _who(request, target), svc.clock(),
                )
            except schedule.EditError:
                pass  # logged with its reason; the page shows it from the log
            row = conn.execute(
                "SELECT max(id) FROM schedule_edits WHERE tenant = ? AND user_id = ?",
                (tenant.slug, _user(request)["id"]),
            ).fetchone()
            after = offered(request, tenant, on) if before is not None else None
            if before is not None and after is not None and row[0] is not None:
                with tx(conn):
                    conn.execute(
                        "UPDATE schedule_edits SET detail = detail || ? WHERE id = ? AND ok = 1",
                        (_offer_change(on, before, after), row[0]),
                    )
        finally:
            conn.close()
        return RedirectResponse(_with_edit(target, row[0]), 303)

    @app.post("/owner/schedule/block")
    def schedule_block(
        request: Request,
        day: str = Form(""),
        window: str = Form(""),
        technician: str = Form(""),
        name: str = Form(""),
        next: str = Form("/owner/schedule"),
    ):
        on = _day(day, date.min)
        return edit(
            request, next,
            lambda conn, fsm, cache, tenant, who, now: schedule.block(
                conn, fsm, cache, tenant, who, now, on, window, _int(technician), name
            ),
            on,
        )

    @app.post("/owner/schedule/blocks/{block_id}/remove")
    def schedule_unblock(
        request: Request,
        block_id: int,
        day: str = Form(""),
        group: str = Form(""),
        next: str = Form("/owner/schedule"),
    ):
        on = _day(day, date.min)
        return edit(
            request, next,
            lambda conn, fsm, cache, tenant, who, now: schedule.unblock(
                conn, fsm, cache, tenant, who, now, block_id, group == "1", on
            ),
            on,
        )

    @app.post("/owner/schedule/jobs")
    def schedule_add_job(
        request: Request,
        day: str = Form(""),
        window: str = Form(""),
        technician: str = Form(""),
        phone: str = Form(""),
        job_type: str = Form(""),
        notes: str = Form(""),
        customer: str = Form(""),
        next: str = Form("/owner/schedule"),
    ):
        on = _day(day, date.min)
        return edit(
            request, next,
            lambda conn, fsm, cache, tenant, who, now: schedule.add_job(
                conn, fsm, cache, tenant, who, now, on, window, phone, job_type, notes,
                _int(technician), _int(customer),
            ),
            on,
        )

    @app.get("/owner/jobs/{job_id}", response_class=HTMLResponse)
    def owner_job(request: Request, job_id: int, back: str = ""):
        """A job and its customer, to change. Opened from the bench, it keeps the bench's tabs and
        asks for an admin like every bench page."""
        back = _next_path(back) if back else "/owner/schedule"
        from_bench = back.startswith("/bench")
        refused = _need_admin(request) if from_bench else None
        if refused:
            return refused
        if not _signed_in(request):
            return _login(request)
        svc = services(request)
        tenant = svc.tenants.default
        try:
            job = schedule.job_detail(svc.fsm[tenant.slug], tenant, job_id, svc.clock())
        except FsmRejected:
            return HTMLResponse("No such job.", 404)
        except FsmUnavailable:
            job = None
        conn = svc.connect()
        try:
            context = {
                **page_context(request, tenant, "bench-schedule" if from_bench else "schedule", conn),
                "bench": from_bench,
                "job": job,
                "job_id": job_id,
                "back": back,
                "here": f"/owner/jobs/{job_id}",
            }
        finally:
            conn.close()
        return _render(request, "job.html", context)

    @app.post("/owner/jobs/{job_id}")
    def owner_job_save(
        request: Request,
        job_id: int,
        window: str = Form(""),
        job_type: str = Form(""),
        notes: str = Form(""),
        street: str = Form(""),
        unit: str = Form(""),
        city: str = Form(""),
        zip: str = Form(""),
        next: str = Form(""),
    ):
        address = {"street": street, "unit": unit, "city": city, "zip": zip}
        return edit(
            request, next or f"/owner/jobs/{job_id}",
            lambda conn, fsm, cache, tenant, who, now: schedule.edit_job(
                conn, fsm, cache, tenant, who, now, job_id, window, job_type, notes,
                address if street or zip else None,
            ),
        )

    @app.post("/bench/schedule/jobs/{job_id}/cancel")
    def bench_cancel_job(
        request: Request, job_id: int, day: str = Form(""), next: str = Form("/bench/schedule")
    ):
        refused = _need_admin(request)
        if refused:
            return refused
        return edit(
            request, next,
            lambda conn, fsm, cache, tenant, who, now: schedule.cancel_job(
                conn, fsm, cache, tenant, who, now, job_id
            ),
            _day(day, date.min),
        )

    @app.post("/owner/customers/{customer_id}")
    def owner_customer_save(
        request: Request,
        customer_id: int,
        name: str = Form(""),
        contact: str = Form(""),
        phone: str = Form(""),
        confirm: str = Form(""),
        next: str = Form("/owner/schedule"),
    ):
        refused = _need_admin(request)
        if refused:
            return refused
        return edit(
            request, next,
            lambda conn, fsm, cache, tenant, who, now: schedule.edit_customer(
                conn, fsm, cache, tenant, who, now, customer_id, name or None, _int(contact),
                phone or None, confirm == "1",
            ),
        )
