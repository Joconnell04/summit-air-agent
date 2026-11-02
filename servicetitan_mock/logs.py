"""One JSON line per request, and the read side behind GET /debug/logs."""
import json
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .errors import ApiError, error_body

SKIP_PREFIXES = ("/debug", "/docs", "/openapi.json", "/redoc")
MAX_BODY_CHARS = 4000
MAX_LIMIT = 1000
_write_lock = threading.Lock()


def _parse(raw: bytes, content_type: str, secret_fields: tuple[str, ...] = ()):
    if not raw:
        return None
    text = raw.decode("utf-8", "replace")
    if "json" in content_type:
        try:
            data = json.loads(text)
        except ValueError:
            return text[:MAX_BODY_CHARS]
    elif "x-www-form-urlencoded" in content_type:
        data = dict(parse_qsl(text))
    else:
        return text[:MAX_BODY_CHARS]
    if isinstance(data, dict):
        data = {k: ("***" if k in secret_fields else v) for k, v in data.items()}
    if len(json.dumps(data)) > MAX_BODY_CHARS:
        return json.dumps(data)[:MAX_BODY_CHARS] + "...(truncated)"
    return data


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        if request.url.path.startswith(SKIP_PREFIXES):
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id
            return response

        request_bytes = await request.body()
        started = time.perf_counter()
        try:
            upstream = await call_next(request)
            response_bytes = b"".join([chunk async for chunk in upstream.body_iterator])
            response = Response(
                response_bytes, upstream.status_code,
                {k: v for k, v in upstream.headers.items() if k.lower() != "content-length"},
                upstream.media_type,
            )
        except Exception:  # noqa: BLE001 - any crash becomes the standard 500 body
            response = JSONResponse(
                error_body(request, 500, "server_error", "Unexpected server error."), 500
            )
            response_bytes = response.body
        response.headers["X-Request-Id"] = request_id

        response_body = _parse(response_bytes, response.headers.get("content-type", ""), ("access_token",))
        record = {
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "requestId": request_id,
            "callId": request.headers.get("x-call-id"),
            "method": request.method,
            "path": request.url.path,
            "query": request.url.query or None,
            "status": response.status_code,
            "durationMs": round((time.perf_counter() - started) * 1000, 1),
            "requestBody": _parse(request_bytes, request.headers.get("content-type", ""), ("client_secret",)),
            "responseBody": response_body,
            "error": response_body.get("code") if response.status_code >= 400 and isinstance(response_body, dict) else None,
        }
        with _write_lock, Path(request.app.state.log_path).open("a") as f:  # noqa: ASYNC230 - tiny append
            f.write(json.dumps(record) + "\n")
        return response


def read_logs(path: str | Path, *, call_id=None, request_id=None, errors_only=False, limit=100) -> list[dict]:
    if not 1 <= limit <= MAX_LIMIT:
        raise ApiError(422, "validation_error", f"limit must be from 1 to {MAX_LIMIT}.",
                       {"limit": "out of range"})
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a half-written line from a crash must not hide every other entry
        if call_id and row["callId"] != call_id:
            continue
        if request_id and row["requestId"] != request_id:
            continue
        if errors_only and row["status"] < 400:
            continue
        rows.append(row)
    return rows[-limit:]
