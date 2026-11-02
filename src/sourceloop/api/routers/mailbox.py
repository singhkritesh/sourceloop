"""The in-app mailbox (email and text windows): list, mark read, and answer a decision by replying in plain words."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api import mailbox as mb
from sourceloop.api import security
from sourceloop.api.deps import BUYER_SIDE, Principal, limiter, require_role, resolve_limit
from sourceloop.api.errors import ApiError
from sourceloop.data.db import get_session
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import Approval, Mailbox

router = APIRouter(prefix="/mailbox", tags=["mailbox"])


class ReplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=500)


def _side(p: Principal) -> str:
    return "SUPPLIER" if p.role is R.SUPPLIER else "BUYER"


def _mine(p: Principal):
    return (Mailbox.owner_org_id == p.org_id, Mailbox.owner_role == _side(p))


@router.get("")
async def list_mailbox(p: Principal = Depends(require_role(*BUYER_SIDE, R.SUPPLIER)), db: AsyncSession = Depends(get_session),
                       channel: str | None = Query(None, pattern="^(EMAIL|SMS)$"), limit: int = Query(100, ge=1, le=300)) -> dict[str, Any]:
    stmt = select(Mailbox).where(*_mine(p))
    if channel:
        stmt = stmt.where(Mailbox.channel == channel)
    rows = list((await db.execute(stmt.order_by(Mailbox.created_at.desc(), Mailbox.id).limit(limit))).scalars())
    open_ids = {a for (a,) in (await db.execute(select(Approval.id).where(
        Approval.id.in_([m.approval_id for m in rows if m.approval_id]), Approval.status == AS.PENDING))).all()} if rows else set()
    unread = {c: n for c, n in (await db.execute(select(Mailbox.channel, func.count()).where(
        *_mine(p), Mailbox.direction == "IN", Mailbox.read_at.is_(None)).group_by(Mailbox.channel))).all()}
    return {"items": [{**mb.out(m), "open": m.approval_id in open_ids} for m in rows],
            "unread": {"EMAIL": unread.get("EMAIL", 0), "SMS": unread.get("SMS", 0)}}


@router.post("/{mid}/read")
async def mark_read(mid: uuid.UUID, request: Request, p: Principal = Depends(require_role(*BUYER_SIDE, R.SUPPLIER)),
                    db: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    m = (await db.execute(select(Mailbox).where(Mailbox.id == mid, *_mine(p)))).scalar_one_or_none()
    if m is None:
        raise ApiError(404, "NOT_FOUND", "message not found")
    if m.read_at is None:
        m.read_at = request.app.state.clock.now()
        await db.commit()
    return {"id": str(mid), "read": True}


@router.post("/{mid}/reply")
@limiter.shared_limit(resolve_limit, scope="approval_resolve")
async def reply(mid: uuid.UUID, body: ReplyBody, request: Request, p: Principal = Depends(require_role(*BUYER_SIDE, R.SUPPLIER)),
                db: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Reply to a decision message. "APPROVE" or "DECLINE <reason>" resolves the decision exactly as the button would; anything else gets a
    short help message back and changes nothing."""
    m = (await db.execute(select(Mailbox).where(Mailbox.id == mid, *_mine(p)))).scalar_one_or_none()
    if m is None:
        raise ApiError(404, "NOT_FOUND", "message not found")
    if m.direction != "IN" or m.approval_id is None:
        raise ApiError(409, "NOT_A_DECISION", "this message is not a decision you can answer")
    now = request.app.state.clock.now()
    channel, project_id, approval_id = m.channel, m.project_id, m.approval_id  # the resolve below ends the read transaction: take what we need first
    text = security.sanitize_text(body.text)
    status, rest = mb.parse_reply(text)
    answer = "Reply APPROVE to accept, or DECLINE followed by a short reason."
    row = (await db.execute(select(Approval).where(Approval.id == approval_id))).scalar_one_or_none()
    if row is not None and status is not None:
        if row.status is not AS.PENDING:
            answer = "This decision was already answered."
        elif status is AS.REJECTED and len(rest) < 10:
            answer = "Please add a reason of at least 10 characters, for example: DECLINE price is above what we can accept."
        else:
            try:
                await request.app.state.service.resolve_approval(db, p, row, status, rest, row.version, {})
                answer = "Done. You approved it." if status is AS.APPROVED else "Done. You declined it."
            except ApiError as e:
                answer = {"VERSION_CONFLICT": "This decision was already answered.", "FORBIDDEN": "This decision is not yours to answer."}.get(
                    e.code, f"It could not be recorded ({e.code.lower().replace('_', ' ')}).")
    elif row is None:
        answer = "This decision is no longer available."
    mb.add(db, now, org=p.org_id, role=_side(p), channel=channel, kind="REPLY", subject="", body=text, project=project_id,
           approval=approval_id, direction="OUT")
    mb.add(db, now, org=p.org_id, role=_side(p), channel=channel, kind="NOTE", subject="", body=answer, project=project_id,
           approval=approval_id)
    await db.execute(update(Mailbox).where(Mailbox.id == mid, Mailbox.read_at.is_(None)).values(read_at=now))
    await db.commit()
    return {"answer": answer}
