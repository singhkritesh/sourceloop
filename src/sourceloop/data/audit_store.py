"""SQL implementation of the `AuditStore` protocol (audit.chain). One short transaction per batch.

Evidence is normalised through `audit.canonical.dumps` before storage so that what is read back hashes identically
to what was hashed at write time (Decimals become strings, tuples become lists, ...).
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sourceloop.audit.canonical import dumps
from sourceloop.audit.chain import GENESIS, AuditRecord, compute_hash
from sourceloop.data import db
from sourceloop.data.models import AuditAnchor, AuditEvent


def _to_row(r: AuditRecord) -> AuditEvent:
    return AuditEvent(project_id=r.project_id, seq=r.seq, ts=r.ts, actor_type=r.actor_type, actor_id=r.actor_id,
                      action=r.action, rule_id=r.rule_id, evidence=json.loads(dumps(list(r.evidence))),
                      model_id=r.model_id, model_version=r.model_version, outcome=r.outcome,
                      approver_id=r.approver_id, config_version=r.config_version, prev_hash=r.prev_hash, hash=r.hash)


def to_record(e: AuditEvent) -> AuditRecord:
    return AuditRecord(project_id=e.project_id, seq=e.seq, ts=e.ts, actor_type=e.actor_type, actor_id=e.actor_id,
                       action=e.action, rule_id=e.rule_id, evidence=tuple(e.evidence or ()), model_id=e.model_id,
                       model_version=e.model_version, outcome=e.outcome, approver_id=e.approver_id,
                       config_version=e.config_version, prev_hash=e.prev_hash, hash=e.hash)


async def advance_anchor(s: AsyncSession, project_id: str, seq: int, hash_: str, now: datetime) -> None:
    """Record the stream head in the caller's transaction (moves forward only)."""
    row = await s.get(AuditAnchor, project_id)
    if row is None:
        s.add(AuditAnchor(project_id=project_id, seq=seq, hash=hash_, updated_at=now))
    elif seq > row.seq:
        row.seq, row.hash, row.updated_at = seq, hash_, now


async def append_in_session(s: AsyncSession, project_id: str, ts: datetime, **fields) -> AuditRecord:
    """Append ONE hash-chained event inside the caller's transaction (head read from the DB, anchor advanced), so the
    caller can commit it atomically with the change it describes. Callers serialise writers of one stream."""
    head = (await s.execute(select(AuditEvent.seq, AuditEvent.hash).where(AuditEvent.project_id == project_id)
                            .order_by(AuditEvent.seq.desc()).limit(1))).first()
    seq, prev = (head[0], head[1]) if head else (0, GENESIS)
    fields["evidence"] = tuple(fields.get("evidence", ()))
    rec = AuditRecord(project_id=project_id, seq=seq + 1, ts=ts, prev_hash=prev, **fields)
    from dataclasses import replace
    rec = replace(rec, hash=compute_hash(rec))
    s.add(_to_row(rec))
    await advance_anchor(s, project_id, rec.seq, rec.hash, ts)
    await s.flush()
    return rec


class SqlAuditStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._sm = sessionmaker

    def _session(self) -> AsyncSession:
        return (self._sm or db.get_sessionmaker())()

    async def write(self, records: list[AuditRecord]) -> None:
        async with self._session() as s:
            s.add_all([_to_row(r) for r in records])
            last: dict[str, AuditRecord] = {}
            for r in records:
                last[r.project_id] = r
            for r in last.values():
                await advance_anchor(s, r.project_id, r.seq, r.hash, r.ts)
            await s.commit()

    async def anchor(self, project_id: str) -> tuple[int, str] | None:
        async with self._session() as s:
            row = await s.get(AuditAnchor, project_id)
        return (row.seq, row.hash) if row else None

    async def read(self, project_id: str, after_seq: int = 0, limit: int | None = None) -> list[AuditRecord]:
        stmt = select(AuditEvent).where(AuditEvent.project_id == project_id, AuditEvent.seq > after_seq)
        stmt = stmt.order_by(AuditEvent.seq)
        if limit:
            stmt = stmt.limit(limit)
        async with self._session() as s:
            return [to_record(e) for e in (await s.execute(stmt)).scalars()]

    async def head(self, project_id: str) -> tuple[int, str]:
        async with self._session() as s:
            row = (await s.execute(select(AuditEvent.seq, AuditEvent.hash).where(AuditEvent.project_id == project_id)
                                   .order_by(AuditEvent.seq.desc()).limit(1))).first()
        return (row[0], row[1]) if row else (0, GENESIS)

    async def count(self, project_id: str) -> int:
        async with self._session() as s:
            return (await s.execute(select(func.count()).select_from(AuditEvent)
                                    .where(AuditEvent.project_id == project_id))).scalar_one()
