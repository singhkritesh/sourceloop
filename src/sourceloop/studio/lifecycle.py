"""Draft -> validate -> publish (immutable, versioned, content-hashed), rollback, diff, export/import, pinning.

Pure library: persistence is the StudioStore Protocol (MemoryStudioStore here; the API layer adds a SQL store).
Every mutation produces a `studio.*` audit event dict and passes it to the injected sink (audit first, then the
change becomes live). Provider keys never pass through here: configs carry only `key_ref`."""
from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from sourceloop.audit.canonical import dumps, sha256_hex
from sourceloop.clock import Clock, SystemClock
from sourceloop.data.enums import RoleEnum
from sourceloop.studio.invariants import SECRET_KEY_NAMES, check_invariants
from sourceloop.studio.schema import SCHEMA_VERSION, StudioConfig
from sourceloop.studio.validator import ValidationReport, validate
from sourceloop.studio.vault import Vault

MAX_BUNDLE_BYTES = 2 * 1024 * 1024
AuditSink = Callable[[dict[str, Any]], Awaitable[None] | None]


class LifecycleError(Exception):
    def __init__(self, code: str, report: ValidationReport | None = None, **detail: Any):
        super().__init__(code)
        self.code, self.report, self.detail = code, report, detail


class VersionConflict(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Actor:
    id: str
    role: RoleEnum

    @property
    def is_admin(self) -> bool:
        return self.role is RoleEnum.SUPER_ADMIN

    @property
    def can_design(self) -> bool:
        return self.role in (RoleEnum.SUPER_ADMIN, RoleEnum.DESIGNER)


class ConfigVersion(BaseModel):
    """Immutable published row. The config is held as canonical JSON text so it cannot be mutated in place."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    number: int
    content_hash: str
    payload_json: str
    author: str
    note: str = ""
    created_at: datetime
    rollback_of: int | None = None
    status: Literal["PUBLISHED", "ARCHIVED"] = "PUBLISHED"

    def config(self) -> StudioConfig:
        return StudioConfig.model_validate_json(self.payload_json)


class StudioStore(Protocol):
    async def get_draft(self, owner: str) -> dict[str, Any] | None: ...
    async def save_draft(self, owner: str, payload: dict[str, Any]) -> None: ...
    async def delete_draft(self, owner: str) -> None: ...
    async def latest_number(self) -> int: ...
    async def add_version(self, v: ConfigVersion) -> None: ...   # raises VersionConflict if the number exists
    async def get_version(self, number: int) -> ConfigVersion | None: ...
    async def list_versions(self) -> list[ConfigVersion]: ...
    async def set_active(self, number: int) -> None: ...         # archives the previous active
    async def get_active(self) -> int | None: ...


class MemoryStudioStore:
    def __init__(self) -> None:
        self._drafts: dict[str, dict[str, Any]] = {}
        self._versions: dict[int, ConfigVersion] = {}
        self._active: int | None = None
        self._lock = asyncio.Lock()

    async def get_draft(self, owner: str) -> dict[str, Any] | None:
        d = self._drafts.get(owner)
        return json.loads(json.dumps(d)) if d is not None else None

    async def save_draft(self, owner: str, payload: dict[str, Any]) -> None:
        self._drafts[owner] = json.loads(json.dumps(payload))

    async def delete_draft(self, owner: str) -> None:
        self._drafts.pop(owner, None)

    async def latest_number(self) -> int:
        return max(self._versions, default=0)

    async def add_version(self, v: ConfigVersion) -> None:
        async with self._lock:
            if v.number in self._versions:
                raise VersionConflict(str(v.number))
            self._versions[v.number] = v

    async def get_version(self, number: int) -> ConfigVersion | None:
        return self._versions.get(number)

    async def list_versions(self) -> list[ConfigVersion]:
        return [self._versions[n] for n in sorted(self._versions)]

    async def set_active(self, number: int) -> None:
        if self._active is not None and self._active in self._versions:
            self._versions[self._active] = self._versions[self._active].model_copy(update={"status": "ARCHIVED"})
        self._active = number

    async def get_active(self) -> int | None:
        return self._active


def config_payload(cfg: StudioConfig) -> dict[str, Any]:
    return cfg.model_dump(mode="json")


def content_hash(cfg: StudioConfig) -> str:
    return sha256_hex(dumps(config_payload(cfg)))


@dataclass(frozen=True, slots=True)
class DiffItem:
    path: str
    before: Any
    after: Any


def _flatten(obj: Any, path: str = "") -> dict[str, Any]:
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            out.update(_flatten(v, f"{path}.{k}" if path else str(k)))
        return out or {path: {}}
    return {path: obj}


def _redact(path: str, value: Any) -> Any:
    last = path.rsplit(".", 1)[-1]
    if value is not None and (last == "key_ref" or SECRET_KEY_NAMES.search(last)):
        return "****"
    return value


def diff_configs(a: StudioConfig, b: StudioConfig) -> list[DiffItem]:
    """Structured diff (path, before, after). Secret-like paths are masked; real keys are not in a config at all."""
    fa, fb = _flatten(config_payload(a)), _flatten(config_payload(b))
    items = []
    for p in sorted(set(fa) | set(fb)):
        if fa.get(p, _MISSING) != fb.get(p, _MISSING):
            items.append(DiffItem(p, _redact(p, fa.get(p)), _redact(p, fb.get(p))))
    return items


_MISSING = object()


class StudioLifecycle:
    def __init__(self, store: StudioStore, audit: AuditSink | None = None, clock: Clock | None = None,
                 vault: Vault | None = None):
        self.store, self._sink, self.clock, self.vault = store, audit, clock or SystemClock(), vault

    # ---- helpers ----
    async def _emit(self, action: str, actor: Actor, version: int | None, hsh: str | None, **detail: Any) -> dict[str, Any]:
        ev = {"action": f"studio.{action}", "actor": actor.id, "actor_role": actor.role.value, "version": version,
              "hash": hsh, "ts": self.clock.now().isoformat(), "detail": detail}
        if self._sink is not None:
            r = self._sink(ev)
            if asyncio.iscoroutine(r):
                await r
        return ev

    @staticmethod
    def _need(actor: Actor, admin: bool = False) -> None:
        if not (actor.is_admin if admin else actor.can_design):
            raise LifecycleError("FORBIDDEN")

    # ---- draft ----
    async def save_draft(self, actor: Actor, payload: Mapping[str, Any]) -> tuple[ValidationReport, dict[str, Any]]:
        """Always saves (invalid drafts are allowed); returns inline validation and the audit event."""
        self._need(actor)
        raw = json.loads(json.dumps(payload, default=str))
        await self.store.save_draft(actor.id, raw)
        rep = validate(raw, is_admin=actor.is_admin)
        ev = await self._emit("draft_saved", actor, None, sha256_hex(dumps(raw)), errors=len(rep.errors),
                              warnings=len(rep.warnings))
        return rep, ev

    async def start_draft(self, actor: Actor) -> dict[str, Any]:
        """The caller's draft; created from the active version when none exists."""
        self._need(actor)
        d = await self.store.get_draft(actor.id)
        if d is None:
            n = await self.store.get_active()
            if n is None:
                raise LifecycleError("NO_ACTIVE_CONFIG")
            v = await self.store.get_version(n)
            if v is None:
                raise LifecycleError("VERSION_NOT_FOUND")
            d = json.loads(v.payload_json)
            await self.store.save_draft(actor.id, d)
        return d

    async def validate_draft(self, actor: Actor) -> ValidationReport:
        self._need(actor)
        d = await self.store.get_draft(actor.id)
        if d is None:
            raise LifecycleError("NO_DRAFT")
        return validate(d, is_admin=actor.is_admin)

    # ---- publish / rollback ----
    async def publish(self, actor: Actor, note: str = "", *, payload: Mapping[str, Any] | None = None,
                      rollback_of: int | None = None) -> ConfigVersion:
        self._need(actor, admin=True)
        raw = dict(payload) if payload is not None else await self.store.get_draft(actor.id)
        if raw is None:
            raise LifecycleError("NO_DRAFT")
        rep = validate(raw, is_admin=True)
        if not rep.ok:
            raise LifecycleError("VALIDATION_FAILED", rep)
        cfg = StudioConfig.model_validate(raw)
        if (viol := check_invariants(cfg)):  # belt and braces: same gate the runtime applies
            raise LifecycleError("INVARIANT_VIOLATION", invariants=[v.invariant_id for v in viol])
        prev_n = await self.store.get_active()
        prev = await self.store.get_version(prev_n) if prev_n is not None else None
        number = await self.store.latest_number() + 1
        ver = ConfigVersion(number=number, content_hash=content_hash(cfg), payload_json=dumps(config_payload(cfg)),
                            author=actor.id, note=note[:300], created_at=self.clock.now(), rollback_of=rollback_of)
        await self.store.add_version(ver)
        changed = [d.path for d in diff_configs(prev.config(), cfg)] if prev else ["*"]
        await self._emit("rolled_back" if rollback_of else "published", actor, number, ver.content_hash,
                         previous=prev_n, rollback_of=rollback_of, changed_paths=changed[:200],
                         warnings=[w.code for w in rep.warnings])
        await self.store.set_active(number)  # live only after the audit event exists
        if payload is None:
            await self.store.delete_draft(actor.id)
        return ver

    async def rollback(self, actor: Actor, to_version: int, note: str = "") -> ConfigVersion:
        """Re-validated copy of an old version, published as a NEW version (invariants may have been tightened)."""
        self._need(actor, admin=True)
        old = await self.store.get_version(to_version)
        if old is None:
            raise LifecycleError("VERSION_NOT_FOUND")
        return await self.publish(actor, note or f"rollback to v{to_version}", payload=json.loads(old.payload_json),
                                  rollback_of=to_version)

    # ---- diff ----
    async def diff(self, a: int, b: int) -> list[DiffItem]:
        va, vb = await self.store.get_version(a), await self.store.get_version(b)
        if va is None or vb is None:
            raise LifecycleError("VERSION_NOT_FOUND")
        return diff_configs(va.config(), vb.config())

    # ---- export / import ----
    async def export_bundle(self, actor: Actor, number: int) -> dict[str, Any]:
        self._need(actor, admin=True)
        v = await self.store.get_version(number)
        if v is None:
            raise LifecycleError("VERSION_NOT_FOUND")
        cfg = json.loads(v.payload_json)
        for p in cfg.get("providers", {}).values():
            p["key_ref"] = None  # providers travel by name; secrets/ids never do
        bundle = {"schema_version": cfg.get("schema_version", SCHEMA_VERSION), "config": cfg,
                  "prompts": [{"agent": k, **p} for k, p in cfg.get("prompts", {}).items()],
                  "scenarios": [{"id": k, **s} for k, s in cfg.get("scenarios", {}).items()]}
        await self._emit("exported", actor, number, v.content_hash)
        return bundle

    async def import_bundle(self, actor: Actor, bundle: Mapping[str, Any]) -> ValidationReport:
        """Bundle -> caller's DRAFT only; validated like manual edits."""
        self._need(actor, admin=True)
        if len(dumps(bundle).encode()) > MAX_BUNDLE_BYTES:
            raise LifecycleError("BUNDLE_TOO_LARGE")
        if bundle.get("schema_version") != SCHEMA_VERSION:
            raise LifecycleError("UNSUPPORTED_SCHEMA_VERSION", needed=SCHEMA_VERSION)
        cfg = bundle.get("config")
        if not isinstance(cfg, Mapping):
            raise LifecycleError("BUNDLE_INVALID")
        cfg = json.loads(json.dumps(cfg, default=str))
        for p in (cfg.get("providers") or {}).values():
            if isinstance(p, dict):
                p["key_ref"] = None  # never import secret references
        rep, _ = await self.save_draft(actor, cfg)
        await self._emit("imported", actor, None, sha256_hex(dumps(cfg)), errors=len(rep.errors))
        return rep

    # ---- provider keys (audit without value) ----
    async def set_provider_key(self, actor: Actor, provider: str, secret: str) -> dict[str, Any]:
        self._need(actor, admin=True)
        if self.vault is None:
            raise LifecycleError("NO_VAULT")
        stamp = self.vault.put_key(provider, secret)
        return await self._emit("provider_key_set", actor, None, None, provider=provider, updated_at=stamp)


    async def delete_provider_key(self, actor: Actor, provider: str) -> dict[str, Any]:
        self._need(actor, admin=True)
        if self.vault is None:
            raise LifecycleError("NO_VAULT")
        self.vault.delete_key(provider)
        return await self._emit("provider_key_deleted", actor, None, None, provider=provider)

    async def record_provider_test(self, actor: Actor, provider: str, ok: bool, code: str) -> dict[str, Any]:
        """Audit a connectivity test (code only: never upstream text, never a key)."""
        self._need(actor, admin=True)
        return await self._emit("provider_tested", actor, None, None, provider=provider, ok=ok, code=code)


class InvariantViolationError(Exception):
    def __init__(self, ids: list[str]):
        super().__init__(",".join(ids))
        self.ids = ids


class ConfigResolver:
    """Projects pin a config version number; the runtime loads config ONLY through this (never 'active' mid-run).
    Verifies the content hash and re-checks invariants on load; returns the cached immutable StudioConfig."""

    def __init__(self, store: StudioStore, max_cache: int = 64):
        self.store, self._max = store, max_cache
        self._cache: OrderedDict[int, StudioConfig] = OrderedDict()

    async def get(self, version_id: int) -> StudioConfig:
        if version_id in self._cache:
            self._cache.move_to_end(version_id)
            return self._cache[version_id]
        v = await self.store.get_version(version_id)
        if v is None:
            raise LifecycleError("VERSION_NOT_FOUND")
        try:
            cfg = v.config()
        except ValidationError:
            raise LifecycleError("CONFIG_CORRUPT") from None
        if content_hash(cfg) != v.content_hash:
            raise LifecycleError("CONFIG_TAMPERED")
        if (viol := check_invariants(cfg)):
            raise InvariantViolationError(sorted({x.invariant_id for x in viol}))
        self._cache[version_id] = cfg
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)
        return cfg

    async def active(self) -> tuple[int, StudioConfig]:
        """Only for `project start` (copy into project.config_version_id). Never call mid-run."""
        n = await self.store.get_active()
        if n is None:
            raise LifecycleError("NO_ACTIVE_CONFIG")
        return n, await self.get(n)
