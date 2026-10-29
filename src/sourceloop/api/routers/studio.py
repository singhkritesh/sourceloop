"""Studio API (/api/v1/studio): draft -> validate -> publish (immutable versions), rollback, diff, providers + write-only
keys, export/import. Contract: docs/api-studio.md. Every mutation appends a hash-chained `studio.*` audit event in the
SAME transaction as the change (stream 'studio'); provider keys are write-only and never appear in any response."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api.deps import Principal, require_role
from sourceloop.api.errors import ApiError
from sourceloop.api.studio_support import StudioCtx, make_ctx
from sourceloop.audit.chain import GENESIS, compute_hash
from sourceloop.data.audit_store import to_record
from sourceloop.data.db import get_session
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import AuditAnchor, AuditEvent
from sourceloop.studio.discovery import probe
from sourceloop.studio.invariants import invariant_catalog
from sourceloop.studio.lifecycle import Actor, ConfigVersion, DiffItem, LifecycleError, VersionConflict
from sourceloop.studio.schema import ProviderDef, StudioConfig, export_json_schema
from sourceloop.studio.sql_store import AUDIT_STREAM, ensure_provider
from sourceloop.studio.validator import ValidationReport, validate
from sourceloop.studio.vault import VaultError

router = APIRouter(prefix="/studio", tags=["studio"])
READ = (R.DESIGNER, R.SUPER_ADMIN, R.EVALUATOR)  # platform configuration: SBN staff only (org admins use /org/overrides)
DESIGN = (R.DESIGNER, R.SUPER_ADMIN)
ADMIN = (R.SUPER_ADMIN,)


def actor_of(p: Principal) -> Actor:
    return Actor(str(p.id), p.role)


@asynccontextmanager
async def mutation(request: Request, db: AsyncSession):  # noqa: ANN201
    """Serialise Studio writers (version numbers, audit stream head) and give the handler a ready context."""
    async with request.app.state.service.studio_lock:
        try:
            yield await make_ctx(db, request.app.state.settings, request.app.state.clock)
        except LifecycleError as e:
            await db.rollback()
            raise lifecycle_error(e) from None
        except VersionConflict:
            await db.rollback()
            raise ApiError(409, "VERSION_CONFLICT", "version number already exists; retry") from None
        except VaultError as e:
            await db.rollback()
            raise ApiError(422 if e.code == "SECRET_INVALID" else 500, e.code, "key rejected" if e.code == "SECRET_INVALID" else "vault error") from None


async def reader(request: Request, db: AsyncSession) -> StudioCtx:
    return await make_ctx(db, request.app.state.settings, request.app.state.clock)


_STATUS = {"VALIDATION_FAILED": 422, "INVARIANT_VIOLATION": 422, "NO_DRAFT": 404, "NO_ACTIVE_CONFIG": 404, "VERSION_NOT_FOUND": 404,
           "BUNDLE_TOO_LARGE": 413, "UNSUPPORTED_SCHEMA_VERSION": 422, "BUNDLE_INVALID": 422, "FORBIDDEN": 403,
           "CONFIG_TAMPERED": 409, "CONFIG_CORRUPT": 409}


def lifecycle_error(e: LifecycleError) -> ApiError:
    details: dict[str, Any] | None = None
    if e.report is not None:
        details = e.report.as_dict()
    elif e.code == "INVARIANT_VIOLATION":
        details = {"errors": [{"path": "", "code": "INVARIANT", "message": "invariant violated", "invariant_id": i}
                              for i in e.detail.get("invariants", [])], "warnings": []}
    return ApiError(_STATUS.get(e.code, 422), e.code, e.code.replace("_", " ").lower(), details=details)


def parse_or_422(raw: dict[str, Any], actor: Actor) -> ValidationReport:
    """Unparseable (unknown / smuggled keys, wrong types) is a 422 and nothing is saved. Parseable-but-invalid is allowed."""
    rep = validate(raw, is_admin=actor.is_admin)
    try:
        StudioConfig.model_validate(raw)
    except ValidationError:
        raise ApiError(422, "CONFIG_UNPARSEABLE", "configuration does not match the schema", details=rep.as_dict()) from None
    return rep


def _json(cfg_json: str) -> dict[str, Any]:
    return json.loads(cfg_json)


def version_out(v: ConfigVersion, active: int | None, with_config: bool = False) -> dict[str, Any]:
    out = {"version": v.number, "content_hash": v.content_hash, "created_by": v.author, "created_at": v.created_at.isoformat(),
           "note": v.note, "rollback_of": v.rollback_of, "active": v.number == active}
    if with_config:
        out["config"] = _json(v.payload_json)
    return out


async def draft_out(ctx: StudioCtx, actor: Actor, validation: ValidationReport | None = None) -> dict[str, Any]:
    cfg = await ctx.store.get_draft(actor.id)
    base, updated = await ctx.store.draft_meta(actor.id)
    out = {"config": cfg, "base_version": base, "updated_at": updated.isoformat() if updated else None}
    if validation is not None:
        out["validation"] = validation.as_dict()
    return out


# ---------------------------------------------------------------- schema / draft / validate
@router.get("/schema")
async def schema(_p: Principal = Depends(require_role(*READ))) -> dict[str, Any]:
    return {"schema": export_json_schema(), "invariants": invariant_catalog()}


@router.get("/draft")
async def get_draft(request: Request, p: Principal = Depends(require_role(*DESIGN)), db: AsyncSession = Depends(get_session)) -> dict:
    async with mutation(request, db) as ctx:  # creates the caller's draft from the active version when none exists
        await ctx.lifecycle.start_draft(actor_of(p))
        await db.commit()
        return await draft_out(ctx, actor_of(p))


class DraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict[str, Any]


@router.put("/draft")
async def put_draft(body: DraftBody, request: Request, p: Principal = Depends(require_role(*DESIGN)),
                    db: AsyncSession = Depends(get_session)) -> dict:
    a = actor_of(p)
    parse_or_422(body.config, a)
    async with mutation(request, db) as ctx:
        if await ctx.store.get_draft(a.id) is None:
            await ctx.lifecycle.start_draft(a)  # records the base version, then replaced below
        rep, _ = await ctx.lifecycle.save_draft(a, body.config)
        await db.commit()
        return await draft_out(ctx, a, rep)


@router.delete("/draft")
async def discard_draft(request: Request, p: Principal = Depends(require_role(*DESIGN)),
                        db: AsyncSession = Depends(get_session)) -> dict:
    async with mutation(request, db) as ctx:
        await ctx.store.delete_draft(actor_of(p).id)
        await ctx.lifecycle._emit("draft_discarded", actor_of(p), None, None)  # noqa: SLF001 - audit through the same sink
        await db.commit()
        return {"discarded": True}


class ValidateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict[str, Any] | None = None


@router.post("/validate")
async def validate_route(body: ValidateBody, request: Request, p: Principal = Depends(require_role(*DESIGN)),
                         db: AsyncSession = Depends(get_session)) -> dict:
    a = actor_of(p)
    raw = body.config if body.config is not None else await (await reader(request, db)).store.get_draft(a.id)
    if raw is None:
        raise ApiError(404, "NO_DRAFT", "no draft to validate")
    return validate(raw, is_admin=a.is_admin).as_dict()


# ---------------------------------------------------------------- publish / versions / rollback / diff
class PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(default="", max_length=300)
    base_version: int | None = None


@router.post("/publish", status_code=201)
async def publish(body: PublishBody, request: Request, p: Principal = Depends(require_role(*ADMIN)),
                  db: AsyncSession = Depends(get_session)) -> dict:
    a = actor_of(p)
    async with mutation(request, db) as ctx:
        active = await ctx.store.get_active()
        base, _ = await ctx.store.draft_meta(a.id)
        if (body.base_version is not None and body.base_version != active) or (base is not None and base != active):
            raise ApiError(409, "BASE_VERSION_STALE", "the active version changed since this draft was started; "
                                                       "discard the draft (DELETE /studio/draft) or re-import and re-apply")
        ver = await ctx.lifecycle.publish(a, body.note)
        await db.commit()
        return {"version": ver.number, "content_hash": ver.content_hash, "previous": active}


@router.get("/versions")
async def versions(request: Request, _p: Principal = Depends(require_role(*READ)), db: AsyncSession = Depends(get_session)) -> list[dict]:
    ctx = await reader(request, db)
    active = await ctx.store.get_active()
    return [version_out(v, active) for v in reversed(await ctx.store.list_versions())]


@router.get("/versions/{number}")
async def version(number: int, request: Request, _p: Principal = Depends(require_role(*READ)),
                  db: AsyncSession = Depends(get_session)) -> dict:
    ctx = await reader(request, db)
    v = await ctx.store.get_version(number)
    if v is None:
        raise ApiError(404, "VERSION_NOT_FOUND", "version not found")
    return version_out(v, await ctx.store.get_active(), with_config=True)


@router.get("/active")
async def active(request: Request, _p: Principal = Depends(require_role(*READ)), db: AsyncSession = Depends(get_session)) -> dict:
    ctx = await reader(request, db)
    n = await ctx.store.get_active()
    v = await ctx.store.get_version(n) if n is not None else None
    if v is None:
        raise ApiError(404, "NO_ACTIVE_CONFIG", "no active configuration")
    return version_out(v, n, with_config=True)


class RollbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_version: int = Field(ge=1)
    note: str = Field(default="", max_length=300)


@router.post("/rollback", status_code=201)
async def rollback(body: RollbackBody, request: Request, p: Principal = Depends(require_role(*ADMIN)),
                   db: AsyncSession = Depends(get_session)) -> dict:
    async with mutation(request, db) as ctx:
        ver = await ctx.lifecycle.rollback(actor_of(p), body.to_version, body.note)
        await db.commit()
        return {"version": ver.number, "content_hash": ver.content_hash, "rollback_of": body.to_version}


def _change(d: DiffItem) -> dict[str, Any]:
    kind = "added" if d.before is None and d.after is not None else "removed" if d.after is None and d.before is not None else "changed"
    return {"path": d.path, "kind": kind, "before": d.before, "after": d.after}


@router.get("/diff")
async def diff(request: Request, a: int = Query(ge=1), b: int = Query(ge=1), _p: Principal = Depends(require_role(*READ)),
               db: AsyncSession = Depends(get_session)) -> dict:
    ctx = await reader(request, db)
    try:
        items = await ctx.lifecycle.diff(a, b)
    except LifecycleError as e:
        raise lifecycle_error(e) from None
    return {"a": a, "b": b, "changes": [_change(d) for d in items]}


# ---------------------------------------------------------------- providers / keys
async def _provider_defs(ctx: StudioCtx, actor: Actor) -> dict[str, ProviderDef]:
    """Providers as the caller sees them: their draft when one exists, else the active version."""
    raw = await ctx.store.get_draft(actor.id)
    if raw is None:
        n = await ctx.store.get_active()
        v = await ctx.store.get_version(n) if n is not None else None
        raw = _json(v.payload_json) if v else {}
    out: dict[str, ProviderDef] = {}
    for name, d in (raw.get("providers") or {}).items():
        try:
            out[name] = ProviderDef.model_validate(d)
        except ValidationError:
            continue  # an unparseable provider in a draft is reported by /validate, not listed here
    return out


@router.get("/providers")
async def providers(request: Request, p: Principal = Depends(require_role(*READ)), db: AsyncSession = Depends(get_session)) -> list[dict]:
    ctx = await reader(request, db)
    defs = await _provider_defs(ctx, actor_of(p))
    return [{"name": n, "kind": d.kind.value, "base_url": d.base_url, "is_local": d.is_local, "has_key": ctx.vault.has_key(n)}
            for n, d in sorted(defs.items())]


class KeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    secret: str = Field(min_length=1, max_length=4096, repr=False)


async def _need_provider(ctx: StudioCtx, a: Actor, name: str) -> dict[str, ProviderDef]:
    defs = await _provider_defs(ctx, a)
    if name not in defs:
        raise ApiError(404, "PROVIDER_NOT_FOUND", "provider is not defined in the draft or active configuration")
    return defs


@router.put("/providers/{name}/key")
async def set_key(name: str, body: KeyBody, request: Request, p: Principal = Depends(require_role(*ADMIN)),
                  db: AsyncSession = Depends(get_session)) -> dict:
    a = actor_of(p)
    async with mutation(request, db) as ctx:
        defs = await _need_provider(ctx, a, name)
        await ctx.lifecycle.set_provider_key(a, name, body.secret)
        await ctx.backend.flush(defs, a.id)
        await db.commit()
        return {"has_key": True}


@router.delete("/providers/{name}/key")
async def delete_key(name: str, request: Request, p: Principal = Depends(require_role(*ADMIN)),
                     db: AsyncSession = Depends(get_session)) -> dict:
    a = actor_of(p)
    async with mutation(request, db) as ctx:
        defs = await _need_provider(ctx, a, name)
        await ctx.lifecycle.delete_provider_key(a, name)
        await ctx.backend.flush(defs, a.id)
        await db.commit()
        return {"has_key": False}


@router.post("/providers/{name}/test")
async def test_provider(name: str, request: Request, p: Principal = Depends(require_role(*ADMIN)),
                        db: AsyncSession = Depends(get_session)) -> dict:
    a = actor_of(p)
    ctx = await reader(request, db)
    defs = await _need_provider(ctx, a, name)
    res = await probe(name, defs[name], ctx.vault)  # network I/O outside any write lock; result carries codes only
    async with mutation(request, db) as wctx:
        row = await ensure_provider(db, name, defs[name])
        row.last_test_at, row.last_test_ok, row.last_test_detail = request.app.state.clock.now(), res["ok"], (res.get("error") or "OK")[:40]
        await wctx.lifecycle.record_provider_test(a, name, res["ok"], res.get("error") or "OK")
        await db.commit()
    out: dict[str, Any] = {"ok": res["ok"], "models": res["models"]}
    if res.get("error"):
        out["error"] = res["error"]
    return out


# ---------------------------------------------------------------- export / import
@router.get("/export")
async def export(request: Request, version: int | None = Query(None, ge=1), p: Principal = Depends(require_role(*ADMIN)),
                 db: AsyncSession = Depends(get_session)) -> dict:
    async with mutation(request, db) as ctx:
        n = version if version is not None else await ctx.store.get_active()
        if n is None:
            raise ApiError(404, "NO_ACTIVE_CONFIG", "no active configuration")
        bundle = await ctx.lifecycle.export_bundle(actor_of(p), n)
        await db.commit()
        return bundle


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle: dict[str, Any]


@router.post("/import")
async def import_bundle(body: ImportBody, request: Request, p: Principal = Depends(require_role(*ADMIN)),
                        db: AsyncSession = Depends(get_session)) -> dict:
    a = actor_of(p)
    cfg = body.bundle.get("config")
    if not isinstance(cfg, dict):
        raise ApiError(422, "BUNDLE_INVALID", "bundle.config is required")
    probe_raw = json.loads(json.dumps(cfg, default=str))
    for pd in (probe_raw.get("providers") or {}).values():
        if isinstance(pd, dict):
            pd["key_ref"] = None
    parse_or_422(probe_raw, a)
    async with mutation(request, db) as ctx:
        await ctx.store.delete_draft(a.id)  # a fresh draft against the CURRENT active version
        rep = await ctx.lifecycle.import_bundle(a, body.bundle)
        await db.commit()
        return await draft_out(ctx, a, rep)


# ---------------------------------------------------------------- audit (studio stream)
@router.get("/audit")
async def studio_audit(request: Request, limit: int = Query(50, ge=1, le=500), before_seq: int | None = Query(None, ge=1),
                       _p: Principal = Depends(require_role(*READ)), db: AsyncSession = Depends(get_session)) -> dict:
    """Newest-first page of the studio stream, read and verified in SQL-sized pages (never the whole stream).

    `before_seq` is the cursor (pass `next_cursor` to get the next, older page). Verification is incremental: the page is
    re-hashed and its first record must chain to the stored record just before it (`page_valid`); the head record must
    match the out-of-band anchor (`head_anchored`, always checked). `valid` = both. A full-stream walk is available by
    following the cursor to the end (every page verified)."""
    stmt = select(AuditEvent).where(AuditEvent.project_id == AUDIT_STREAM)
    if before_seq is not None:
        stmt = stmt.where(AuditEvent.seq < before_seq)
    rows = list((await db.execute(stmt.order_by(AuditEvent.seq.desc()).limit(limit))).scalars())
    rows.reverse()  # ascending for verification
    total = (await db.execute(select(func.count()).select_from(AuditEvent).where(AuditEvent.project_id == AUDIT_STREAM))).scalar_one()
    head = (await db.execute(select(AuditEvent.seq, AuditEvent.hash).where(AuditEvent.project_id == AUDIT_STREAM)
                             .order_by(AuditEvent.seq.desc()).limit(1))).first()
    anchor = await db.get(AuditAnchor, AUDIT_STREAM)
    head_ok = True
    if anchor is not None and anchor.seq > 0:
        head_ok = head is not None and head[0] >= anchor.seq and not (head[0] == anchor.seq and head[1] != anchor.hash)
    page_ok, bad = True, None
    if rows:
        prev_hash, expected = GENESIS, 1
        if rows[0].seq > 1:  # link to the stored predecessor
            prev = (await db.execute(select(AuditEvent.seq, AuditEvent.hash).where(
                AuditEvent.project_id == AUDIT_STREAM, AuditEvent.seq == rows[0].seq - 1))).first()
            if prev is None:
                page_ok, bad = False, rows[0].seq - 1
            else:
                prev_hash, expected = prev[1], prev[0] + 1
        if page_ok:
            for e in rows:
                r = to_record(e)
                if r.seq != expected or r.prev_hash != prev_hash or compute_hash(r) != r.hash:
                    page_ok, bad = False, r.seq
                    break
                prev_hash, expected = r.hash, expected + 1
    if not head_ok and bad is None:
        bad = (head[0] + 1) if head else 1
    items = [{"seq": e.seq, "ts": e.ts.isoformat(), "action": e.action, "actor_id": e.actor_id, "config_version": e.config_version,
              "evidence": e.evidence, "hash": e.hash} for e in rows][::-1]
    more = bool(rows) and rows[0].seq > 1
    return {"valid": page_ok and head_ok, "page_valid": page_ok, "head_anchored": head_ok, "first_bad_seq": bad,
            "events": total, "items": items, "next_cursor": rows[0].seq if more else None}
