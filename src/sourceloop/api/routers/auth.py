"""Cookie-based auth: HttpOnly access + refresh cookies, double-submit CSRF cookie, lockout, rotation with reuse detection."""
from __future__ import annotations

import asyncio
import re
from datetime import timedelta

import structlog
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api import deps, security
from sourceloop.api.deps import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE, Principal, limiter
from sourceloop.api.errors import ApiError
from sourceloop.api.throttle import LoginThrottle
from sourceloop.data.db import get_session
from sourceloop.data.enums import OrgKindEnum
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import Organization, RefreshToken, User

log = structlog.get_logger("sourceloop.auth")
router = APIRouter(prefix="/auth", tags=["auth"])
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,}$")


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)


class RegisterBody(Credentials):
    password: str = Field(min_length=12, max_length=256)
    org_name: str = Field(min_length=2, max_length=200)
    org_kind: OrgKindEnum = OrgKindEnum.BUYER


def _set_cookies(request: Request, response: Response, access: str, refresh: str, csrf: str) -> None:
    s = deps.settings_of(request)
    if s.is_prod:
        secure = True  # prod: configuration decides, never the (client-influenced) Host header
    else:
        secure = s.cookie_secure if s.cookie_secure is not None else (request.url.hostname not in ("localhost", "127.0.0.1", "testserver"))
    common = {"samesite": "strict", "path": "/api/v1", "secure": secure}
    response.set_cookie(ACCESS_COOKIE, access, httponly=True, max_age=s.access_ttl_minutes * 60, **common)  # type: ignore[arg-type]
    response.set_cookie(REFRESH_COOKIE, refresh, httponly=True, max_age=s.refresh_ttl_hours * 3600, **common)  # type: ignore[arg-type]
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, max_age=s.refresh_ttl_hours * 3600, samesite="strict", path="/", secure=secure)


def _clear_cookies(response: Response) -> None:
    for c in (ACCESS_COOKIE, REFRESH_COOKIE):
        response.delete_cookie(c, path="/api/v1")
    response.delete_cookie(CSRF_COOKIE, path="/")


async def _issue(request: Request, response: Response, db: AsyncSession, user: User) -> None:
    s, now = deps.settings_of(request), request.app.state.clock.now()
    access, _jti, _exp = security.make_access(s, now, user.id, user.role.value, user.org_id)
    refresh = security.new_token()
    db.add(RefreshToken(user_id=user.id, token_hash=security.token_hash(refresh),
                        expires_at=now + timedelta(hours=s.refresh_ttl_hours), created_at=now))
    await db.commit()
    _set_cookies(request, response, access, refresh, security.new_csrf())


def _me(user: User, org: Organization) -> dict:
    return {"id": str(user.id), "email": user.email, "role": user.role.value,
            "org": {"id": str(org.id), "name": org.name, "kind": org.kind.value}}


def _throttle(request: Request) -> LoginThrottle:
    s = deps.settings_of(request)
    t: LoginThrottle | None = getattr(request.app.state, "login_throttle", None)
    if t is None:
        t = request.app.state.login_throttle = LoginThrottle(s.max_failed_logins, s.max_failed_logins_per_ip,
                                                              timedelta(minutes=s.lockout_minutes))
    return t


@router.post("/register", status_code=201)
@limiter.limit(deps.login_limit, key_func=deps.ip_key)
async def register(request: Request, body: RegisterBody, response: Response, db: AsyncSession = Depends(get_session)) -> dict:
    s = deps.settings_of(request)
    if not s.registration_enabled:
        raise ApiError(403, "REGISTRATION_DISABLED", "self-registration is disabled")
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise ApiError(422, "VALIDATION_ERROR", "invalid fields: email")
    if (await db.execute(select(User.id).where(User.email == email))).first():
        raise ApiError(409, "EMAIL_TAKEN", "email already registered")
    now = request.app.state.clock.now()
    org = Organization(name=security.sanitize_text(body.org_name), kind=body.org_kind, created_at=now)
    db.add(org)
    await db.flush()
    pw = await asyncio.to_thread(request.app.state.passwords.hash, body.password)
    user = User(email=email, password_hash=pw, org_id=org.id, created_at=now,
                role=R.BUYER if body.org_kind is OrgKindEnum.BUYER else R.SUPPLIER)  # self-service roles only
    db.add(user)
    await db.flush()
    await _issue(request, response, db, user)
    return _me(user, org)


@router.post("/login")
@limiter.limit(deps.login_limit, key_func=deps.ip_key)
async def login(request: Request, body: Credentials, response: Response, db: AsyncSession = Depends(get_session)) -> dict:
    s, clock = deps.settings_of(request), request.app.state.clock
    now = clock.now()
    email = body.email.strip().lower()
    ip, th = deps.client_ip(request), _throttle(request)
    wait = th.ip_retry_after(ip, now)
    if wait is not None:  # this IP alone is hammering logins (any accounts): it is slowed down, nobody else is affected
        log.warning("auth.ip_throttled")
        raise ApiError(429, "RATE_LIMITED", "too many requests", {"Retry-After": str(wait)})
    row = (await db.execute(select(User, Organization).join(Organization, Organization.id == User.org_id)
                            .where(User.email == email))).first()
    user, org = (row[0], row[1]) if row else (None, None)
    locked = user is not None and user.locked_until is not None and user.locked_until > now
    pair_locked = th.pair_locked(email, ip, now)
    ok = await asyncio.to_thread(request.app.state.passwords.verify, user.password_hash if user else None, body.password)
    if locked or pair_locked or not ok or user is None:
        # uniform 401 for wrong password, unknown email, locked (account or account+IP) - no existence/lock oracle; a locked
        # account / pair rejects even the correct password and does not extend its own lock.
        if locked or pair_locked:
            th.record_ip_failure(ip, now)  # counts against the IP only: a lock is never extended by further attempts
        else:
            th.record_failure(email, ip, now)
        if user is not None and not locked and not pair_locked:
            await db.execute(update(User).where(User.id == user.id).values(failed_logins=User.failed_logins + 1))  # atomic
            n = (await db.execute(select(User.failed_logins).where(User.id == user.id))).scalar_one()
            if n >= s.max_failed_logins_account:  # account-wide lock only after many failures (i.e. from several IPs)
                await db.execute(update(User).where(User.id == user.id)
                                 .values(locked_until=now + timedelta(minutes=s.lockout_minutes), failed_logins=0))
            await db.commit()
        log.warning("auth.fail")  # no email / password in logs
        raise ApiError(401, "UNAUTHENTICATED", "invalid credentials")
    th.record_success(email, ip)
    user.failed_logins, user.locked_until = 0, None
    await _issue(request, response, db, user)
    return _me(user, org)  # type: ignore[arg-type]


DEMO_DOMAIN = "@sourceloop.test"  # accounts created by `python -m sourceloop.seed`; nothing else is ever offered here


class DemoLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)


@router.get("/demo-roles")
@limiter.limit(deps.login_limit, key_func=deps.ip_key)
async def demo_roles(request: Request, db: AsyncSession = Depends(get_session)) -> dict:
    """Test-bed role picker: lists the seeded demo accounts when DEMO_ROLE_LOGIN=true, otherwise `{enabled: false}`."""
    if not deps.settings_of(request).demo_role_login:
        return {"enabled": False, "accounts": []}
    rows = (await db.execute(select(User, Organization).join(Organization, Organization.id == User.org_id)
                             .where(User.email.like(f"%{DEMO_DOMAIN}")).order_by(User.email))).all()
    return {"enabled": True, "accounts": [{"email": u.email, "role": u.role.value, "org": o.name, "org_kind": o.kind.value} for u, o in rows]}


@router.post("/demo-login")
@limiter.limit(deps.login_limit, key_func=deps.ip_key)
async def demo_login(request: Request, body: DemoLogin, response: Response, db: AsyncSession = Depends(get_session)) -> dict:
    """Sign in as a seeded demo account without a password. Off unless DEMO_ROLE_LOGIN=true (and never in prod); demo accounts only."""
    if not deps.settings_of(request).demo_role_login:
        raise ApiError(404, "NOT_FOUND", "not found")
    email = body.email.strip().lower()
    row = (await db.execute(select(User, Organization).join(Organization, Organization.id == User.org_id)
                            .where(User.email == email))).first() if email.endswith(DEMO_DOMAIN) else None
    if row is None:
        raise ApiError(404, "NOT_FOUND", "not found")
    log.warning("auth.demo_login", role=row[0].role.value)  # audible in logs: password-less sign-in happened
    await _issue(request, response, db, row[0])
    return _me(row[0], row[1])


@router.post("/refresh")
@limiter.limit(deps.login_limit, key_func=deps.ip_key)
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_session)) -> dict:
    now = request.app.state.clock.now()
    tok = request.cookies.get(REFRESH_COOKIE)
    row = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == security.token_hash(tok)))).scalar_one_or_none() if tok else None
    if row is None:
        raise ApiError(401, "UNAUTHENTICATED", "invalid refresh token")
    if row.revoked_at is not None:  # a rotated token was replayed: assume theft, revoke everything for the user
        await db.execute(update(RefreshToken).where(RefreshToken.user_id == row.user_id, RefreshToken.revoked_at.is_(None))
                         .values(revoked_at=now))
        await db.commit()
        log.warning("auth.refresh_reuse")
        raise ApiError(401, "UNAUTHENTICATED", "refresh token reuse detected")
    if row.expires_at <= now:
        raise ApiError(401, "UNAUTHENTICATED", "refresh token expired")
    # rotation is a compare-and-set: of N concurrent refreshes with the same token exactly one flips revoked_at; the others
    # lose the race and take the reuse-detected path (everything revoked), instead of each minting a fresh session.
    res = await db.execute(update(RefreshToken).where(RefreshToken.id == row.id, RefreshToken.revoked_at.is_(None))
                           .values(revoked_at=now))
    if res.rowcount != 1:
        await db.execute(update(RefreshToken).where(RefreshToken.user_id == row.user_id, RefreshToken.revoked_at.is_(None))
                         .values(revoked_at=now))
        await db.commit()
        log.warning("auth.refresh_reuse")
        raise ApiError(401, "UNAUTHENTICATED", "refresh token reuse detected")
    user = (await db.execute(select(User).where(User.id == row.user_id))).scalar_one()
    org = (await db.execute(select(Organization).where(Organization.id == user.org_id))).scalar_one()
    await _issue(request, response, db, user)
    return _me(user, org)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_session)) -> Response:
    now = request.app.state.clock.now()
    tok = request.cookies.get(REFRESH_COOKIE)
    if tok:
        await db.execute(update(RefreshToken).where(RefreshToken.token_hash == security.token_hash(tok),
                                                    RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
        await db.commit()
    claims = security.decode_access(deps.settings_of(request), request.cookies.get(ACCESS_COOKIE, ""))
    if claims:
        await deps.revoke_access(db, request.app, claims, now)
    out = Response(status_code=204)
    _clear_cookies(out)
    return out


@router.get("/me")
async def me(p: Principal = Depends(deps.current_principal), db: AsyncSession = Depends(get_session)) -> dict:
    org = (await db.execute(select(Organization).where(Organization.id == p.org_id))).scalar_one()
    return {"id": str(p.id), "email": p.email, "role": p.role.value,
            "org": {"id": str(org.id), "name": org.name, "kind": org.kind.value}}


