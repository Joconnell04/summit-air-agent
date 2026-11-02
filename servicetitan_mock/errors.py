"""One JSON error shape for every failure.

{"status": 409, "code": "slot_taken", "title": "...", "detail": "...", "errors": {}, "requestId": "..."}

The shape is modeled on ServiceTitan's problem-details bodies; the exact real shape is ASSUMED.
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

TITLES = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    422: "Rejected by a business rule",
    500: "Server error",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str, errors: dict | None = None):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.errors = errors or {}


def error_body(request: Request, status: int, code: str, detail: str, errors: dict | None = None) -> dict:
    return {
        "status": status,
        "code": code,
        "title": TITLES.get(status, "Error"),
        "detail": detail,
        "errors": errors or {},
        "requestId": getattr(request.state, "request_id", None),
    }


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(error_body(request, exc.status, exc.code, exc.detail, exc.errors), exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = {".".join(str(p) for p in e["loc"][1:]) or "body": e["msg"] for e in exc.errors()}
        return JSONResponse(
            error_body(request, 400, "validation_error", "The request was not valid.", errors), 400
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(error_body(request, exc.status_code, code, str(exc.detail)), exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        return JSONResponse(error_body(request, 500, "server_error", "Unexpected server error."), 500)
