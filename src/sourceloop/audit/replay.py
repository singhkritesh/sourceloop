"""Audit store used to REBUILD a run after a restart by deterministic replay.

The run is re-executed from genesis against this store. Every regenerated record whose seq already exists in the durable chain must hash
to exactly the stored record (otherwise the replay is not the run that happened: ReplayMismatch). Records beyond the durable head are the
run's next steps; they are BUFFERED and only written (`commit`) once the whole replay succeeded, so a failed restore leaves the durable
chain untouched. Nothing here ever rewrites or deletes a stored record."""
from __future__ import annotations

from sourceloop.audit.chain import GENESIS, AuditRecord, AuditStore


class ReplayMismatch(Exception):
    def __init__(self, seq: int):
        super().__init__(f"replay diverged from the stored audit chain at seq {seq}")
        self.seq = seq


class ReplayStore:
    def __init__(self, real: AuditStore, persisted: list[AuditRecord]):
        self.real = real
        self.persisted = persisted
        self.tail: list[AuditRecord] = []
        self.produced = 0          # records the replay generated (must cover the whole durable chain)
        self.replaying = True

    async def write(self, records: list[AuditRecord]) -> None:
        if not self.replaying:  # restore finished: an ordinary store from here on
            await self.real.write(records)
            return
        for r in records:
            self.produced += 1
            if r.seq <= len(self.persisted):
                if r.hash != self.persisted[r.seq - 1].hash:
                    raise ReplayMismatch(r.seq)
            else:
                self.tail.append(r)  # not durable yet: held back until commit()

    async def read(self, project_id: str, after_seq: int = 0) -> list[AuditRecord]:
        if not self.replaying:
            return await self.real.read(project_id, after_seq)
        return [r for r in [*self.persisted, *self.tail] if r.seq > after_seq]

    async def head(self, project_id: str) -> tuple[int, str]:
        return (0, GENESIS) if self.replaying else await self.real.head(project_id)  # replay starts from genesis

    async def anchor(self, project_id: str) -> tuple[int, str] | None:
        fn = getattr(self.real, "anchor", None)
        return await fn(project_id) if fn is not None else None

    async def commit(self) -> int:
        """Make the replay's new records durable; afterwards this store is a pass-through. Returns how many were written."""
        n = len(self.tail)
        if self.tail:
            await self.real.write(self.tail)
            self.tail = []
        self.replaying = False
        return n
