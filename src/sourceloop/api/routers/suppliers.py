from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api.deps import Principal, require_role
from sourceloop.api.errors import ApiError
from sourceloop.data.db import get_session
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import Supplier

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get("/search")
async def search(p: Principal = Depends(require_role(R.BUYER, R.EVALUATOR, R.ADMIN, R.SUPER_ADMIN)), db: AsyncSession = Depends(get_session),
                 q: str | None = Query(None, max_length=100), country: str | None = Query(None, pattern="^[A-Z]{2}$"),
                 category: str | None = Query(None, max_length=80, pattern="^[A-Za-z0-9_ -]+$"),
                 limit: int = Query(25, ge=1, le=50), offset: int = Query(0, ge=0)) -> dict:
    if p.role is R.ADMIN and p.org_kind != "BUYER":  # the registry lists competitors: never for a supplier organization
        raise ApiError(403, "FORBIDDEN", "role not permitted")
    stmt = select(Supplier).where(Supplier.deleted_at.is_(None))
    if q:
        stmt = stmt.where(Supplier.name_norm.contains(q.strip().lower(), autoescape=True))
    if country:
        stmt = stmt.where(Supplier.country == country)
    if category:
        stmt = stmt.where(cast(Supplier.categories, String).contains(f'"{category}"', autoescape=True))
    rows = (await db.execute(stmt.order_by(Supplier.name_norm, Supplier.id).limit(limit).offset(offset))).scalars()
    # public registry fields only: no tax id, risk scores, forced-labour flag, sub-tier origin
    return {"items": [{"id": s.id, "name": s.name, "country": s.country, "categories": s.categories,
                       "network_status": s.network_status, "years_in_business": s.years_in_business} for s in rows],
            "limit": limit, "offset": offset}
