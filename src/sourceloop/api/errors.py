"""One error envelope for everything: {"error": {"code", "message", "request_id"}}. Never echoes request input."""
from __future__ import annotations

import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger("sourceloop.api")

_STATUS_CODES = {400: "BAD_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED",
                 409: "CONFLICT", 413: "PAYLOAD_TOO_LARGE", 422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 501: "NOT_IMPLEMENTED"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str | None = None, headers: dict[str, str] | None = None,
                 details: dict | None = None):
        super().__init__(code)
        self.status, self.code, self.message, self.headers = status, code, message or code.replace("_", " ").lower(), headers
        self.details = details  # optional machine-readable payload (e.g. a Studio validation report); never request input


def request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if rid is None:
        rid = request.state.request_id = str(uuid.uuid4())
    return rid


def envelope(request: Request, status: int, code: str, message: str, headers: dict[str, str] | None = None,
             details: dict | None = None) -> JSONResponse:
    h = dict(headers or {})
    h["X-Request-ID"] = request_id(request)
    body: dict = {"code": code, "message": message, "request_id": request_id(request)}
    if details is not None:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status, headers=h)


def install(app: FastAPI, *, prod: bool) -> None:
    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError) -> JSONResponse:
        return envelope(request, exc.status, exc.code, exc.message, exc.headers, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "ERROR")
        return envelope(request, exc.status_code, code, code.replace("_", " ").lower(), getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # locations only: never echo submitted values (they may contain passwords)
        locs = sorted({".".join(str(p) for p in e["loc"] if p != "body") for e in exc.errors()})[:10]
        return envelope(request, 422, "VALIDATION_ERROR", "invalid fields: " + ", ".join(locs))

    @app.exception_handler(RateLimitExceeded)
    async def _rate(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        try:
            retry = str(int(exc.limit.limit.get_expiry()))
        except Exception:  # noqa: BLE001
            retry = "60"
        log.warning("ratelimit.hit", path=request.url.path)
        return envelope(request, 429, "RATE_LIMITED", "too many requests", {"Retry-After": retry})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled", path=request.url.path, error=type(exc).__name__, request_id=request_id(request))
        msg = "internal error" if prod else f"internal error: {type(exc).__name__}"
        return envelope(request, 500, "INTERNAL", msg)
