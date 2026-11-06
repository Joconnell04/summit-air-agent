"""The FastAPI app. Run with: uvicorn servicetitan_mock.app:create_app --factory"""

import json
import os
import secrets
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Header, Request

from . import logs, worldkit
from .config import DEFAULT_TENANT_DIR, TenantConfig, parse_dt
from .db import add_missing_tables
from .deps import get_conn
from .errors import ApiError, install_handlers
from .routes import crm, dispatch, jpm, memberships, settings
from .routes.settings import CallIn, shape_call
from .seed import reseed

TOKEN_SECONDS = 900  # ServiceTitan tokens last 15 minutes


SHIFTABLE_WORLDS = ("eval", "server-led")


def shifted() -> Callable[[], datetime] | None:
    """The tool server's shifted clock (app/clock.py), read from the same variables: the mock
    must agree with it on the time. Never on the live world."""
    raw = os.environ.get("SUMMIT_CLOCK_OFFSET_H", "").strip()
    if not raw or os.environ.get("SUMMIT_WORLD", "").strip() not in SHIFTABLE_WORLDS:
        return None
    offset = timedelta(hours=float(raw))
    return lambda: datetime.now(UTC) + offset


def create_app(
    db_path: str | Path | None = None,
    tenant_dir: str | Path | None = None,
    log_path: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
    allow_reset: bool | None = None,
    reset_token: str | None = None,
) -> FastAPI:
    """Defaults come from ST_MOCK_DB, ST_MOCK_TENANT and ST_MOCK_NOW (an ISO time with an offset that
    freezes the clock, for repeatable evals). SUMMIT_CLOCK_OFFSET_H shifts it on the eval and
    server-led worlds only (shifted, below; app/clock.py reads the same). POST /debug/reset exists only when ST_MOCK_ALLOW_RESET
    is 1; ST_MOCK_RESET_TOKEN, if set, must come in the X-Debug-Token header."""
    db_path = Path(db_path or os.environ.get("ST_MOCK_DB", "data/servicetitan_mock.db"))
    tenant_dir = tenant_dir or os.environ.get("ST_MOCK_TENANT", DEFAULT_TENANT_DIR)
    if clock is None:
        frozen = os.environ.get("ST_MOCK_NOW")
        clock = (lambda: parse_dt(frozen)) if frozen else (shifted() or (lambda: datetime.now(UTC)))

    add_missing_tables(db_path)
    app = FastAPI(title="ServiceTitan mock", version="0.1.0")
    app.state.cfg = TenantConfig.load(tenant_dir)
    app.state.db_path = db_path
    app.state.log_path = Path(log_path) if log_path else db_path.with_suffix(".log")
    app.state.clock = clock
    app.state.tokens = {}
    app.state.allow_reset = (
        allow_reset if allow_reset is not None else os.environ.get("ST_MOCK_ALLOW_RESET") == "1"
    )
    app.state.reset_token = (
        reset_token if reset_token is not None else os.environ.get("ST_MOCK_RESET_TOKEN", "")
    )
    app.state.tenant_dir = Path(tenant_dir)

    install_handlers(app)
    app.add_middleware(logs.RequestLogMiddleware)
    for module in (crm, memberships, jpm, dispatch, settings):
        for name in (
            "router",
            "equipment_router",
            "marketing_router",
            "jbce_router",
            "telecom_router",
            "telecom_v3_router",
        ):
            if hasattr(module, name):
                app.include_router(getattr(module, name))

    @app.post("/connect/token")
    def token(
        request: Request,
        grant_type: str = Form(...),
        client_id: str = Form(...),
        client_secret: str = Form(...),
    ):
        auth = request.app.state.cfg.auth
        if grant_type != "client_credentials":
            raise ApiError(400, "unsupported_grant_type", "Only client_credentials is supported.")
        if (client_id, client_secret) != (auth["client_id"], auth["client_secret"]):
            raise ApiError(400, "invalid_client", "Unknown client_id or wrong client_secret.")
        value = secrets.token_urlsafe(24)
        request.app.state.tokens[value] = time.time() + TOKEN_SECONDS
        return {"access_token": value, "token_type": "Bearer", "expires_in": TOKEN_SECONDS}

    @app.get("/health")
    def health(request: Request):
        return {
            "status": "ok",
            "tenantId": request.app.state.cfg.tenant_id,
            "now": request.app.state.clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    @app.get("/debug/logs")
    def debug_logs(
        request: Request,
        callId: str | None = None,
        requestId: str | None = None,
        errors: bool = False,
        limit: int = 100,
    ):
        """Every request and response, filtered. Open to anyone; it is a developer tool."""
        rows = logs.read_logs(
            request.app.state.log_path,
            call_id=callId,
            request_id=requestId,
            errors_only=errors,
            limit=limit,
        )
        return {"count": len(rows), "data": rows}

    @app.post("/debug/calls")
    def debug_log_call(
        body: CallIn, request: Request, conn: sqlite3.Connection = Depends(get_conn)
    ):
        """Log a call the way ServiceTitan's phone system does when a call happens. Not in the real
        API, where the phone system writes the call log and nothing else can. It stands in for the
        phone system in the mock, so a callback the agent placed shows up in the call log. A
        repeated sid returns the first record. Open to anyone, like the other /debug endpoints."""
        cfg = request.app.state.cfg
        if body.sid:
            existing = conn.execute("SELECT id FROM calls WHERE sid = ?", (body.sid,)).fetchone()
            if existing:
                return shape_call(
                    conn,
                    cfg,
                    conn.execute("SELECT * FROM calls WHERE id = ?", (existing[0],)).fetchone(),
                )
        conn.execute("BEGIN IMMEDIATE")
        try:
            call_id = worldkit.add_call(
                conn,
                cfg,
                body.receivedOn or request.app.state.clock(),
                body.fromNumber,
                body.customerId,
                body.jobId,
                body.callType,
                body.reason or "",
                body.durationSeconds,
                body.toNumber,
                body.direction,
                body.sid,
                tuple(body.tags),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return shape_call(
            conn, cfg, conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
        )

    @app.get("/debug/scenarios")
    def debug_scenarios(conn: sqlite3.Connection = Depends(get_conn)):
        """Named people and ids the seed script planted, so tests and evals can find them."""
        row = conn.execute("SELECT value FROM meta WHERE key = 'scenarios'").fetchone()
        return json.loads(row["value"]) if row else {}

    @app.post("/debug/reset")
    def debug_reset(request: Request, x_debug_token: str | None = Header(None)):
        """Rebuild the seeded world in place, so an eval run starts without the bookings earlier
        runs left. Refused unless the process was started with ST_MOCK_ALLOW_RESET=1, which only
        the eval instance sets; the live mock never does."""
        state = request.app.state
        if not state.allow_reset:
            raise ApiError(403, "reset_disabled", "This mock does not allow resets.")
        if state.reset_token and not secrets.compare_digest(x_debug_token or "", state.reset_token):
            raise ApiError(403, "reset_forbidden", "Missing or wrong X-Debug-Token.")
        today = state.clock().astimezone(state.cfg.tz).date()
        scenarios = reseed(state.db_path, state.tenant_dir, today)
        return {"reset": True, "startDate": today.isoformat(), "scenarios": sorted(scenarios)}

    return app
