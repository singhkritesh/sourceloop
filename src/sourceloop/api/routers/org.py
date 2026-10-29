"""Organization administration.

* An ORG ADMIN (role ADMIN) manages ITS OWN organization only: users and roles, and the own-side agent settings (versioned overrides that sit on top of
  the platform configuration). Everything is scoped in SQL to the caller's organization; another organization's id is a 404.
* SUPER_ADMIN (SBN) can do the same for any organization (`?org_id=`), create organizations, and create platform staff.
* EVALUATOR may read, never write.
Every mutation appends an audit event to the organization's own hash chain (`org:<id>`) in the SAME transaction. Initial passwords are shown ONCE in the
response of the call that created them and are never stored in recoverable form."""
from __future__ import annotations

import asyncio
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api.deps import DISABLED_UNTIL, Principal, require_role
from sourceloop.api.errors import ApiError
from sourceloop.api.studio_support import active_pin
from sourceloop.data.audit_store import append_in_session
from sourceloop.data.db import get_session
from sourceloop.data.enums import OrgKindEnum
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import BuyerProfile, Organization, OrgOverride, Project, RefreshToken, User
from sourceloop.studio import org_override as OO

router = APIRouter(tags=["organization"])
READ = (R.ADMIN, R.SUPER_ADMIN, R.EVALUATOR)
WRITE = (R.ADMIN, R.SUPER_ADMIN)
ROLES_BY_KIND: dict[str, tuple[R, ...]] = {
    "BUYER": (R.BUYER, R.COMPLIANCE, R.QUALITY, R.LEGAL, R.FINANCE, R.ADMIN),
    "SUPPLIER": (R.SUPPLIER, R.ADMIN),
}
PLATFORM_STAFF = (R.EVALUATOR, R.DESIGNER, R.SUPER_ADMIN)
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,}$")
_locks: dict[str, asyncio.Lock] = {}


def _lock(org: uuid.UUID) -> asyncio.Lock:
    return _locks.setdefault(str(org), asyncio.Lock())


# ---------------------------------------------------------------- scoping
async def target_org(db: AsyncSession, p: Principal, org_id: uuid.UUID | None) -> Organization:
    if p.role is R.ADMIN:
        if org_id is not None and org_id != p.org_id:
            raise ApiError(404, "NOT_FOUND", "organization not found")  # never reveal that another organization exists
        oid = p.org_id
    else:
        oid = org_id or p.org_id
    org = await db.get(Organization, oid)
    if org is None:
        raise ApiError(404, "NOT_FOUND", "organization not found")
    return org


def _user_out(u: User, now: datetime) -> dict[str, Any]:
    disabled = bool(u.locked_until and u.locked_until >= DISABLED_UNTIL)
    return {"id": str(u.id), "email": u.email, "role": u.role.value, "disabled": disabled,
            "locked": bool(u.locked_until and not disabled and u.locked_until > now), "created_at": u.created_at.isoformat()}


async def _audit(s: AsyncSession, org: uuid.UUID, p: Principal, action: str, now: datetime, **evidence: Any) -> None:
    await append_in_session(s, f"org:{org}", now, actor_type="human", actor_id=str(p.id), action=action, outcome="ok",
                            approver_id=str(p.id), evidence=[{k: v for k, v in evidence.items() if v is not None}])


# ---------------------------------------------------------------- organization + users
@router.get("/org")
async def get_org(org_id: uuid.UUID | None = Query(None), p: Principal = Depends(require_role(*READ)), db: AsyncSession = Depends(get_session)) -> dict:
    org = await target_org(db, p, org_id)
    n = (await db.execute(select(func.count()).select_from(User).where(User.org_id == org.id))).scalar_one()
    return {"id": str(org.id), "name": org.name, "kind": org.kind.value, "users": n, "override_version": org.override_version,
            "roles": [r.value for r in ROLES_BY_KIND.get(org.kind.value, ())]}


@router.get("/org/users")
async def list_users(org_id: uuid.UUID | None = Query(None), p: Principal = Depends(require_role(*READ)), db: AsyncSession = Depends(get_session)) -> dict:
    org = await target_org(db, p, org_id)
    now = datetime.now(UTC)
    rows = (await db.execute(select(User).where(User.org_id == org.id).order_by(User.role, User.email))).scalars()
    return {"items": [_user_out(u, now) for u in rows]}


class NewUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)
    role: R


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: R | None = None
    disabled: bool | None = None


def _new_password() -> str:
    return secrets.token_urlsafe(14)


@router.post("/org/users", status_code=201)
async def create_user(body: NewUser, request: Request, org_id: uuid.UUID | None = Query(None),
                      p: Principal = Depends(require_role(*WRITE)), db: AsyncSession = Depends(get_session)) -> dict:
    org = await target_org(db, p, org_id)
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise ApiError(422, "INVALID_EMAIL", "enter a valid email address")
    if body.role not in ROLES_BY_KIND.get(org.kind.value, ()):
        raise ApiError(422, "ROLE_NOT_ALLOWED", f"a {org.kind.value.lower()} organization cannot have the role {body.role.value}")
    pw = _new_password()
    hashed = await asyncio.to_thread(request.app.state.passwords.hash, pw)
    async with _lock(org.id):
        if (await db.execute(select(User.id).where(User.email == email))).first():
            raise ApiError(409, "EMAIL_TAKEN", "that email is already registered")
        now = request.app.state.clock.now()
        u = User(id=uuid.uuid4(), email=email, password_hash=hashed, role=body.role, org_id=org.id, created_at=now)
        db.add(u)
        await db.flush()
        await _audit(db, org.id, p, "org.user_created", now, user=str(u.id), email=email, role=body.role.value)
        await db.commit()
    return {"user": _user_out(u, now), "initial_password": pw}  # shown once: the admin hands it over, the user should change it


async def _managed_user(db: AsyncSession, org: Organization, uid: uuid.UUID) -> User:
    u = (await db.execute(select(User).where(User.id == uid, User.org_id == org.id))).scalar_one_or_none()
    if u is None:
        raise ApiError(404, "NOT_FOUND", "user not found")  # also the answer for a user of another organization
    return u


async def _admins_left(db: AsyncSession, org: Organization, excluding: uuid.UUID) -> int:
    return (await db.execute(select(func.count()).select_from(User).where(
        User.org_id == org.id, User.role == R.ADMIN, User.id != excluding,
        (User.locked_until.is_(None)) | (User.locked_until < DISABLED_UNTIL)))).scalar_one()


@router.patch("/org/users/{uid}")
async def patch_user(uid: uuid.UUID, body: UserPatch, request: Request, org_id: uuid.UUID | None = Query(None),
                     p: Principal = Depends(require_role(*WRITE)), db: AsyncSession = Depends(get_session)) -> dict:
    org = await target_org(db, p, org_id)
    if body.role is None and body.disabled is None:
        raise ApiError(422, "NOTHING_TO_CHANGE", "give a role or a disabled flag")
    async with _lock(org.id):
        u = await _managed_user(db, org, uid)
        if u.id == p.id:
            raise ApiError(409, "SELF_CHANGE", "you cannot change or disable your own account")
        if u.role is R.SUPER_ADMIN:
            raise ApiError(403, "FORBIDDEN", "platform staff are managed by SBN")
        now = request.app.state.clock.now()
        changes: dict[str, Any] = {}
        if body.role is not None and body.role is not u.role:
            if body.role not in ROLES_BY_KIND.get(org.kind.value, ()):
                raise ApiError(422, "ROLE_NOT_ALLOWED", f"a {org.kind.value.lower()} organization cannot have the role {body.role.value}")
            if u.role is R.ADMIN and await _admins_left(db, org, u.id) == 0:
                raise ApiError(409, "LAST_ADMIN", "an organization must keep at least one active admin")
            changes["role"] = body.role
        if body.disabled is not None:
            if body.disabled and u.role is R.ADMIN and await _admins_left(db, org, u.id) == 0:
                raise ApiError(409, "LAST_ADMIN", "an organization must keep at least one active admin")
            changes["locked_until"] = DISABLED_UNTIL if body.disabled else None
            if not body.disabled:
                changes["failed_logins"] = 0
        if changes:
            await db.execute(update(User).where(User.id == u.id).values(**changes))
            if body.disabled:  # end every session now
                await db.execute(delete(RefreshToken).where(RefreshToken.user_id == u.id))
            await _audit(db, org.id, p, "org.user_changed", now, user=str(u.id), role=changes["role"].value if "role" in changes else None,
                         disabled=body.disabled)
            await db.commit()
            await db.refresh(u)
    return _user_out(u, now)


@router.post("/org/users/{uid}/reset-password")
async def reset_password(uid: uuid.UUID, request: Request, org_id: uuid.UUID | None = Query(None),
                         p: Principal = Depends(require_role(*WRITE)), db: AsyncSession = Depends(get_session)) -> dict:
    org = await target_org(db, p, org_id)
    pw = _new_password()
    hashed = await asyncio.to_thread(request.app.state.passwords.hash, pw)
    async with _lock(org.id):
        u = await _managed_user(db, org, uid)
        if u.role is R.SUPER_ADMIN and p.role is not R.SUPER_ADMIN:
            raise ApiError(403, "FORBIDDEN", "platform staff are managed by SBN")
        now = request.app.state.clock.now()
        await db.execute(update(User).where(User.id == u.id).values(password_hash=hashed, failed_logins=0))
        await db.execute(delete(RefreshToken).where(RefreshToken.user_id == u.id))  # no new access tokens; one already issued lives out its short TTL
        await _audit(db, org.id, p, "org.password_reset", now, user=str(u.id))
        await db.commit()
    return {"initial_password": pw}


# ---------------------------------------------------------------- own-side agent settings (versioned overrides)
class OverrideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any]
    note: str = Field(default="", max_length=300)


class RollbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_version: int = Field(ge=1)
    note: str = Field(default="", max_length=300)


def _issues(e: OO.OverrideInvalid) -> dict[str, Any]:
    return {"issues": e.issues}


@router.get("/org/overrides")
async def get_overrides(request: Request, org_id: uuid.UUID | None = Query(None), p: Principal = Depends(require_role(*READ)),
                        db: AsyncSession = Depends(get_session)) -> dict:
    org = await target_org(db, p, org_id)
    kind = org.kind.value
    pin = await active_pin(db, request.app.state.settings, request.app.state.service.studio_lock)
    rows = (await db.execute(select(OrgOverride).where(OrgOverride.org_id == org.id).order_by(OrgOverride.version.desc()))).scalars().all()
    active = next((r for r in rows if r.version == org.override_version), None)
    return {"kind": kind, "platform_version": pin.number, "active_version": org.override_version, "active": active.payload if active else {},
            "versions": [{"version": r.version, "content_hash": r.content_hash, "note": r.note, "created_at": r.created_at.isoformat(),
                          "created_by": str(r.created_by)} for r in rows],
            "editable": OO.editable(pin.cfg, kind)}


async def _publish_override(db: AsyncSession, request: Request, org: Organization, p: Principal, spec: OO.OrgOverrideSpec, note: str, action: str,
                            **evidence: Any) -> dict[str, Any]:
    pin = await active_pin(db, request.app.state.settings, request.app.state.service.studio_lock)
    try:
        OO.apply(pin.cfg, spec, org.kind.value)  # the whole platform validator, invariants included, BEFORE anything is saved
    except OO.OverrideInvalid as e:
        raise ApiError(422, "OVERRIDE_REJECTED", "these settings are not allowed", details=_issues(e)) from None
    async with _lock(org.id):
        now = request.app.state.clock.now()
        nxt = (await db.execute(select(func.coalesce(func.max(OrgOverride.version), 0)).where(OrgOverride.org_id == org.id))).scalar_one() + 1
        h = OO.content_hash(spec)
        db.add(OrgOverride(org_id=org.id, version=nxt, payload=OO.canonical(spec), content_hash=h, note=note.strip(), created_by=p.id, created_at=now))
        await db.execute(update(Organization).where(Organization.id == org.id).values(override_version=nxt))
        await _audit(db, org.id, p, action, now, version=nxt, content_hash=h, **evidence)
        await db.commit()
    return {"version": nxt, "content_hash": h}


@router.put("/org/overrides", status_code=201)
async def put_overrides(body: OverrideBody, request: Request, org_id: uuid.UUID | None = Query(None),
                        p: Principal = Depends(require_role(*WRITE)), db: AsyncSession = Depends(get_session)) -> dict:
    org = await target_org(db, p, org_id)
    try:
        spec = OO.parse(body.payload)
    except OO.OverrideInvalid as e:
        raise ApiError(422, "OVERRIDE_REJECTED", "these settings are not allowed", details=_issues(e)) from None
    return await _publish_override(db, request, org, p, spec, body.note, "org.override_saved")


@router.post("/org/overrides/rollback", status_code=201)
async def rollback_overrides(body: RollbackBody, request: Request, org_id: uuid.UUID | None = Query(None),
                             p: Principal = Depends(require_role(*WRITE)), db: AsyncSession = Depends(get_session)) -> dict:
    org = await target_org(db, p, org_id)
    row = (await db.execute(select(OrgOverride).where(OrgOverride.org_id == org.id, OrgOverride.version == body.to_version))).scalar_one_or_none()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "no such version")
    return await _publish_override(db, request, org, p, OO.parse(row.payload), body.note or f"rollback to v{body.to_version}",
                                   "org.override_rolled_back", rollback_of=body.to_version)


# ---------------------------------------------------------------- SBN: organizations and platform staff
@router.get("/admin/orgs")
async def list_orgs(p: Principal = Depends(require_role(R.SUPER_ADMIN, R.EVALUATOR)), db: AsyncSession = Depends(get_session)) -> dict:
    users = dict((await db.execute(select(User.org_id, func.count()).group_by(User.org_id))).all())
    projects = dict((await db.execute(select(Project.buyer_org_id, func.count()).group_by(Project.buyer_org_id))).all())
    rows = (await db.execute(select(Organization).order_by(Organization.kind, Organization.name))).scalars()
    return {"items": [{"id": str(o.id), "name": o.name, "kind": o.kind.value, "users": users.get(o.id, 0), "projects": projects.get(o.id, 0),
                       "override_version": o.override_version, "platform": o.id == p.org_id} for o in rows]}


class NewOrg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=200)
    kind: OrgKindEnum
    admin_email: str = Field(max_length=254)


@router.post("/admin/orgs", status_code=201)
async def create_org(body: NewOrg, request: Request, p: Principal = Depends(require_role(R.SUPER_ADMIN)), db: AsyncSession = Depends(get_session)) -> dict:
    email = body.admin_email.strip().lower()
    if not _EMAIL.match(email):
        raise ApiError(422, "INVALID_EMAIL", "enter a valid email address")
    pw = _new_password()
    hashed = await asyncio.to_thread(request.app.state.passwords.hash, pw)
    if (await db.execute(select(User.id).where(User.email == email))).first():
        raise ApiError(409, "EMAIL_TAKEN", "that email is already registered")
    now = request.app.state.clock.now()
    org = Organization(id=uuid.uuid4(), name=body.name.strip(), kind=body.kind, created_at=now)
    db.add(org)
    await db.flush()
    if body.kind is OrgKindEnum.BUYER:
        db.add(BuyerProfile(org_id=org.id))
    admin = User(id=uuid.uuid4(), email=email, password_hash=hashed, role=R.ADMIN, org_id=org.id, created_at=now)
    db.add(admin)
    await db.flush()
    await _audit(db, org.id, p, "org.created", now, name=org.name, kind=body.kind.value, admin=str(admin.id))
    await db.commit()
    return {"org": {"id": str(org.id), "name": org.name, "kind": body.kind.value}, "admin": _user_out(admin, now), "initial_password": pw}


class NewStaff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)
    role: R


@router.post("/admin/staff", status_code=201)
async def create_staff(body: NewStaff, request: Request, p: Principal = Depends(require_role(R.SUPER_ADMIN)), db: AsyncSession = Depends(get_session)) -> dict:
    """SBN platform staff (EVALUATOR, DESIGNER, SUPER_ADMIN) live in SBN's own organization, i.e. the creator's."""
    if body.role not in PLATFORM_STAFF:
        raise ApiError(422, "ROLE_NOT_ALLOWED", "platform staff roles are EVALUATOR, DESIGNER and SUPER_ADMIN")
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise ApiError(422, "INVALID_EMAIL", "enter a valid email address")
    if (await db.execute(select(User.id).where(User.email == email))).first():
        raise ApiError(409, "EMAIL_TAKEN", "that email is already registered")
    pw = _new_password()
    hashed = await asyncio.to_thread(request.app.state.passwords.hash, pw)
    now = request.app.state.clock.now()
    u = User(id=uuid.uuid4(), email=email, password_hash=hashed, role=body.role, org_id=p.org_id, created_at=now)
    db.add(u)
    await db.flush()
    await _audit(db, p.org_id, p, "org.staff_created", now, user=str(u.id), email=email, role=body.role.value)
    await db.commit()
    return {"user": _user_out(u, now), "initial_password": pw}
