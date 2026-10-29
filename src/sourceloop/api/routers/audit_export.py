"""GET /projects/{pid}/audit/export - NDJSON audit export (PRD G3). EVALUATOR / ADMIN only, scoped in SQL.

One JSON object per line: `{"type":"event",...}` per audit row in seq order, then ONE trailer line
`{"type":"trailer", valid, first_bad_seq, events, head_hash, anchor, anchor_verified}` so an offline auditor can re-check the
chain (hash = sha256(prev_hash + canonical.dumps(body))) and compare the head with the recorded anchor.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api.deps import Principal, get_visible_project, require_role, supplier_scoped
from sourceloop.api.errors import ApiError
from sourceloop.audit.chain import GENESIS, compute_hash
from sourceloop.data import db as dbm
from sourceloop.data.audit_store import to_record
from sourceloop.data.db import get_session
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import AuditAnchor, AuditEvent

router = APIRouter(prefix="/projects", tags=["audit"])
PAGE = 500


def _line(o: dict) -> bytes:
    return (json.dumps(o, sort_keys=True, separators=(",", ":"), default=str) + "\n").encode()


@router.get("/{pid}/audit/export")
async def audit_export(pid: uuid.UUID, p: Principal = Depends(require_role(R.EVALUATOR, R.ADMIN, R.SUPER_ADMIN)),
                       db: AsyncSession = Depends(get_session)) -> StreamingResponse:
    if supplier_scoped(p):  # the raw chain holds both sides' traffic: not for a supplier organization
        raise ApiError(403, "FORBIDDEN", "role not permitted")
    await get_visible_project(db, p, pid)  # 404 if not visible; scoped in SQL
    await db.rollback()  # release the request-scoped connection before streaming
    key = str(pid)

    async def gen() -> AsyncIterator[bytes]:
        prev, expected, bad, head, n = GENESIS, 1, None, None, 0
        async with dbm.get_sessionmaker()() as s:
            after = 0
            while True:
                rows = (await s.execute(select(AuditEvent).where(AuditEvent.project_id == key, AuditEvent.seq > after)
                                        .order_by(AuditEvent.seq).limit(PAGE))).scalars().all()
                if not rows:
                    break
                for e in rows:
                    rec = to_record(e)
                    if bad is None and (rec.seq != expected or rec.prev_hash != prev or compute_hash(rec) != rec.hash):
                        bad = rec.seq
                    prev, expected, head, n, after = rec.hash, expected + 1, (rec.seq, rec.hash), n + 1, rec.seq
                    yield _line({"type": "event", "seq": e.seq, "ts": e.ts.isoformat(), "actor_type": e.actor_type,
                                 "actor_id": e.actor_id, "action": e.action, "rule_id": e.rule_id, "evidence": e.evidence,
                                 "model_id": e.model_id, "model_version": e.model_version, "outcome": e.outcome,
                                 "approver_id": e.approver_id, "config_version": e.config_version,
                                 "prev_hash": e.prev_hash, "hash": e.hash})
            anchor = await s.get(AuditAnchor, key)
        anchor_ok = True
        if anchor is not None and anchor.seq > 0:
            if head is None or head[0] < anchor.seq:
                anchor_ok, bad = False, bad if bad is not None else expected
            elif head[0] == anchor.seq and head[1] != anchor.hash:
                anchor_ok, bad = False, bad if bad is not None else head[0]
        yield _line({"type": "trailer", "project_id": key, "valid": bad is None and anchor_ok, "first_bad_seq": bad,
                     "events": n, "head_hash": head[1] if head else None,
                     "anchor": {"seq": anchor.seq, "hash": anchor.hash} if anchor else None, "anchor_verified": anchor_ok})

    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={
        "Content-Disposition": f'attachment; filename="audit-{key}.ndjson"', "Cache-Control": "no-store"})
