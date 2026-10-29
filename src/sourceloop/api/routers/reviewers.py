"""GET /reviewers/override-rates - how often each human reviewer overrides the agent recommendation (PRD G2).

Computed in SQL from RESOLVED approvals (status APPROVED / EDITED / REJECTED), per reviewer and approval kind. A reviewer
with >= MIN_DECISIONS decisions and an override rate below RUBBER_STAMP_THRESHOLD is flagged POSSIBLE_RUBBER_STAMP.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api.deps import Principal, require_role
from sourceloop.api.errors import ApiError
from sourceloop.data.db import get_session
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import Approval, Project, User

router = APIRouter(prefix="/reviewers", tags=["reviewers"])
MIN_DECISIONS = 20
RUBBER_STAMP_THRESHOLD = 0.02


def _flag(n: int, rate: float) -> str | None:
    return "POSSIBLE_RUBBER_STAMP" if n >= MIN_DECISIONS and rate < RUBBER_STAMP_THRESHOLD else None


@router.get("/override-rates")
async def override_rates(p: Principal = Depends(require_role(R.ADMIN, R.EVALUATOR, R.SUPER_ADMIN)),
                         db: AsyncSession = Depends(get_session)) -> dict:
    if p.role is R.ADMIN and p.org_kind != "BUYER":
        raise ApiError(403, "FORBIDDEN", "role not permitted")
    # an org admin sees the reviewers of its OWN organization only; SUPER_ADMIN / EVALUATOR see everyone
    own_org = [Approval.project_id.in_(select(Project.id).where(Project.buyer_org_id == p.org_id))] if p.role is R.ADMIN else []

    def n(st: AS):  # noqa: ANN202
        return func.sum(case((Approval.status == st, 1), else_=0))

    rows = (await db.execute(
        select(Approval.resolver_id, Approval.target_role, Approval.kind, n(AS.APPROVED), n(AS.EDITED), n(AS.REJECTED))
        .where(Approval.status != AS.PENDING, Approval.resolver_id.is_not(None), *own_org)
        .group_by(Approval.resolver_id, Approval.target_role, Approval.kind)
        .order_by(Approval.resolver_id, Approval.kind))).all()
    ids: set[uuid.UUID] = set()
    for r in rows:
        try:
            ids.add(uuid.UUID(r[0]))
        except ValueError:
            pass
    emails = {str(u.id): u.email for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars()} if ids else {}
    per: dict[str, dict[str, Any]] = {}
    for rid, role, kind, a, e, rj in rows:
        a, e, rj = int(a or 0), int(e or 0), int(rj or 0)
        tot = a + e + rj
        who = per.setdefault(rid, {"resolver_id": rid, "email": emails.get(str(uuid.UUID(rid))) if _isuuid(rid) else None,
                                   "role": role.value, "approved": 0, "edited": 0, "rejected": 0, "by_kind": []})
        who["approved"] += a
        who["edited"] += e
        who["rejected"] += rj
        who["by_kind"].append({"kind": kind.value, "approved": a, "edited": e, "rejected": rj, "decisions": tot,
                               "override_rate": round((e + rj) / tot, 3)})
    items = []
    for who in per.values():
        tot = who["approved"] + who["edited"] + who["rejected"]
        rate = round((who["edited"] + who["rejected"]) / tot, 3)
        items.append({**who, "decisions": tot, "override_rate": rate, "flag": _flag(tot, rate)})
    return {"items": items, "min_decisions": MIN_DECISIONS, "rubber_stamp_threshold": RUBBER_STAMP_THRESHOLD}


def _isuuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False
