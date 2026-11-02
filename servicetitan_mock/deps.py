import sqlite3
import time
from collections.abc import Iterator
from datetime import datetime

from fastapi import Header, Request

from .config import TenantConfig
from .db import connect
from .errors import ApiError


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_cfg(request: Request) -> TenantConfig:
    return request.app.state.cfg


def get_now(request: Request) -> datetime:
    return request.app.state.clock()


def authorize(
    request: Request,
    tenant_id: int,
    authorization: str | None = Header(None),
    st_app_key: str | None = Header(None),
) -> None:
    """Bearer token plus ST-App-Key, as ServiceTitan requires. Applied to every tenant route."""
    state = request.app.state
    if tenant_id != state.cfg.tenant_id:
        raise ApiError(404, "unknown_tenant", f"No tenant {tenant_id} on this server.")
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not token or state.tokens.get(token, 0) <= time.time():
        raise ApiError(
            401, "unauthorized", "Missing or unknown bearer token. Request one at /connect/token."
        )
    if st_app_key != state.cfg.auth["app_key"]:
        raise ApiError(403, "invalid_app_key", "Missing or wrong ST-App-Key header.")


def paging(page: int = 1, pageSize: int = 50) -> tuple[int, int]:
    if page < 1 or not 1 <= pageSize <= 200:
        raise ApiError(400, "validation_error", "page must be 1 or more and pageSize 1 to 200.")
    return page, pageSize
