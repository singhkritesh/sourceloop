"""FastAPI app factory. Base path /api/v1."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from sourceloop.api import deps, errors
from sourceloop.api.broker import Broker
from sourceloop.api.config import Settings, load_settings
from sourceloop.api.logging_config import configure_logging
from sourceloop.api.routers import (
    approvals,
    audit_export,
    auth,
    health,
    org,
    perspective,
    projects,
    reviewers,
    studio,
    suppliers,
)
from sourceloop.api.routers import settings as settings_router
from sourceloop.api.security import Passwords
from sourceloop.api.service import ProjectService
from sourceloop.api.studio_support import ensure_default_config
from sourceloop.audit.chain import AuditLog
from sourceloop.clock import Clock, SystemClock
from sourceloop.data import db as dbm
from sourceloop.data.audit_store import SqlAuditStore

API_PREFIX = "/api/v1"
MAX_BODY = 1_000_000
log = structlog.get_logger("sourceloop.api")


class _BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Pure ASGI: counts bytes AS THE BODY IS READ (chunked / lying Content-Length included) and answers 413."""

    def __init__(self, app, max_body: int = MAX_BODY) -> None:  # noqa: ANN001
        self.app, self.max_body = app, max_body

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        seen, started, exceeded = 0, False, False

        async def limited_receive():  # noqa: ANN202
            nonlocal seen, exceeded
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max_body:
                    exceeded = True
                    raise _BodyTooLarge
            return msg

        async def tracking_send(msg) -> None:  # noqa: ANN001
            nonlocal started
            if exceeded:  # whatever the app answers after the abort (FastAPI turns it into a 400) is replaced by the 413
                if not started:
                    started = True
                    await errors.envelope(Request(scope), 413, "PAYLOAD_TOO_LARGE", "request body too large")(scope, receive, send)
                return
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BaseException:
            if not exceeded:
                raise
            await tracking_send({"type": "http.response.start"})


class ContextMiddleware(BaseHTTPMiddleware):
    """Request id, body-size cap (413), security headers."""

    def __init__(self, app, *, prod: bool, api_origins: str) -> None:  # noqa: ANN001
        super().__init__(app)
        self.prod, self.api_origins = prod, api_origins

    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: ANN001
        request.state.request_id = str(uuid.uuid4())
        t0 = time.perf_counter()
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_BODY:
            resp: Response = errors.envelope(request, 413, "PAYLOAD_TOO_LARGE", "request body too large")
        else:
            resp = await call_next(request)
        resp.headers.setdefault("X-Request-ID", request.state.request_id)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = (
            f"default-src 'self'; connect-src 'self' {self.api_origins}".strip()
            + "; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")
        if resp.headers.get("content-type", "").startswith("application/json"):
            resp.headers.setdefault("Cache-Control", "no-store")  # authenticated JSON must not sit in shared/browser caches
        if self.prod:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        log.info("http", method=request.method, path=request.url.path, status=resp.status_code,
                 ms=int((time.perf_counter() - t0) * 1000))
        return resp


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    s = settings or load_settings()
    configure_logging(logging.getLevelNamesMapping().get(s.log_level, logging.INFO))
    if settings is not None or not dbm.is_configured():
        dbm.configure(s.database_url)
    deps.bind_limits(s)
    broker = Broker()
    service = ProjectService(s, broker, clock)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        await service.recover()
        await service.prune_revoked()
        await ensure_default_config(s, service.studio_lock)  # config v1 on first boot; projects pin versions (rule 15a)
        yield
        await service.shutdown()
        if settings is not None:
            await dbm.dispose()

    app = FastAPI(title="SourceLoop API", version="0.1.0", lifespan=lifespan, docs_url=None if s.is_prod else "/docs",
                  redoc_url=None, openapi_url=None if s.is_prod else "/openapi.json", dependencies=[Depends(deps.csrf_protect)])
    app.state.settings = s
    app.state.clock = clock or SystemClock()
    app.state.passwords = Passwords(s)
    app.state.broker = broker
    app.state.service = service
    app.state.revoked_jti = set()
    app.state.limiter = deps.limiter
    app.state.system_audit = _SystemAudit(AuditLog(SqlAuditStore(), app.state.clock))

    errors.install(app, prod=s.is_prod)
    app.add_middleware(BodyLimitMiddleware)  # innermost: ContextMiddleware still adds headers to the 413
    app.add_middleware(SlowAPIMiddleware)
    csp_origins = [o for o in s.cors_origins if not s.is_prod or o.startswith("https://")]  # no dev origin in the prod CSP
    app.add_middleware(ContextMiddleware, prod=s.is_prod, api_origins=" ".join(csp_origins))
    app.add_middleware(CORSMiddleware, allow_origins=list(s.cors_origins), allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-CSRF-Token", "Last-Event-ID"])

    for r in (health.router, auth.router, projects.router, approvals.router, suppliers.router, settings_router.router):
        app.include_router(r, prefix=API_PREFIX)
    app.include_router(studio.router, prefix=API_PREFIX)
    app.include_router(audit_export.router, prefix=API_PREFIX)
    app.include_router(reviewers.router, prefix=API_PREFIX)
    app.include_router(perspective.router, prefix=API_PREFIX)
    app.include_router(org.router, prefix=API_PREFIX)
    return app


class _SystemAudit:
    """Non-project audit events (settings changes, later Studio) go to the hash-chained stream 'system'."""

    def __init__(self, log_: AuditLog) -> None:
        self._log = log_

    async def append(self, project_id: str, **fields):  # noqa: ANN003, ANN201
        return await self._log.append(project_id, **fields)
