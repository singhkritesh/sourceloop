"""Per-request Studio wiring: SQL store + vault + lifecycle over ONE session (the router commits once), boot of the
default config, and pinned-config loading for the runtime (rule 15a: projects read config ONLY through here)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api.config import Settings
from sourceloop.api.errors import ApiError
from sourceloop.clock import Clock, SystemClock
from sourceloop.data import db as dbm
from sourceloop.data.enums import RoleEnum
from sourceloop.studio.defaults import default_config
from sourceloop.studio.lifecycle import (
    Actor,
    ConfigResolver,
    InvariantViolationError,
    LifecycleError,
    StudioLifecycle,
)
from sourceloop.studio.schema import StudioConfig
from sourceloop.studio.sql_store import SqlStudioStore, SqlVaultBackend, audit_sink
from sourceloop.studio.vault import Vault, VaultError, _fernet_from_env

log = structlog.get_logger("sourceloop.studio")
SYSTEM_ACTOR = Actor("system", RoleEnum.SUPER_ADMIN)


@dataclass
class StudioCtx:
    store: SqlStudioStore
    backend: SqlVaultBackend
    vault: Vault
    lifecycle: StudioLifecycle
    resolver: ConfigResolver


_ENV_FERNET: Fernet | None = None


def fernet_for(settings: Settings) -> Fernet:
    global _ENV_FERNET
    if not settings.config_encryption_key:  # SOURCELOOP_VAULT_KEY or the loud dev key; resolved (and warned about) once
        if _ENV_FERNET is None:
            _ENV_FERNET = _fernet_from_env()
        return _ENV_FERNET
    try:
        return Fernet(settings.config_encryption_key.encode())
    except Exception:  # noqa: BLE001 - never echo the value
        raise VaultError("VAULT_KEY_INVALID") from None


async def make_ctx(db: AsyncSession, settings: Settings, clock: Clock | None = None) -> StudioCtx:
    clock = clock or SystemClock()
    store = SqlStudioStore(db, clock)
    backend = await SqlVaultBackend(db).load()
    vault = Vault(backend, clock, fernet_for(settings))
    lc = StudioLifecycle(store, audit_sink(db, clock), clock, vault)
    return StudioCtx(store, backend, vault, lc, ConfigResolver(store))


async def ensure_default_config(settings: Settings, lock: asyncio.Lock | None = None) -> int:
    """First boot / seed: publish `default_config()` as version 1 when no version exists. Idempotent."""
    async def _go() -> int:
        async with dbm.session_scope() as s:
            ctx = await make_ctx(s, settings)
            n = await ctx.store.get_active()
            if n is not None:
                return n
            if await ctx.store.latest_number() > 0:  # versions exist but none active: leave to an ADMIN, do not guess
                raise LifecycleError("NO_ACTIVE_CONFIG")
            ver = await ctx.lifecycle.publish(SYSTEM_ACTOR, "bootstrap: default configuration",
                                              payload=default_config().model_dump(mode="json"))
            log.info("studio.default_config_published", version=ver.number)
            return ver.number

    if lock is None:
        return await _go()
    async with lock:
        return await _go()


@dataclass(frozen=True)
class Pinned:
    row_id: object  # config_version.id (stored on project.config_version_id)
    number: int
    cfg: StudioConfig


def api_error_for(e: Exception) -> ApiError:
    """Integrity failures of a pinned config are loud and typed; nothing about the content leaks."""
    if isinstance(e, InvariantViolationError):
        return ApiError(409, "CONFIG_INVARIANT_VIOLATION", "the pinned configuration violates a safety invariant")
    code = e.code if isinstance(e, LifecycleError) else "CONFIG_ERROR"
    return ApiError(409, code, f"configuration unavailable: {code}")


async def active_pin(db: AsyncSession, settings: Settings, lock: asyncio.Lock | None = None) -> Pinned:
    """The version a NEW project pins. Loaded through the resolver (hash + invariants verified)."""
    ctx = await make_ctx(db, settings)
    if await ctx.store.get_active() is None:
        await ensure_default_config(settings, lock)
        await db.rollback()
        ctx = await make_ctx(db, settings)
    try:
        n, cfg = await ctx.resolver.active()
    except (LifecycleError, InvariantViolationError) as e:
        raise api_error_for(e) from None
    row = await ctx.store.row_id(n)
    assert row is not None
    return Pinned(row, n, cfg)


async def pinned_config(db: AsyncSession, settings: Settings, row_id: object) -> Pinned:
    """Config of an existing project, by its pinned `config_version_id` (never 'active')."""
    ctx = await make_ctx(db, settings)
    n = await ctx.store.number_of(row_id)  # type: ignore[arg-type]
    if n is None:
        raise ApiError(409, "VERSION_NOT_FOUND", "pinned configuration version is missing")
    try:
        cfg = await ctx.resolver.get(n)
    except (LifecycleError, InvariantViolationError) as e:
        raise api_error_for(e) from None
    return Pinned(row_id, n, cfg)
