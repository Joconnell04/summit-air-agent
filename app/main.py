"""The tool server. Run with: uvicorn app.main:create_app --factory --port 8100 (make server).

Every endpoint returns HTTP 200 with the envelope in app/envelope.py, including on bad input, so
the voice layer never hears an error page. Every request is logged (app/logs.py).
"""

import hashlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import db, jobs, lifecycle, logs, records, trace
from .envelope import Refused, Result
from .fsm import FsmUnavailable, SlotCache, make_adapter
from .fsm.base import fsm_timings
from .priority import SideModel
from .schemas import EndIn, HeartbeatIn, StartIn, TurnIn
from .settings import Settings
from .tenant import Tenants
from .tools import TOOLS, Ctx

log = logging.getLogger("app")

# Template fallbacks, said when nothing more specific applies. Company lines are in agent.yaml.
SAY_RETRY = "Sorry, give me just a moment."
SAY_FSM_DOWN = "I can't pull that up right now."
SAY_BAD_INPUT = "Let me check that again."
WORKER_STALE_SECONDS = 120


class Services:
    """Everything a request needs, built once per app."""

    def __init__(
        self,
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
        fsm_clients: dict[str, httpx.Client] | None = None,
        side_model: SideModel | None = None,
    ):
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(UTC))
        db.init(settings.db_path)
        self.tenants = Tenants.load(settings.tenants_dir, settings.default_tenant)
        clients = fsm_clients or {}
        self.fsm = {t.slug: make_adapter(t, settings, clients.get(t.slug)) for t in self.tenants}
        self.caches = {t.slug: SlotCache(settings.slot_cache_seconds) for t in self.tenants}
        self.side_model = side_model
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
    tenant = services.tenants.by_slug[call["tenant"]]
    return Ctx(
        conn,
        call,
        tenant,
        services.fsm[tenant.slug],
        services.caches[tenant.slug],
        services.clock(),
        services.side_model,
        key,
    )


def _tool_route(app: FastAPI, name: str, handler: Callable, model: type[BaseModel], write: bool):
    def endpoint(request: Request, body: model):  # type: ignore[valid-type]
        services: Services = request.app.state.services
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
) -> FastAPI:
    services = Services(settings or Settings(), clock, fsm_clients, side_model)

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
            return lifecycle.start(conn, tenant, services.fsm[tenant.slug], body, services.clock())

        return run(services, request, "calls.start", body.call_id, body.model_dump(), fn)

    @app.post("/calls/{call_id}/turn")
    def call_turn(request: Request, call_id: str, body: TurnIn):
        def fn(conn, key):
            ctx = _ctx(services, conn, call_id, key)
            return lifecycle.turn(conn, ctx.tenant, ctx.call, body, ctx.now)

        return run(services, request, "turn", call_id, body.model_dump(), fn)

    @app.post("/calls/{call_id}/end")
    def call_end(request: Request, call_id: str, body: EndIn):
        def fn(conn, key):
            call = records.get_call(conn, call_id)
            if call is None:
                raise Refused("unknown_call", "")
            tenant = services.tenants.by_slug[call["tenant"]]
            return lifecycle.end(conn, tenant, call, body, services.clock())

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
