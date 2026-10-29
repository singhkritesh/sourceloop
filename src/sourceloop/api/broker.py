"""In-process wake-up channel for SSE streams. Streams read the durable audit log; the broker only says 'look again'."""
from __future__ import annotations

import asyncio
from collections import defaultdict


class Broker:
    def __init__(self) -> None:
        self._waiters: dict[str, set[asyncio.Event]] = defaultdict(set)
        self.streams: dict[str, int] = defaultdict(int)  # user id -> open streams

    def subscribe(self, project_id: str) -> asyncio.Event:
        ev = asyncio.Event()
        self._waiters[project_id].add(ev)
        return ev

    def unsubscribe(self, project_id: str, ev: asyncio.Event) -> None:
        self._waiters[project_id].discard(ev)
        if not self._waiters[project_id]:
            self._waiters.pop(project_id, None)

    def notify(self, project_id: str) -> None:
        for ev in list(self._waiters.get(project_id, ())):
            ev.set()
