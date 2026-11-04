"""The tool server. Run with: uvicorn app.main:create_app --factory --port 8100 (make server).

Every endpoint returns HTTP 200 with the envelope in app/envelope.py, including on bad input, so
the voice layer never hears an error page. Every request is logged (app/logs.py).
"""

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import (
    accounts,
    address_as,
    bench,
    bench_rules,
    cards,
    db,
    geocode,
    jobs,
    lifecycle,
    logs,
    outbound,
    owner_view,
    recordings,
    records,
    reports,
    rules,
    summary,
    trace,
    transfer,
    weather,
)
from . import side_model as side_models
from .envelope import Refused, Result
from .fsm import FsmUnavailable, SlotCache, make_adapter
from .fsm.base import fsm_timings
from .guidance_check import Check
from .guidance_check import check as check_guidance
from .mail import Mailer
from .priority import SideModel
from .schemas import (
    EndIn,
    HandoffDoneIn,
    HeartbeatIn,
    OutboundClaimIn,
    OutboundDoneIn,
    RecordingIn,
    StartIn,
    TransferDoneIn,
    TransferIn,
    TurnIn,
)
from .settings import WORKER_STALE_SECONDS, Settings
from .tenant import Tenants
from .tools import TOOLS, Ctx, attach_late_job_notes, demand_handoff, needs_on_call_tonight

log = logging.getLogger("app")

# Template fallbacks, said when nothing more specific applies. Company lines are in agent.yaml.
SAY_RETRY = "Sorry, give me just a moment."
SAY_FSM_DOWN = "I can't pull that up right now."
SAY_BAD_INPUT = "Let me check that again."


class Services:
    """Everything a request needs, built once per app."""

    def __init__(
        self,
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
        fsm_clients: dict[str, httpx.Client] | None = None,
        side_model: SideModel | None = None,
        weather_clients: dict[str, httpx.Client] | None = None,
        summary_model: summary.SummaryModel | None = None,
        guidance_check: Check | None = None,
        geocoder: geocode.Geocoder | None = None,
    ):
        self.settings = settings
        if clock is None and settings.fixed_now:
            # Only for development: a frozen clock would stop bookings, jobs and purges moving.
            if settings.environment != "development":
                raise ValueError("FIXED_NOW is for development only; unset it")
            fixed = datetime.fromisoformat(settings.fixed_now)
            if fixed.tzinfo is None:
                raise ValueError("FIXED_NOW needs a UTC offset, such as 2026-11-03T17:20:00-05:00")
            clock = lambda: fixed
        self.clock = clock or (lambda: datetime.now(UTC))
        db.init(settings.db_path)
        self.tenants = Tenants.load(settings.tenants_dir, settings.default_tenant, settings.trades_dir)
        clients = fsm_clients or {}
        self.fsm = {t.slug: make_adapter(t, settings, clients.get(t.slug)) for t in self.tenants}
        self.caches = {t.slug: SlotCache(settings.slot_cache_seconds) for t in self.tenants}
        self.side_model = side_model or side_models.from_settings(settings.side_model)
        # One address lookup per tenant, or the one given (tests and the eval world).
        self.geocoders = {
            t.slug: geocoder or geocode.from_settings(settings.geocoder, t.address_lookup)
            for t in self.tenants
        }
        self.summary_model = summary_model or (
            summary.model_from_config() if settings.summaries else None
        )
        self.guidance_check = guidance_check or check_guidance
        self.report_poster = reports.github_poster(settings)
        self.weather = weather_clients or (
            {t.slug: weather.client(t.weather) for t in self.tenants} if settings.weather else {}
        )
        self.mailer = Mailer(settings)
        self.bucket = recordings.bucket(settings)
        conn = self.connect()
        try:
            accounts.seed_admin(
                conn,
                settings.owner_admin_username,
                settings.owner_admin_name,
                settings.owner_admin_password,
                self.clock(),
            )
        finally:
            conn.close()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def connect(self):
        return db.connect(self.settings.db_path)

    def lock(self, call_id: str) -> threading.Lock:
        """One request at a time per call, so a retried booking can't race its first attempt.
        This assumes one server process, which is how it is deployed (docs/architecture.md)."""
        with self._locks_guard:
            return self._locks.setdefault(call_id, threading.Lock())

    def release(self, call_id: str) -> None:
        with self._locks_guard:
            self._locks.pop(call_id, None)


def _hash(args: dict) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()


def _replay(conn, key: str, tool: str, call_id: str, args: dict) -> Result | None:
    row = conn.execute(
        "SELECT * FROM idempotency WHERE key = ? AND tool = ?", (key, tool)
    ).fetchone()
    if not row:
        return None
    if row["request_hash"] != _hash(args) or row["call_id"] != call_id:
        raise Refused("idempotency_key_reused", SAY_RETRY)
    body = json.loads(row["response_json"])
    return Result(body["ok"], body["say"], body["data"], body["refused"])


def _remember(conn, key: str, tool: str, call_id: str, args: dict, result: Result, now) -> None:
    with db.tx(conn):
        conn.execute(
            "INSERT OR IGNORE INTO idempotency (key, tool, call_id, request_hash, response_json, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (key, tool, call_id, _hash(args), json.dumps(result.body()), db.iso(now)),
        )


def run(
    services: Services,
    request: Request,
    tool: str,
    call_id: str | None,
    args: dict,
    fn: Callable,
    *,
    write: bool = False,
) -> dict:
    """Run one request: idempotency replay, the handler, refusals into the envelope, the log line."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    key = request.headers.get("idempotency-key") if write else None
    started = time.perf_counter()
    timings: list = []
    token = fsm_timings.set(timings)
    conn = services.connect()
    error, replayed = None, False
    try:
        with services.lock(call_id or request_id):
            try:
                result = _replay(conn, key, tool, call_id, args) if key else None
                replayed = result is not None
                if result is None:
                    result = fn(conn, key)
                    # Store results that changed something, so a retry gets the first answer. A
                    # refusal is not stored: the same request may pass once its gate is met.
                    if key and (result.ok or result.refused == "fsm_unavailable"):
                        _remember(conn, key, tool, call_id, args, result, services.clock())
            except Refused as exc:
                result = exc.result
            except FsmUnavailable as exc:
                error = f"fsm_unavailable: {exc}"
                result = Result(False, SAY_FSM_DOWN, refused="fsm_unavailable")
            except Exception as exc:
                log.exception("%s failed", tool)
                error = f"{type(exc).__name__}: {exc}"
                result = Result(False, SAY_RETRY, refused="server_error")
        body = result.body()
        line = logs.record(
            now=services.clock(),
            request_id=request_id,
            call_id=call_id,
            tool=tool,
            args=args,
            body=body,
            gate=result.gate,
            ms=round((time.perf_counter() - started) * 1000, 1),
            fsm=timings,
            error=error,
            replayed=replayed,
        )
        with db.tx(conn):
            logs.write(services.settings.log_path, conn, line)
    finally:
        fsm_timings.reset(token)
        conn.close()
    return body


def _ctx(services: Services, conn, call_id: str, key: str | None) -> Ctx:
    call = records.get_call(conn, call_id)
    if call is None:
        raise Refused("unknown_call", SAY_RETRY)
    if call["ended_at"]:
        raise Refused("call_ended", "")
    tenant = rules.tenant_for_call(conn, services.tenants.by_slug[call["tenant"]], call_id)
    numbers = services.settings.transfer_numbers
    if numbers is None:
        numbers = os.environ
    return Ctx(
        conn,
        call,
        tenant,
        services.fsm[tenant.slug],
        services.caches[tenant.slug],
        services.clock(),
        services.side_model,
        key,
        numbers,
        services.geocoders.get(tenant.slug),
    )


# How many earlier caller lines a card read aloud can span: one digit a line, with a few "um"s.
CARD_WINDOW_LINES = 24


def redact_caller_line(conn, log_path: Path, call_id: str, text: str) -> str:
    """This caller line with card digits taken out, read together with the caller's lines just
    before it (app/cards.py). Earlier lines that turn out to hold part of the card are rewritten
    in the turns table, the tool log and the JSON log before this line is stored."""
    earlier = records.recent_caller_lines(conn, call_id, CARD_WINDOW_LINES)
    redacted = cards.redact_window([t for _, t in earlier] + [text])
    changes = {
        seq: new for (seq, old), new in zip(earlier, redacted[:-1], strict=True) if new != old
    }
    if changes:
        olds = {old: changes[seq] for seq, old in earlier if seq in changes}
        with db.tx(conn):
            records.rewrite_caller_lines(conn, call_id, changes, olds)
        logs.rewrite_turn_texts(log_path, call_id, olds)
    return redacted[-1]


def _tool_route(app: FastAPI, name: str, handler: Callable, model: type[BaseModel], write: bool):
    def endpoint(request: Request, body: model):  # type: ignore[valid-type]
        services: Services = request.app.state.services
        # Card digits the model copied into an argument are taken out before the tool, the tool
        # log or anything downstream sees them (app/cards.py).
        body = model.model_validate(cards.scrub(body.model_dump(mode="json")))
        return run(
            services,
            request,
            name,
            body.call_id,
            body.model_dump(mode="json"),
            lambda conn, key: handler(_ctx(services, conn, body.call_id, key), body),
            write=write,
        )

    endpoint.__name__ = name
    app.post(f"/tools/{name}", summary=name, description=handler.__doc__ or "")(endpoint)


def _check_debug(request: Request) -> JSONResponse | None:
    token = request.app.state.services.settings.debug_token
    given = request.headers.get("x-debug-token") or request.query_params.get("token")
    if not token or given != token:
        return JSONResponse({"detail": "a debug token is required"}, 401)
    return None


def create_app(
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
    fsm_clients: dict[str, httpx.Client] | None = None,
    side_model: SideModel | None = None,
    guidance_check: Check | None = None,
    geocoder: geocode.Geocoder | None = None,
) -> FastAPI:
    services = Services(
        settings or Settings(),
        clock,
        fsm_clients,
        side_model,
        guidance_check=guidance_check,
        geocoder=geocoder,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        interval = services.settings.jobs_interval_seconds

        def loop():
            while not stop.wait(interval):
                try:
                    jobs.run_once(services)
                except Exception:
                    log.exception("background jobs failed")

        worker = threading.Thread(target=loop, daemon=True) if interval > 0 else None
        if worker:
            worker.start()
        yield
        stop.set()

    app = FastAPI(title="Tool server", version="0.1.0", lifespan=lifespan)
    app.state.services = services

    @app.exception_handler(RequestValidationError)
    async def bad_input(request: Request, exc: RequestValidationError):
        errors = {".".join(str(p) for p in e["loc"][1:]) or "body": e["msg"] for e in exc.errors()}
        body = {
            "ok": False,
            "say": SAY_BAD_INPUT,
            "data": {"errors": errors},
            "refused": "invalid_arguments",
        }
        try:
            raw = json.loads(await request.body() or b"{}")
        except ValueError:
            raw = {}
        raw = cards.scrub(raw)
        call_id = raw.get("call_id") if isinstance(raw, dict) else None
        call_id = call_id or request.path_params.get("call_id")
        conn = services.connect()
        try:
            line = logs.record(
                now=services.clock(),
                request_id=request.headers.get("x-request-id") or uuid.uuid4().hex[:12],
                call_id=call_id if isinstance(call_id, str) else None,
                tool=request.url.path.rsplit("/", 1)[-1],
                args=raw if isinstance(raw, dict) else {},
                body=body,
                gate=None,
                ms=0.0,
                fsm=[],
                error="invalid_arguments",
                replayed=False,
            )
            with db.tx(conn):
                logs.write(services.settings.log_path, conn, line)
        finally:
            conn.close()
        return JSONResponse(body, 200)

    # ---- call lifecycle (the voice layer, not the model) -------------------------------------

    @app.post("/calls/start")
    def call_start(request: Request, body: StartIn):
        def fn(conn, key):
            tenant = services.tenants.for_number(body.dialed_number)
            return lifecycle.start(
                conn,
                tenant,
                services.fsm[tenant.slug],
                body,
                services.clock(),
                record=services.bucket is not None,
            )

        return run(services, request, "calls.start", body.call_id, body.model_dump(), fn)

    @app.post("/calls/{call_id}/turn")
    def call_turn(request: Request, call_id: str, body: TurnIn):
        # Card digits never reach the turns table, the log or the checks (app/cards.py); the
        # turn check sees the marker in their place. A card read across several caller lines is
        # found on the line that completes it, and the earlier lines are rewritten then.
        if body.speaker == "caller":
            conn = services.connect()
            try:
                text = redact_caller_line(conn, services.settings.log_path, call_id, body.text)
            finally:
                conn.close()
        else:
            text = cards.redact(body.text)
        body = body.model_copy(update={"text": text})

        def fn(conn, key):
            ctx = _ctx(services, conn, call_id, key)
            result = lifecycle.turn(conn, ctx.tenant, ctx.call, body, ctx.now)
            # A technician asked for after the visit was booked goes onto that job now.
            if "technician_requested" in result.data.get("hints", []):
                attach_late_job_notes(ctx)
            # A caller who demands a person is sent to one now, but never on a turn that brought
            # the safety lines or the check question: those are said first.
            spoken = result.data.get("safety") or result.data.get("spoken")
            if body.speaker == "caller" and not spoken:
                handed = demand_handoff(ctx, body.text)
                if handed is not None:
                    result.say = handed.say
                    result.data["handoff"] = handed.data["handoff"]
            # Look up a street the caller just said, so the answer is back before the agent asks.
            geocoder = services.geocoders.get(ctx.tenant.slug)
            if body.speaker == "caller" and geocoder:
                geocoder.prefetch_line(body.text)
            # Ask the side model now, so its answer is back before find_slots or book needs it.
            slot = ctx.tenant.priority.get("side_model")
            if body.speaker == "caller" and services.side_model and slot:
                services.side_model.prefetch(slot["question"], records.caller_text(conn, call_id))
            return result

        return run(services, request, "turn", call_id, body.model_dump(), fn)

    @app.post("/calls/{call_id}/transfer")
    def call_transfer(request: Request, call_id: str, body: TransferIn):
        def fn(conn, key):
            ctx = _ctx(services, conn, call_id, key)
            return transfer.plan(
                conn,
                ctx.tenant,
                ctx.call,
                body.flag_id,
                body.callback_id,
                ctx.now,
                needs_on_call_tonight(ctx),
                ctx.numbers,
            )

        return run(services, request, "calls.transfer", call_id, body.model_dump(), fn)

    @app.post("/calls/{call_id}/transfer/{transfer_id}/done")
    def call_transfer_done(request: Request, call_id: str, transfer_id: int, body: TransferDoneIn):
        def fn(conn, key):
            ctx = _ctx(services, conn, call_id, key)
            return transfer.finish(conn, ctx.tenant, ctx.call, transfer_id, body.outcome, ctx.now)

        return run(services, request, "calls.transfer_done", call_id, body.model_dump(), fn)

    @app.post("/calls/{call_id}/handoff/{handoff_id}/done")
    def call_handoff_done(request: Request, call_id: str, handoff_id: int, body: HandoffDoneIn):
        def fn(conn, key):
            ctx = _ctx(services, conn, call_id, key)
            return transfer.finish_handoff(
                conn, ctx.tenant, ctx.call, handoff_id, body.outcome, ctx.now
            )

        return run(services, request, "calls.handoff_done", call_id, body.model_dump(), fn)

    @app.post("/calls/{call_id}/recording")
    def call_recording(request: Request, call_id: str, body: RecordingIn):
        def fn(conn, key):
            ctx = _ctx(services, conn, call_id, key)
            return recordings.started(conn, ctx.call, body.egress_id, body.object_key, ctx.now)

        return run(services, request, "calls.recording", call_id, body.model_dump(), fn)

    @app.post("/calls/{call_id}/end")
    def call_end(request: Request, call_id: str, body: EndIn):
        def fn(conn, key):
            call = records.get_call(conn, call_id)
            if call is None:
                raise Refused("unknown_call", "")
            tenant = services.tenants.by_slug[call["tenant"]]
            ended = lifecycle.end(conn, tenant, call, body, services.clock())
            # What the caller asked to be called goes onto their record for the next call.
            try:
                address_as.save(conn, services.fsm[tenant.slug], call, services.clock())
            except Exception:
                log.exception("saving the preferred name on call %s failed", call_id)
            # A callback the system placed is judged when its call ends (app/outbound.py).
            try:
                outbound.settle_answered(
                    conn, tenant, services.fsm[tenant.slug], services.clock(), call_id
                )
            except Exception:
                log.exception("settling the callback on call %s failed", call_id)
            return ended

        result = run(services, request, "calls.end", call_id, body.model_dump(), fn)
        services.release(call_id)
        return result

    @app.get("/healthz")
    def healthz():
        conn = services.connect()
        try:
            conn.execute("SELECT 1").fetchone()
            database = True
        except Exception:  # noqa: BLE001
            database = False
        now = services.clock()
        workers = {
            r["worker"]: {
                "last_seen": r["at"],
                "stale": db.parse_iso(r["at"]) < now - timedelta(seconds=WORKER_STALE_SECONDS),
            }
            for r in conn.execute("SELECT * FROM heartbeats")
        }
        queued = conn.execute("SELECT COUNT(*) FROM bookings WHERE status = 'queued'").fetchone()[0]
        conn.close()
        fsm = {slug: adapter.health() for slug, adapter in services.fsm.items()}
        data = {"database": database, "fsm": fsm, "workers": workers, "queued_bookings": queued}
        return {"ok": database, "say": "", "data": data, "refused": None}

    @app.post("/healthz/heartbeat")
    def heartbeat(body: HeartbeatIn):
        conn = services.connect()
        with db.tx(conn):
            conn.execute(
                "INSERT INTO heartbeats (worker, at) VALUES (?, ?) "
                "ON CONFLICT (worker) DO UPDATE SET at = excluded.at",
                (body.worker, db.iso(services.clock())),
            )
        conn.close()
        return {"ok": True, "say": "", "data": {"worker": body.worker}, "refused": None}

    # ---- calls the system places (journey 12; the voice layer dials them) ----------------------

    @app.post("/outbound/claim")
    def outbound_claim(body: OutboundClaimIn):
        """The next call to place, or none. Start it with /calls/start using the returned call_id,
        caller_id = phone, direction outbound; open with opening_line. Then report /outbound/{id}/done."""
        conn = services.connect()
        try:
            found = outbound.claim(
                conn, services.tenants.by_slug, services.fsm, services.clock()
            )
        finally:
            conn.close()
        return {"ok": True, "say": "", "data": {"outbound": found}, "refused": None}

    @app.post("/outbound/{outbound_id}/done")
    def outbound_done(outbound_id: int, body: OutboundDoneIn):
        conn = services.connect()
        try:
            row = conn.execute(
                "SELECT tenant FROM outbound WHERE id = ?", (outbound_id,)
            ).fetchone()
            found = (
                outbound.finish(
                    conn,
                    services.tenants.by_slug[row["tenant"]],
                    services.fsm[row["tenant"]],
                    outbound_id,
                    body.answered,
                    body.voicemail,
                    services.clock(),
                )
                if row
                else None
            )
        finally:
            conn.close()
        if found is None:
            return {"ok": False, "say": "", "data": {}, "refused": "invalid_state"}
        return {"ok": True, "say": "", "data": found, "refused": None}

    owner_view.add_routes(app)
    bench.add_routes(app)
    bench_rules.add_routes(app)

    # ---- tools the model can call ------------------------------------------------------------

    for name, (handler, model, write) in TOOLS.items():
        _tool_route(app, name, handler, model, write)

    # ---- debug (needs DEBUG_TOKEN) -----------------------------------------------------------

    @app.get("/debug/calls/{call_id}")
    def debug_call(request: Request, call_id: str):
        denied = _check_debug(request)
        if denied:
            return denied
        conn = services.connect()
        try:
            found = trace.timeline(conn, call_id)
        finally:
            conn.close()
        if found is None:
            return JSONResponse({"detail": f"no call {call_id}"}, 404)
        return found

    @app.get("/debug/logs")
    def debug_logs(
        request: Request,
        call_id: str | None = None,
        request_id: str | None = None,
        tool: str | None = None,
        errors: bool = False,
        limit: int = 100,
    ):
        denied = _check_debug(request)
        if denied:
            return denied
        rows = logs.read(
            services.settings.log_path,
            call_id=call_id,
            request_id=request_id,
            tool=tool,
            errors_only=errors,
            limit=limit,
        )
        return {"count": len(rows), "data": rows}

    return app
