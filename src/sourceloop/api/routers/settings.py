from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api.deps import Principal, require_role
from sourceloop.data import db as dbm
from sourceloop.data.db import get_session
from sourceloop.data.enums import RoleEnum as R

router = APIRouter(prefix="/settings", tags=["settings"])
MASK = "****"


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    llm_mode: Literal["replay", "live", "record"] | None = None
    token_budget_per_project: int | None = Field(default=None, ge=1000, le=10_000_000)
    kill_switch: bool | None = None   # True: no project can start or resume until switched off


async def _view(request: Request, db: AsyncSession) -> dict:
    s, eff = request.app.state.settings, await request.app.state.service.effective_settings(db)
    row = await dbm.get_setting(db)
    return {**eff, "app_env": s.app_env, "cors_origins": list(s.cors_origins), "active_config_version": row.active_config_version if row else None,
            "setup_complete": bool(row and row.setup_complete),
            "jwt_secret": MASK, "org_signing_secret": MASK, "config_encryption_key": MASK if s.config_encryption_key else None,
            "database_url": MASK}


@router.get("")
async def get_settings(request: Request, _p: Principal = Depends(require_role(R.SUPER_ADMIN, R.EVALUATOR)),
                       db: AsyncSession = Depends(get_session)) -> dict:
    return await _view(request, db)


@router.put("")
async def put_settings(body: SettingsPatch, request: Request, p: Principal = Depends(require_role(R.SUPER_ADMIN)),
                       db: AsyncSession = Depends(get_session)) -> dict:
    values = body.model_dump(exclude_none=True)
    now = request.app.state.clock.now()
    audit = request.app.state.system_audit
    await db.rollback()  # end the auth read transaction: the audit store writes through its own connection
    # audit first (rule 10), then the change; if the change cannot be committed a compensating event is chained, so the
    # log never claims a settings change that did not happen (and never misses one that did)
    await audit.append("system", actor_type="human", actor_id=str(p.id), action="settings.updated",
                       evidence=[{"keys": sorted(values)}], outcome="ok", approver_id=str(p.id))
    try:
        await dbm.upsert_setting(db, now, **values)
        await db.commit()
    except Exception:
        await db.rollback()
        await audit.append("system", actor_type="orchestrator", actor_id="orchestrator", action="settings.update_failed",
                           evidence=[{"keys": sorted(values)}], outcome="rolled_back", approver_id=str(p.id))
        raise
    return await _view(request, db)
