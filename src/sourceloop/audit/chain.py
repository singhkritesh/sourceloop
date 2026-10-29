"""Append-only, hash-chained audit log (per project).

Optimized for the hot path: events are appended under a per-project lock, hashes are computed
in-process, and `append_many` writes a whole batch with a single store call (one transaction
in the SQL store). The store is a narrow protocol so the chain logic is testable without a DB.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import structlog

from sourceloop.audit.canonical import dumps, sha256_hex
from sourceloop.clock import Clock, SystemClock

GENESIS = "0" * 64
_log = structlog.get_logger("sourceloop.audit")


@dataclass(frozen=True, slots=True)
class AuditRecord:
    project_id: str
    seq: int
    ts: datetime
    actor_type: str  # orchestrator | buyer_agent | supplier_agent | subagent | human | studio
    actor_id: str
    action: str
    rule_id: str | None = None
    evidence: tuple[Any, ...] = ()
    model_id: str | None = None
    model_version: str | None = None
    outcome: str | None = None
    approver_id: str | None = None
    config_version: int | None = None
    prev_hash: str = GENESIS
    hash: str = ""

    def body(self) -> dict[str, Any]:
        """Everything that is hashed (i.e., all fields except `hash`)."""
        return {
            "project_id": self.project_id,
            "seq": self.seq,
            "ts": self.ts,
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "action": self.action,
            "rule_id": self.rule_id,
            "evidence": list(self.evidence),
            "model_id": self.model_id,
            "model_version": self.model_version,
            "outcome": self.outcome,
            "approver_id": self.approver_id,
            "config_version": self.config_version,
            "prev_hash": self.prev_hash,
        }


def compute_hash(rec: AuditRecord) -> str:
    return sha256_hex(rec.prev_hash + dumps(rec.body()))


class AuditStore(Protocol):
    async def write(self, records: list[AuditRecord]) -> None: ...
    async def read(self, project_id: str, after_seq: int = 0) -> list[AuditRecord]: ...
    async def head(self, project_id: str) -> tuple[int, str]: ...
    # Optional: `async def anchor(self, project_id) -> tuple[int, str] | None` - the head recorded OUTSIDE the event rows
    # (written with them, never rewound). `AuditLog.verify` uses it to detect tail truncation.


class MemoryAuditStore:
    def __init__(self) -> None:
        self._rows: dict[str, list[AuditRecord]] = defaultdict(list)
        self._anchors: dict[str, tuple[int, str]] = {}  # separate from the rows so truncating rows leaves the anchor

    async def write(self, records: list[AuditRecord]) -> None:
        for r in records:
            self._rows[r.project_id].append(r)
            if r.seq >= self._anchors.get(r.project_id, (0, ""))[0]:
                self._anchors[r.project_id] = (r.seq, r.hash)

    async def anchor(self, project_id: str) -> tuple[int, str] | None:
        return self._anchors.get(project_id)

    async def read(self, project_id: str, after_seq: int = 0) -> list[AuditRecord]:
        return [r for r in self._rows[project_id] if r.seq > after_seq]

    async def head(self, project_id: str) -> tuple[int, str]:
        rows = self._rows[project_id]
        return (rows[-1].seq, rows[-1].hash) if rows else (0, GENESIS)

    def tamper(self, project_id: str, seq: int, **changes: Any) -> None:
        """Test helper only: mutate a stored row to prove verify() detects it."""
        rows = self._rows[project_id]
        for i, r in enumerate(rows):
            if r.seq == seq:
                rows[i] = _replace(r, **changes)


def _replace(r: AuditRecord, **changes: Any) -> AuditRecord:
    from dataclasses import replace

    return replace(r, **changes)


Listener = Callable[[AuditRecord], Awaitable[None]]


@dataclass
class AuditLog:
    store: AuditStore
    clock: Clock = field(default_factory=SystemClock)
    _locks: dict[str, asyncio.Lock] = field(default_factory=lambda: defaultdict(asyncio.Lock))
    _heads: dict[str, tuple[int, str]] = field(default_factory=dict)
    _listeners: list[Listener] = field(default_factory=list)

    def subscribe(self, fn: Listener) -> None:
        """Listeners (SSE broker) are notified only AFTER the record is durably written."""
        self._listeners.append(fn)

    def clear_listeners(self) -> None:
        self._listeners.clear()

    async def _head(self, project_id: str) -> tuple[int, str]:
        if project_id not in self._heads:
            self._heads[project_id] = await self.store.head(project_id)
        return self._heads[project_id]

    async def append(self, project_id: str, **fields: Any) -> AuditRecord:
        return (await self.append_many(project_id, [fields]))[0]

    async def append_many(self, project_id: str, events: list[dict[str, Any]]) -> list[AuditRecord]:
        async with self._locks[project_id]:
            seq, prev = await self._head(project_id)
            out: list[AuditRecord] = []
            for ev in events:
                seq += 1
                ev = dict(ev)
                ev["evidence"] = tuple(ev.get("evidence", ()))
                rec = AuditRecord(project_id=project_id, seq=seq, ts=self.clock.now(), prev_hash=prev, **ev)
                h = compute_hash(rec)
                rec = _replace(rec, hash=h)
                out.append(rec)
                prev = h
            await self.store.write(out)  # durable first
            self._heads[project_id] = (seq, prev)
        for rec in out:
            for fn in self._listeners:
                try:
                    await fn(rec)
                except Exception as e:  # noqa: BLE001 - the record is already durable: a failing observer must not make the
                    # caller believe the event (and the action it proves) did not happen. Derived state is re-synced later.
                    _log.error("audit.listener_failed", project_id=project_id, seq=rec.seq, error=type(e).__name__)
        return out

    async def verify(self, project_id: str) -> tuple[bool, int | None]:
        """Chain integrity PLUS head check: the last stored record must reach the anchor (persisted by the store) and
        the head this log last wrote, so deleting the tail of the log is detected, not just edits in the middle."""
        anchors = [self._heads[project_id]] if project_id in self._heads else []
        fn = getattr(self.store, "anchor", None)
        if fn is not None and (a := await fn(project_id)) is not None:
            anchors.append(a)
        return verify_chain(await self.store.read(project_id), anchor=max(anchors) if anchors else None)


def verify_chain(records: list[AuditRecord], anchor: tuple[int, str] | None = None) -> tuple[bool, int | None]:
    prev = GENESIS
    expected_seq = 1
    for r in records:
        if r.seq != expected_seq or r.prev_hash != prev or compute_hash(r) != r.hash:
            return False, r.seq
        prev = r.hash
        expected_seq += 1
    if anchor is not None and anchor[0] > 0:
        last = records[-1] if records else None
        if last is None or last.seq < anchor[0]:
            return False, expected_seq  # tail missing: first seq that should exist
        if last.seq == anchor[0] and last.hash != anchor[1]:
            return False, last.seq
    return True, None
