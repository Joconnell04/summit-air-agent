"""One JSON error shape for every failure, ServiceTitan's problem-details body.

{"type": "...", "title": "...", "status": 409, "traceId": "...", "errors": {"field": ["..."]}, "data": {}}

The field names are the real ones (SOURCE: the `ApiErrorResponse` schema in every ServiceTitan
OpenAPI document, docs/explorations/006-servicetitan-field-names.md). Not in the real API: a short
machine code in `data.code` (for example `slot_taken`), kept so the mock's own tests can tell two
409s apart. The adapter never reads it.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# ASP.NET problem-details type links, by status.
TYPES = {
    400: "https://tools.ietf.org/html/rfc7231#section-6.5.1",
    401: "https://tools.ietf.org/html/rfc7235#section-3.1",
    403: "https://tools.ietf.org/html/rfc7231#section-6.5.3",
    404: "https://tools.ietf.org/html/rfc7231#section-6.5.4",
    405: "https://tools.ietf.org/html/rfc7231#section-6.5.5",
    409: "https://tools.ietf.org/html/rfc7231#section-6.5.8",
    422: "https://tools.ietf.org/html/rfc4918#section-11.2",
    500: "https://tools.ietf.org/html/rfc7231#section-6.6.1",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str, errors: dict | None = None):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.errors = errors or {}


def error_body(
    request: Request, status: int, code: str, detail: str, errors: dict | None = None
) -> dict:
    return {
        "type": TYPES.get(status, TYPES[500]),
        "title": detail,
        "status": status,
        "traceId": getattr(request.state, "request_id", None),
        "errors": {field: [message] for field, message in (errors or {}).items()},
        "data": {"code": code},
    }


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(
            error_body(request, exc.status, exc.code, exc.detail, exc.errors), exc.status
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = {".".join(str(p) for p in e["loc"][1:]) or "body": e["msg"] for e in exc.errors()}
        return JSONResponse(
            error_body(request, 400, "validation_error", "The request was not valid.", errors), 400
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(
            error_body(request, exc.status_code, code, str(exc.detail)), exc.status_code
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        return JSONResponse(
            error_body(request, 500, "server_error", "Unexpected server error."), 500
        )
