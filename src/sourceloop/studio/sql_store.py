"""SQL adapters for the pure Studio library (the only DB-aware module under studio/).

Request-scoped: every instance wraps ONE `AsyncSession` and only flushes. The router commits once, so a publish writes
`config_version` + `app_setting.active_config_version` + the `studio.*` audit event + the audit anchor in ONE transaction
(audit before live, all-or-nothing). Writers of the 'studio' audit stream are serialised by the caller.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.audit.canonical import dumps
from sourceloop.clock import Clock, SystemClock
from sourceloop.data import db as dbm
from sourceloop.data.audit_store import append_in_session
from sourceloop.data.enums import ConfigStatusEnum as CS
from sourceloop.data.models import AuditEvent, Provider, ProviderKey, StudioDraft
from sourceloop.data.models import ConfigVersion as ConfigRow
from sourceloop.studio.lifecycle import ConfigVersion, VersionConflict
from sourceloop.studio.schema import ProviderDef

AUDIT_STREAM = "studio"


def _uuid(v: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(v) if v else None
    except ValueError:
        return None  # e.g. the 'system' bootstrap actor


class SqlStudioStore:
    """Implements `lifecycle.StudioStore` (plus a few SQL-only helpers used by the router)."""

    def __init__(self, s: AsyncSession, clock: Clock | None = None) -> None:
        self.s, self.clock = s, clock or SystemClock()

    # ---- drafts ----
    async def get_draft(self, owner: str) -> dict[str, Any] | None:
        row = await self.s.get(StudioDraft, owner)
        return json.loads(json.dumps(row.body)) if row is not None else None

    async def draft_meta(self, owner: str) -> tuple[int | None, datetime | None]:
        row = await self.s.get(StudioDraft, owner)
        return (row.base_version, row.updated_at) if row is not None else (None, None)

    async def save_draft(self, owner: str, payload: dict[str, Any]) -> None:
        now = self.clock.now()
        body = json.loads(dumps(payload))
        row = await self.s.get(StudioDraft, owner)
        if row is None:
            self.s.add(StudioDraft(owner_id=owner, body=body, base_version=await self.get_active(), updated_at=now))
        else:
            row.body, row.updated_at = body, now
        await self.s.flush()

    async def delete_draft(self, owner: str) -> None:
        row = await self.s.get(StudioDraft, owner)
        if row is not None:
            await self.s.delete(row)
            await self.s.flush()

    async def reset_draft_base(self, owner: str) -> None:
        """Import/discard start a fresh draft against the CURRENT active version."""
        row = await self.s.get(StudioDraft, owner)
        if row is not None:
            row.base_version = await self.get_active()
            await self.s.flush()

    # ---- versions ----
    async def latest_number(self) -> int:
        return (await self.s.execute(select(func.coalesce(func.max(ConfigRow.version), 0)))).scalar_one()

    async def add_version(self, v: ConfigVersion) -> None:
        if (await self.s.execute(select(ConfigRow.id).where(ConfigRow.version == v.number))).first() is not None:
            raise VersionConflict(str(v.number))
        prev = await self.get_active()
        parent = None
        if prev is not None:
            parent = (await self.s.execute(select(ConfigRow.id).where(ConfigRow.version == prev))).scalar_one_or_none()
        author = _uuid(v.author)
        self.s.add(ConfigRow(id=uuid.uuid4(), version=v.number, content_hash=v.content_hash, body=json.loads(v.payload_json),
                             status=CS.PUBLISHED, schema_version=1, parent_id=parent, created_by=author, published_by=author,
                             published_at=v.created_at, created_at=v.created_at, note=v.note, rollback_of=v.rollback_of))
        try:
            await self.s.flush()
        except IntegrityError:
            raise VersionConflict(str(v.number)) from None

    @staticmethod
    def _to_version(r: ConfigRow) -> ConfigVersion:
        return ConfigVersion(number=r.version, content_hash=r.content_hash, payload_json=dumps(r.body),
                             author=str(r.created_by) if r.created_by else "system", note=r.note or "",
                             created_at=r.created_at, rollback_of=r.rollback_of,
                             status="ARCHIVED" if r.status is CS.ARCHIVED else "PUBLISHED")

    async def get_version(self, number: int) -> ConfigVersion | None:
        r = (await self.s.execute(select(ConfigRow).where(ConfigRow.version == number))).scalar_one_or_none()
        return self._to_version(r) if r is not None else None

    async def list_versions(self) -> list[ConfigVersion]:
        rows = (await self.s.execute(select(ConfigRow).order_by(ConfigRow.version))).scalars()
        return [self._to_version(r) for r in rows]

    async def row_id(self, number: int) -> uuid.UUID | None:
        return (await self.s.execute(select(ConfigRow.id).where(ConfigRow.version == number))).scalar_one_or_none()

    async def number_of(self, row_id: uuid.UUID) -> int | None:
        return (await self.s.execute(select(ConfigRow.version).where(ConfigRow.id == row_id))).scalar_one_or_none()

    async def set_active(self, number: int) -> None:
        prev = await self.get_active()
        if prev is not None and prev != number:  # only the status moves; body / hash / version are frozen by trigger + ORM guard
            await self.s.execute(update(ConfigRow).where(ConfigRow.version == prev).values(status=CS.ARCHIVED))
        await dbm.upsert_setting(self.s, self.clock.now(), active_config_version=number)

    async def get_active(self) -> int | None:
        row = await dbm.get_setting(self.s)
        return row.active_config_version if row else None


def audit_sink(s: AsyncSession, clock: Clock) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Lifecycle audit sink: appends the `studio.*` event to the hash-chained 'studio' stream in the caller's transaction."""

    async def sink(ev: dict[str, Any]) -> None:
        await append_in_session(
            s, AUDIT_STREAM, clock.now(), actor_type="studio", actor_id=str(ev["actor"])[:80], action=str(ev["action"])[:60],
            rule_id=None, evidence=[json.loads(dumps({"role": ev["actor_role"], "version": ev["version"], "hash": ev["hash"],
                                                     "detail": ev["detail"]}))],
            outcome="ok", approver_id=str(ev["actor"])[:80], config_version=ev["version"])

    return sink


async def studio_events(s: AsyncSession, limit: int = 200) -> list[AuditEvent]:
    rows = (await s.execute(select(AuditEvent).where(AuditEvent.project_id == AUDIT_STREAM)
                            .order_by(AuditEvent.seq.desc()).limit(limit))).scalars()
    return list(rows)


# ---------------------------------------------------------------------------------------------------- vault
class SqlVaultBackend:
    """`vault.VaultBackend` over provider/provider_key (ciphertext only). The Protocol is synchronous, so ciphertexts are
    loaded once (`await load()`), reads are served from that snapshot, and writes are queued and applied by
    `await flush(...)` inside the request transaction (so the key change commits with its audit event)."""

    def __init__(self, s: AsyncSession) -> None:
        self.s = s
        self._d: dict[str, tuple[bytes, str]] = {}
        self._ops: list[tuple[str, str, bytes, str]] = []

    async def load(self) -> SqlVaultBackend:
        rows = await self.s.execute(select(Provider.name, ProviderKey.ciphertext, ProviderKey.updated_at)
                                    .join(ProviderKey, ProviderKey.provider_id == Provider.id))
        self._d = {n: (bytes(c), u.isoformat()) for n, c, u in rows}
        return self

    def get(self, provider: str) -> tuple[bytes, str] | None:
        return self._d.get(provider)

    def put(self, provider: str, token: bytes, updated_at: str) -> None:
        self._d[provider] = (token, updated_at)
        self._ops.append(("put", provider, token, updated_at))

    def delete(self, provider: str) -> None:
        self._d.pop(provider, None)
        self._ops.append(("delete", provider, b"", ""))

    def __repr__(self) -> str:
        return f"SqlVaultBackend(entries={len(self._d)})"

    async def flush(self, defs: dict[str, ProviderDef], actor: str) -> None:
        for op, name, token, stamp in self._ops:
            if op == "put":
                prov = await ensure_provider(self.s, name, defs.get(name))
                row = (await self.s.execute(select(ProviderKey).where(ProviderKey.provider_id == prov.id))).scalar_one_or_none()
                ts = datetime.fromisoformat(stamp).astimezone(UTC)
                if row is None:
                    self.s.add(ProviderKey(provider_id=prov.id, ciphertext=token, key_id="k1", updated_at=ts,
                                           updated_by=_uuid(actor)))
                else:
                    row.ciphertext, row.updated_at, row.updated_by = token, ts, _uuid(actor)
            else:
                prov = (await self.s.execute(select(Provider).where(Provider.name == name))).scalar_one_or_none()
                if prov is not None:
                    row = (await self.s.execute(select(ProviderKey).where(ProviderKey.provider_id == prov.id))).scalar_one_or_none()
                    if row is not None:
                        await self.s.delete(row)
        self._ops.clear()
        await self.s.flush()


async def ensure_provider(s: AsyncSession, name: str, d: ProviderDef | None) -> Provider:
    """Provider row (FK parent of the key). Mirrors the config's definition; created on first key/test."""
    from sourceloop.data.enums import ProviderKindEnum

    row = (await s.execute(select(Provider).where(Provider.name == name))).scalar_one_or_none()
    kind = d.kind if d is not None else ProviderKindEnum.OLLAMA
    if row is None:
        row = Provider(name=name, kind=kind, base_url=(d.base_url if d else None) or None, is_local=bool(d and d.is_local))
        s.add(row)
    elif d is not None:
        row.kind, row.base_url, row.is_local = d.kind, d.base_url or None, d.is_local
    await s.flush()
    return row
