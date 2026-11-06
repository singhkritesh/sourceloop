"""A supplier organization's saved profile: the figures its agent gives when the buyer agent asks for information.
Declared by the supplier, never verified here; it only fills blanks. It holds no limit, floor or price."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api.deps import Principal, require_role, supplier_scoped
from sourceloop.api.errors import ApiError
from sourceloop.api.studio_support import active_pin
from sourceloop.bus.messages import RfiReply
from sourceloop.data.audit_store import append_in_session
from sourceloop.data.db import get_session
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import SupplierSavedProfile

router = APIRouter(prefix="/supplier-profile", tags=["supplier-profile"])
Field_ = Literal["capacity_units", "lead_time_weeks", "financial_risk", "years_in_business"]


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[Field_, float] = Field(min_length=1, max_length=4)


async def _max_age(request: Request, db: AsyncSession) -> int:
    pin = await active_pin(db, request.app.state.settings, request.app.state.service.studio_lock)
    return pin.cfg.workflow.saved_profile_max_age_days


def _own(p: Principal) -> None:
    if not supplier_scoped(p):
        raise ApiError(403, "FORBIDDEN", "role not permitted")


def _view(row: SupplierSavedProfile | None, max_age_days: int, now: Any) -> dict[str, Any]:
    if row is None:
        return {"values": {}, "confirmed_at": None, "stale": False, "source": "self-declared, not verified"}
    return {"values": row.values, "confirmed_at": row.confirmed_at.isoformat(), "stale": (now - row.confirmed_at).days >= max_age_days,
            "source": "self-declared, not verified"}


@router.get("")
async def get_profile(request: Request, p: Principal = Depends(require_role(R.SUPPLIER, R.ADMIN)), db: AsyncSession = Depends(get_session)) -> dict:
    _own(p)
    row = (await db.execute(select(SupplierSavedProfile).where(SupplierSavedProfile.org_id == p.org_id))).scalar_one_or_none()
    return _view(row, await _max_age(request, db), request.app.state.clock.now())


@router.put("")
async def put_profile(body: ProfileBody, request: Request, p: Principal = Depends(require_role(R.SUPPLIER, R.ADMIN)),
                      db: AsyncSession = Depends(get_session)) -> dict:
    _own(p)
    try:
        RfiReply(values=body.values)  # the same plausibility ranges as an answer on the bus
    except ValidationError:
        raise ApiError(422, "PROFILE_INVALID", "a figure is out of its plausible range") from None
    now = request.app.state.clock.now()
    row = (await db.execute(select(SupplierSavedProfile).where(SupplierSavedProfile.org_id == p.org_id))).scalar_one_or_none()
    if row is None:
        row = SupplierSavedProfile(org_id=p.org_id, values=dict(body.values), confirmed_at=now, updated_by=p.id)
        db.add(row)
    else:
        row.values, row.confirmed_at, row.updated_by = dict(body.values), now, p.id
    await append_in_session(db, f"org:{p.org_id}", now, actor_type="human", actor_id=str(p.id), action="supplier_profile_saved", outcome="ok",
                            approver_id=str(p.id), evidence=[{"fields": sorted(body.values)}])
    await db.commit()
    return _view(row, await _max_age(request, db), now)


@router.delete("", status_code=204)
async def delete_profile(request: Request, p: Principal = Depends(require_role(R.SUPPLIER, R.ADMIN)), db: AsyncSession = Depends(get_session)) -> None:
    _own(p)
    await db.execute(delete(SupplierSavedProfile).where(SupplierSavedProfile.org_id == p.org_id))
    await append_in_session(db, f"org:{p.org_id}", request.app.state.clock.now(), actor_type="human", actor_id=str(p.id),
                            action="supplier_profile_deleted", outcome="ok", approver_id=str(p.id), evidence=[{}])
    await db.commit()
