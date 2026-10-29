"""Load a sanctions source into `watchlist_entry`. Runs as an explicit admin/CLI action, never inside a project run, so a run's
screening input is fixed (replay stays deterministic). Replaces ONLY rows of the source's own list_name; other lists are untouched.
Watchlist rows only ever add scrutiny: matching never auto-clears a hit (compliance rule), so syncing can't weaken screening."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.connectors.base import ConnectorError, SanctionedEntity, SanctionsSource
from sourceloop.data.models import WatchlistEntry

MIN_ENTITIES = 100


@dataclass(frozen=True, slots=True)
class SyncResult:
    list_name: str
    fetched: int
    inserted: int
    removed: int
    unknown_country: int


async def sync_sanctions(s: AsyncSession, source: SanctionsSource, *, list_name: str) -> SyncResult:
    ents: list[SanctionedEntity] = [e for e in await source.fetch() if e.list_name == list_name]
    if len(ents) < MIN_ENTITIES:  # never replace a list with an obviously truncated one
        raise ConnectorError("LIST_TOO_SMALL")
    removed = (await s.execute(select(func.count()).select_from(WatchlistEntry).where(WatchlistEntry.list_name == list_name))).scalar_one()
    await s.execute(delete(WatchlistEntry).where(WatchlistEntry.list_name == list_name))
    for e in ents:
        s.add(WatchlistEntry(id=uuid.uuid4(), name=e.name, aliases=list(e.aliases), country=e.country, list_name=e.list_name))
    await s.flush()
    return SyncResult(list_name, len(ents), len(ents), int(removed), sum(1 for e in ents if e.country == "XX"))
