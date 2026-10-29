from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.data.db import get_session

router = APIRouter()


@router.get("/health")
async def health(request: Request, db: AsyncSession = Depends(get_session)) -> dict:
    try:
        await db.execute(select(1))
        ok = True
    except Exception:  # noqa: BLE001 - health must answer, not raise
        ok = False
    eff = await request.app.state.service.effective_settings(db) if ok else {"llm_mode": "unknown"}
    return {"status": "ok" if ok else "degraded", "db": ok, "llm_mode": eff["llm_mode"]}
