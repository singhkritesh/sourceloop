from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api.deps import BUYER_SIDE, Principal, limiter, require_role, resolve_limit
from sourceloop.api.errors import ApiError
from sourceloop.data.db import get_session
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import Approval, Project, ProjectSupplier, Supplier
from sourceloop.orchestrator.approvals import sla_fields

router = APIRouter(prefix="/approvals", tags=["approvals"])
APPROVAL_ROLES = (*BUYER_SIDE, R.SUPPLIER, R.EVALUATOR, R.SUPER_ADMIN, R.ADMIN)


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["APPROVED", "REJECTED", "EDITED"]
    comment: str = Field(default="", max_length=1000)
    version: int
    edit: dict[str, Any] = Field(default_factory=dict)


_SID = Field(max_length=64, min_length=1)


class _IncludeEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include: list[str] = Field(default_factory=list, max_length=50)


class _FailEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fail: list[str] = Field(default_factory=list, max_length=50)


class _AwardEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier: str = _SID


class _ClauseEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accept_change: bool = False


def _known_ids(a: Approval) -> set[str]:
    ev = a.evidence or {}
    if a.kind.value == "COMPLIANCE_FLAG":
        return {str(k) for k in (ev.get("flagged") or {})}
    if a.kind.value == "TECHNICAL_QUAL":
        return {str(x) for x in (ev.get("candidates") or [])}
    if a.kind.value == "AWARD":
        return {str(r.get("supplier")) for r in (ev.get("ranking") or []) if isinstance(r, dict)}
    return set()


def validate_edit(a: Approval, edit: dict[str, Any]) -> dict[str, Any]:
    """Strict per-kind edit schema + ids must be among the approval's own known supplier ids. 422 INVALID_EDIT."""
    models: dict[str, tuple[type[BaseModel], str | None]] = {
        "COMPLIANCE_FLAG": (_IncludeEdit, "include"), "TECHNICAL_QUAL": (_FailEdit, "fail"),
        "AWARD": (_AwardEdit, "supplier"), "CLAUSE": (_ClauseEdit, None)}
    spec = models.get(a.kind.value)
    if not edit:
        return {}
    if spec is None:
        raise ApiError(422, "INVALID_EDIT", "this approval kind does not accept an edit payload")
    model, idfield = spec
    try:
        parsed = model.model_validate(edit, strict=True).model_dump()
    except ValidationError:
        raise ApiError(422, "INVALID_EDIT", "edit payload does not match the schema for this approval") from None
    if idfield:
        v = parsed[idfield]
        ids = set(v) if isinstance(v, list) else {v}
        if not ids <= _known_ids(a):
            raise ApiError(422, "INVALID_EDIT", "edit references unknown supplier ids")
    return parsed


def approval_out(a: Approval, now: Any = None) -> dict[str, Any]:
    sla = sla_fields(a.kind, a.status, a.created_at, now) if now is not None else {}
    return {**sla, "id": str(a.id), "project_id": str(a.project_id), "kind": a.kind.value, "stage": a.stage,
            "target_role": a.target_role.value, "status": a.status.value, "reasons": a.reasons, "evidence": a.evidence,
            "recommendation": a.recommendation, "comment": a.comment, "version": a.version,
            "created_at": a.created_at.isoformat(), "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None}


def _supplier_scope(p: Principal):
    """A supplier user sees only the checkpoints addressed to the SUPPLIER role on projects where its own organization is a participant."""
    mine = (select(ProjectSupplier.project_id).join(Supplier, Supplier.id == ProjectSupplier.supplier_id).where(Supplier.org_id == p.org_id))
    return select(Approval).where(Approval.target_role == R.SUPPLIER, Approval.project_id.in_(mine))


async def _is_mine(db: AsyncSession, p: Principal, a: Approval) -> bool:
    """The checkpoint names one supplier in its own evidence; it belongs only to that supplier's organization."""
    sid = (a.evidence or {}).get("supplier")
    if not isinstance(sid, str):
        return False
    return (await db.execute(select(Supplier.id).where(Supplier.id == sid, Supplier.org_id == p.org_id))).first() is not None


def _scope(p: Principal):
    if p.role is R.SUPPLIER:
        return _supplier_scope(p)
    stmt = select(Approval).join(Project, Project.id == Approval.project_id)
    if p.role in BUYER_SIDE:
        return stmt.where(Project.buyer_org_id == p.org_id, Approval.target_role == p.role)  # role-scoped inbox
    if p.role is R.ADMIN:  # org admin: every checkpoint of its OWN organization's projects (read); it resolves only ADMIN-targeted ones
        return stmt.where(Project.buyer_org_id == p.org_id)
    return stmt  # EVALUATOR / SUPER_ADMIN: everything (read); SUPER_ADMIN resolves only ADMIN-targeted (operational) items


@router.get("")
async def list_approvals(request: Request, p: Principal = Depends(require_role(*APPROVAL_ROLES)), db: AsyncSession = Depends(get_session),
                         status: str | None = Query(None, pattern="^(PENDING|APPROVED|REJECTED|EDITED)$"),
                         project_id: uuid.UUID | None = None, limit: int = Query(100, ge=1, le=500)) -> dict:
    stmt = _scope(p)
    if status:
        stmt = stmt.where(Approval.status == AS(status))
    if project_id:
        stmt = stmt.where(Approval.project_id == project_id)
    rows = list((await db.execute(stmt.order_by(Approval.created_at.desc(), Approval.core_id).limit(limit))).scalars())
    if p.role is R.SUPPLIER:
        rows = [a for a in rows if await _is_mine(db, p, a)]
    now = request.app.state.clock.now()
    return {"items": [approval_out(a, now) for a in rows]}


@router.post("/{aid}/resolve")
@limiter.shared_limit(resolve_limit, scope="approval_resolve")  # fixed scope: one bucket per user, not per URL
async def resolve(aid: uuid.UUID, body: ResolveBody, request: Request,
                  p: Principal = Depends(require_role(*BUYER_SIDE, R.SUPPLIER, R.ADMIN, R.SUPER_ADMIN)), db: AsyncSession = Depends(get_session)) -> dict:
    # scope in SQL: another org's approval is a 404 (never 403); same org but other role's item is 403 afterwards
    if p.role is R.SUPPLIER:
        row = (await db.execute(_supplier_scope(p).where(Approval.id == aid))).scalar_one_or_none()
        if row is not None and not await _is_mine(db, p, row):
            row = None
    else:
        stmt = select(Approval).join(Project, Project.id == Approval.project_id).where(Approval.id == aid)
        if p.role in BUYER_SIDE or p.role is R.ADMIN:
            stmt = stmt.where(Project.buyer_org_id == p.org_id)
        row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "approval not found")
    if body.status != "EDITED" and body.edit:
        raise ApiError(422, "INVALID_EDIT", "edit is only allowed with status EDITED")
    edit = validate_edit(row, body.edit) if body.status == "EDITED" else {}
    out = await request.app.state.service.resolve_approval(db, p, row, AS(body.status), body.comment, body.version, edit)
    return approval_out(out, request.app.state.clock.now())
