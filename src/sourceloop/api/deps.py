"""Auth / authorization dependencies. Scoping is done IN SQL (cross-tenant / other-side => 404), roles at the router."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import Depends, Request
from slowapi import Limiter
from sqlalchemy import Select, false, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api import security
from sourceloop.api.config import Settings
from sourceloop.api.errors import ApiError
from sourceloop.data.db import get_session
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import Organization, Project, ProjectSupplier, RevokedToken, Supplier, User

log = structlog.get_logger("sourceloop.api")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
DISABLED_UNTIL = datetime(9999, 1, 1, tzinfo=UTC)  # `locked_until` at or past this means "disabled by an admin" (see routers/org.py)
BUYER_SIDE: tuple[R, ...] = (R.BUYER, R.COMPLIANCE, R.QUALITY, R.LEGAL, R.FINANCE)
# Perspectives. BUSINESS users (buyer-side approvers, suppliers) see only their own side of the business, never the agent workflow.
# INSPECTORS see how the agents behave: SUPER_ADMIN and EVALUATOR everywhere, an org ADMIN for its own organization's side only.
INSPECTORS: tuple[R, ...] = (R.SUPER_ADMIN, R.EVALUATOR, R.ADMIN)
PROJECT_READERS: tuple[R, ...] = (*BUYER_SIDE, R.SUPPLIER, *INSPECTORS)
BUYER_VIEW: tuple[R, ...] = (*BUYER_SIDE, *INSPECTORS)  # roles that may see buyer-side detail
PLATFORM_ROLES: tuple[R, ...] = (R.SUPER_ADMIN, R.EVALUATOR, R.DESIGNER)  # SBN staff: not members of a buyer / supplier organization
ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE = "access_token", "refresh_token", "csrf"
CSRF_EXEMPT = ("/auth/login", "/auth/register")


@dataclass(frozen=True)
class Principal:
    id: uuid.UUID
    email: str
    role: R
    org_id: uuid.UUID
    org_kind: str
    jti: str = ""


def supplier_scoped(p: Principal) -> bool:
    """True when the caller must get the supplier-side filtered view: a supplier user, or the admin of a supplier organization."""
    return p.role is R.SUPPLIER or (p.role is R.ADMIN and p.org_kind == "SUPPLIER")


def buyer_org_admin(p: Principal) -> bool:
    return p.role is R.ADMIN and p.org_kind == "BUYER"


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def client_ip(request: Request, hops: int | None = None) -> str:
    """Peer address for rate-limit keys. X-Forwarded-For is honoured ONLY when `trusted_proxy_hops` > 0, and then only the
    entry the outermost trusted proxy appended (hops-th from the right), never the client-controlled left side."""
    if hops is None:
        s: Settings | None = getattr(request.app.state, "settings", None)
        hops = s.trusted_proxy_hops if s else 0
    peer = request.client.host if request.client else "unknown"
    if hops > 0:
        hdrs = request.headers
        lines = hdrs.getlist("x-forwarded-for") if hasattr(hdrs, "getlist") else [hdrs.get("x-forwarded-for", "")]
        parts = [x.strip() for x in ",".join(lines).split(",") if x.strip()]  # ALL header lines: proxies may append a new line
        if len(parts) >= hops:
            return parts[-hops]
    return peer


def ip_key(request: Request) -> str:
    """Always the peer / trusted-proxy address: used for unauthenticated routes (login, register, refresh), where a cookie
    must never pick the bucket."""
    return f"ip:{client_ip(request)}"


def _key(request: Request) -> str:
    """Per-user bucket ONLY for a fully validated token (signature, exp, required claims, uuid sub, not revoked); anything
    else - absent, forged, expired, revoked - is keyed by IP, so a cookie cannot be used to dodge or to borrow a bucket."""
    tok = request.cookies.get(ACCESS_COOKIE)
    s: Settings | None = getattr(request.app.state, "settings", None)
    if tok and s:
        claims = security.decode_access(s, tok)
        if claims and claims["jti"] not in getattr(request.app.state, "revoked_jti", ()):
            try:
                return f"u:{uuid.UUID(claims['sub'])}"
            except ValueError:
                pass
    return ip_key(request)


_current: dict[str, Settings] = {}
limiter = Limiter(key_func=_key, default_limits=[lambda: _current["s"].default_limit], headers_enabled=False)
login_limit: Callable[[], str] = lambda: _current["s"].login_limit  # noqa: E731
create_limit: Callable[[], str] = lambda: _current["s"].create_limit  # noqa: E731
start_limit: Callable[[], str] = lambda: _current["s"].start_limit  # noqa: E731
resolve_limit: Callable[[], str] = lambda: _current["s"].resolve_limit  # noqa: E731


def bind_limits(s: Settings) -> None:
    _current["s"] = s
    limiter.reset()


async def csrf_protect(request: Request) -> None:
    """Double-submit: csrf cookie must equal X-CSRF-Token on mutations. Only enforced when auth cookies are present
    (a request with no ambient credentials is not CSRF-able; it falls through to 401)."""
    if request.method in SAFE_METHODS:
        return
    path = request.url.path
    if any(path.endswith(p) for p in CSRF_EXEMPT):
        return
    if not (request.cookies.get(ACCESS_COOKIE) or request.cookies.get(REFRESH_COOKIE)):
        return
    if not security.csrf_ok(request.cookies.get(CSRF_COOKIE), request.headers.get("x-csrf-token")):
        log.warning("authz.denied", reason="csrf", path=path)
        raise ApiError(403, "CSRF_FAILED", "missing or invalid CSRF token")


async def revoke_access(db: AsyncSession, app: Any, claims: dict[str, Any], now: Any) -> None:
    """Persist an access-token revocation (survives restarts) and cache it; prune rows whose JWT has expired anyway."""
    from datetime import UTC, datetime

    from sqlalchemy import delete

    jti = claims["jti"]
    app.state.revoked_jti.add(jti)
    if await db.get(RevokedToken, jti) is None:
        db.add(RevokedToken(jti=jti, expires_at=datetime.fromtimestamp(int(claims["exp"]), UTC), revoked_at=now))
    await db.execute(delete(RevokedToken).where(RevokedToken.expires_at < now))
    await db.commit()


async def is_revoked(db: AsyncSession, app: Any, jti: str) -> bool:
    if jti in app.state.revoked_jti:
        return True
    return await db.get(RevokedToken, jti) is not None


async def current_principal(request: Request, db: AsyncSession = Depends(get_session)) -> Principal:
    s = settings_of(request)
    tok = request.cookies.get(ACCESS_COOKIE)
    claims = security.decode_access(s, tok) if tok else None
    if not claims or await is_revoked(db, request.app, claims["jti"]):
        raise ApiError(401, "UNAUTHENTICATED", "authentication required")
    try:
        uid = uuid.UUID(claims["sub"])
    except ValueError:
        raise ApiError(401, "UNAUTHENTICATED", "authentication required") from None
    row = (await db.execute(select(User, Organization.kind).join(Organization, Organization.id == User.org_id)
                            .where(User.id == uid))).first()
    if row is None:
        raise ApiError(401, "UNAUTHENTICATED", "authentication required")
    user, kind = row
    if user.locked_until is not None and user.locked_until >= DISABLED_UNTIL:  # an admin disabled this account: live sessions end at once
        raise ApiError(401, "UNAUTHENTICATED", "authentication required")
    p = Principal(user.id, user.email, user.role, user.org_id, kind.value, claims["jti"])
    request.state.principal = p
    if p.role is R.EVALUATOR and request.method not in SAFE_METHODS and not request.url.path.endswith("/auth/logout"):
        raise ApiError(403, "READ_ONLY_ROLE", "EVALUATOR is read-only")
    return p


def require_role(*roles: R) -> Callable[..., Any]:
    allowed = set(roles)

    async def dep(request: Request, p: Principal = Depends(current_principal)) -> Principal:
        if p.role not in allowed:
            log.warning("authz.denied", role=p.role.value, path=request.url.path)
            raise ApiError(403, "FORBIDDEN", "role not permitted")
        return p

    return dep


# ---------------------------------------------------------------- SQL scoping
def visible_projects(p: Principal) -> Select[Any]:
    stmt = select(Project)
    if p.role in BUYER_SIDE or buyer_org_admin(p):
        return stmt.where(Project.buyer_org_id == p.org_id)
    if supplier_scoped(p):
        return stmt.where(Project.id.in_(
            select(ProjectSupplier.project_id).join(Supplier, Supplier.id == ProjectSupplier.supplier_id)
            .where(Supplier.org_id == p.org_id)))
    if p.role in (R.EVALUATOR, R.SUPER_ADMIN):
        return stmt
    return stmt.where(false())


async def get_visible_project(db: AsyncSession, p: Principal, pid: uuid.UUID) -> Project:
    proj = (await db.execute(visible_projects(p).where(Project.id == pid))).scalar_one_or_none()
    if proj is None:
        raise ApiError(404, "NOT_FOUND", "project not found")
    return proj


async def own_supplier_ids(db: AsyncSession, p: Principal, pid: uuid.UUID) -> set[str]:
    rows = await db.execute(select(ProjectSupplier.supplier_id).join(Supplier, Supplier.id == ProjectSupplier.supplier_id)
                            .where(ProjectSupplier.project_id == pid, Supplier.org_id == p.org_id))
    return set(rows.scalars())
