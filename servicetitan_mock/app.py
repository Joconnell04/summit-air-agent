"""The FastAPI app. Run with: uvicorn servicetitan_mock.app:create_app --factory"""
import json
import os
import secrets
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request

from . import logs
from .config import TenantConfig, parse_dt
from .deps import get_conn
from .errors import ApiError, install_handlers
from .routes import crm, dispatch, jpm, memberships, settings

TOKEN_SECONDS = 900  # ServiceTitan tokens last 15 minutes


def create_app(
    db_path: str | Path | None = None,
    tenant_dir: str | Path | None = None,
    log_path: str | Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Defaults come from ST_MOCK_DB, ST_MOCK_TENANT and ST_MOCK_NOW (an ISO time with an offset that
    freezes the clock, for repeatable evals)."""
    db_path = Path(db_path or os.environ.get("ST_MOCK_DB", "data/servicetitan_mock.db"))
    tenant_dir = tenant_dir or os.environ.get("ST_MOCK_TENANT", "tenants/summit-air")
    if clock is None:
        frozen = os.environ.get("ST_MOCK_NOW")
        clock = (lambda: parse_dt(frozen)) if frozen else (lambda: datetime.now(UTC))

    app = FastAPI(title="ServiceTitan mock", version="0.1.0")
    app.state.cfg = TenantConfig.load(tenant_dir)
    app.state.db_path = db_path
    app.state.log_path = Path(log_path) if log_path else db_path.with_suffix(".log")
    app.state.clock = clock
    app.state.tokens = {}

    install_handlers(app)
    app.add_middleware(logs.RequestLogMiddleware)
    for module in (crm, memberships, jpm, dispatch, settings):
        for name in ("router", "equipment_router", "marketing_router", "telecom_router"):
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
        return {"status": "ok", "tenantId": request.app.state.cfg.tenant_id,
                "now": request.app.state.clock().strftime("%Y-%m-%dT%H:%M:%SZ")}

    @app.get("/debug/logs")
    def debug_logs(
        request: Request,
        callId: str | None = None,
        requestId: str | None = None,
        errors: bool = False,
        limit: int = 100,
    ):
        """Every request and response, filtered. Open to anyone; it is a developer tool."""
        rows = logs.read_logs(request.app.state.log_path, call_id=callId, request_id=requestId,
                              errors_only=errors, limit=limit)
        return {"count": len(rows), "data": rows}

    @app.get("/debug/scenarios")
    def debug_scenarios(conn: sqlite3.Connection = Depends(get_conn)):
        """Named people and ids the seed script planted, so tests and evals can find them."""
        row = conn.execute("SELECT value FROM meta WHERE key = 'scenarios'").fetchone()
        return json.loads(row["value"]) if row else {}

    return app
