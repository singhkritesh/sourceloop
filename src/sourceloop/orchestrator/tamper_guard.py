"""TAMPER_SUSPECTED guard (PRD G3). Call before a run starts or resumes: if the audit chain does not verify (broken link,
edited row, or truncated tail vs the anchor) the run is blocked and the block itself is audited.

Wiring (service.py, owned elsewhere): `await assert_chain_ok(session_factory, pid, audit=h.audit)` on start and resume;
map `TamperSuspected` to a 409 `TAMPER_SUSPECTED` and keep the project out of RUNNING.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sourceloop.audit.chain import AuditLog, verify_chain
from sourceloop.clock import Clock, SystemClock
from sourceloop.data.audit_store import append_in_session, to_record
from sourceloop.data.models import AuditAnchor, AuditEvent

ACTION = "tamper_suspected"


class TamperSuspected(Exception):
    code = "TAMPER_SUSPECTED"

    def __init__(self, project_id: str, first_bad_seq: int | None):
        super().__init__(f"audit chain failed verification: {project_id} @ {first_bad_seq}")
        self.project_id, self.first_bad_seq = project_id, first_bad_seq


async def assert_chain_ok(session_factory: async_sessionmaker[AsyncSession], project_id: str, *,
                          audit: AuditLog | None = None, clock: Clock | None = None) -> None:
    """Raise TamperSuspected (after auditing it once) when the project's chain fails `verify_chain`. Passing the live
    `audit` log keeps its cached head consistent; without it the event is appended in its own short transaction."""
    pid = str(project_id)
    async with session_factory() as s:
        rows = (await s.execute(select(AuditEvent).where(AuditEvent.project_id == pid).order_by(AuditEvent.seq))).scalars().all()
        anchor = await s.get(AuditAnchor, pid)
        ok, bad = verify_chain([to_record(e) for e in rows], anchor=(anchor.seq, anchor.hash) if anchor else None)
        if ok:
            return
        already = bool(rows) and rows[-1].action == ACTION
    if not already:
        ev = {"actor_type": "orchestrator", "actor_id": "orchestrator", "action": ACTION, "rule_id": "AUDIT_CHAIN_INVALID",
              "evidence": [{"first_bad_seq": bad}], "outcome": "RUN_BLOCKED"}
        if audit is not None:
            await audit.append(pid, **ev)
        else:
            async with session_factory() as s:
                await append_in_session(s, pid, (clock or SystemClock()).now(), **ev)
                await s.commit()
    raise TamperSuspected(pid, bad)
