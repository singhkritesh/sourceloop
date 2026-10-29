from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api import security, sse
from sourceloop.api.deps import (
    ACCESS_COOKIE,
    BUYER_SIDE,
    BUYER_VIEW,
    INSPECTORS,
    PROJECT_READERS,
    Principal,
    create_limit,
    get_visible_project,
    is_revoked,
    limiter,
    own_supplier_ids,
    require_role,
    start_limit,
    supplier_scoped,
    visible_projects,
)
from sourceloop.api.errors import ApiError
from sourceloop.api.routers.approvals import approval_out
from sourceloop.api.service import ProjectService
from sourceloop.audit.chain import AuditRecord, verify_chain
from sourceloop.bus.bus import ai_generated
from sourceloop.data import db as dbm
from sourceloop.data.audit_store import to_record
from sourceloop.data.db import get_session
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import (
    Approval,
    AuditAnchor,
    AuditEvent,
    Contract,
    Message,
    Organization,
    Project,
    User,
)
from sourceloop.data.synthetic import CATEGORIES, CERTS, COUNTRIES, INDUSTRY

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    need_text: str = Field(default="")  # length is checked in the handler so oversize is 413, not 422
    scenario: str = Field(default="happy", pattern=r"^[a-z][a-z0-9_]{0,39}$")  # must exist in the pinned config
    need_index: int | None = Field(default=None, ge=0, le=11)  # None => the scenario's own need_index (default 0)
    seed: int = Field(default=42, ge=0, le=10_000)
    # OPTIONAL structured need (R2-5): when present these REPLACE the synthetic need's fields in the brief / mandate /
    # discovery; when absent the scenario's synthetic need is used. `need_text` stays display-only (never parsed).
    category: str | None = None
    industry: str | None = None
    volume_units: int | None = Field(default=None, ge=1, le=10_000_000)
    required_certs: list[str] | None = Field(default=None, max_length=len(CERTS))
    excluded_countries: list[str] | None = Field(default=None, max_length=len(COUNTRIES))
    max_lead_time_weeks: int | None = Field(default=None, ge=1, le=104)
    target_price: Decimal | None = Field(default=None, gt=0, lt=Decimal("100000000"), max_digits=14, decimal_places=4,
                                         allow_inf_nan=False)

    @field_validator("category")
    @classmethod
    def _cat(cls, v: str | None) -> str | None:
        if v is not None and v not in CATEGORIES:
            raise ValueError("unknown category")
        return v

    @field_validator("industry")
    @classmethod
    def _ind(cls, v: str | None) -> str | None:
        if v is not None and v not in set(INDUSTRY.values()):
            raise ValueError("unknown industry")
        return v

    @field_validator("required_certs")
    @classmethod
    def _certs(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and (len(set(v)) != len(v) or not set(v) <= set(CERTS)):
            raise ValueError("unknown or duplicate certificate")
        return v

    @field_validator("excluded_countries")
    @classmethod
    def _countries(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and (len(set(v)) != len(v) or not set(v) <= set(COUNTRIES)):
            raise ValueError("unknown or duplicate country (ISO 3166 alpha-2 from the registry)")
        return v

    def need_fields(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k in ("category", "industry", "volume_units", "max_lead_time_weeks"):
            if getattr(self, k) is not None:
                out[k] = getattr(self, k)
        for k in ("required_certs", "excluded_countries"):
            if getattr(self, k) is not None:
                out[k] = list(getattr(self, k))
        if self.target_price is not None:
            out["target_price"] = str(self.target_price)
        return out


def svc(request: Request) -> ProjectService:
    return request.app.state.service


def project_out(proj: Project, p: Principal) -> dict[str, Any]:
    base = {"id": str(proj.id), "title": proj.title, "stage": proj.stage.value, "status": proj.status.value,
            "outcome": proj.outcome.value if proj.outcome else None, "version": proj.version,
            "created_at": proj.created_at.isoformat(), "updated_at": proj.updated_at.isoformat()}
    if supplier_scoped(p):  # the buyer's brief / need text / budget are not for the counterparty
        return {**base, "title": f"Sourcing request {str(proj.id)[:8]}"}  # neutral label: the buyer's title is theirs
    if p.role in BUYER_SIDE:  # a business user sees the business: no model mode, token spend, config pin or org ids
        return {**base, "need_text": proj.need_text, "brief": _business_brief(proj.brief), "reason": proj.reason}
    return {**base, "need_text": proj.need_text, "brief": proj.brief, "reason": proj.reason,
            "tokens_used": proj.tokens_used, "llm_mode": proj.llm_mode,
            "config_version": (proj.brief or {}).get("config_version") if isinstance(proj.brief, dict) else None, "buyer_org_id": str(proj.buyer_org_id)}


def _business_brief(brief: Any) -> dict[str, Any]:
    """The need as the buyer stated it. The run's technical pins (scenario, seed, config version, token cap, replay fingerprints) stay out."""
    b = brief if isinstance(brief, dict) else {}
    keep = ("category", "industry", "volume_units", "required_certificates", "excluded_countries", "max_lead_time_weeks",
            "target_unit_price", "spec_summary")
    return {k: b[k] for k in keep if k in b}


_TERMINAL = ("DONE", "STOPPED")


async def supplier_project_out(db: AsyncSession, proj: Project, p: Principal) -> dict[str, Any]:
    """What a supplier may know about a project: a neutral label, the stage as of ITS OWN last interaction, and once the
    project is over a neutral CLOSED (outcome DEAL only for the awarded supplier). No internal status, no activity timing."""
    base = project_out(proj, p)
    mine = [f"supplier:{s}" for s in await own_supplier_ids(db, p, proj.id)]
    last = await _last_own_seq(db, proj.id, mine)
    stage = "NEED"
    if last:
        st = (await db.execute(select(AuditEvent.outcome).where(AuditEvent.project_id == str(proj.id), AuditEvent.action == "stage_changed",
                                                                 AuditEvent.seq <= last).order_by(AuditEvent.seq.desc()).limit(1))).scalar_one_or_none()
        stage = st or stage
    closed = proj.status.value in _TERMINAL
    outcome = None
    if closed:
        won = (await db.execute(select(Contract.supplier_id).where(Contract.project_id == proj.id))).scalar_one_or_none()
        owns = won is not None and won in {m.split(":", 1)[1] for m in mine}
        outcome = "DEAL" if (owns and proj.outcome and proj.outcome.value == "DEAL") else "CLOSED"
    return {**base, "stage": stage, "status": "DONE" if closed else "RUNNING", "outcome": outcome, "version": 0,
            "updated_at": base["updated_at"] if closed else base["created_at"]}


@router.post("", status_code=201)
@limiter.limit(create_limit)
async def create_project(request: Request, body: ProjectCreate, p: Principal = Depends(require_role(R.BUYER)),
                         db: AsyncSession = Depends(get_session)) -> dict:
    if len(body.need_text) > 4000:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "need_text exceeds 4000 characters")
    proj = await svc(request).create_project(db, p, title=security.sanitize_text(body.title),
                                             need_text=security.sanitize_text(body.need_text), scenario=body.scenario,
                                             need_index=body.need_index, seed=body.seed, need_fields=body.need_fields())
    return project_out(proj, p)


@router.get("")
async def list_projects(p: Principal = Depends(require_role(*PROJECT_READERS)), db: AsyncSession = Depends(get_session),
                        limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
    rows = (await db.execute(visible_projects(p).order_by(Project.created_at.desc(), Project.id).limit(limit).offset(offset))).scalars()
    if supplier_scoped(p):
        return {"items": [await supplier_project_out(db, x, p) for x in rows], "limit": limit, "offset": offset}
    return {"items": [project_out(x, p) for x in rows], "limit": limit, "offset": offset}


@router.get("/{pid}")
async def get_project(pid: uuid.UUID, p: Principal = Depends(require_role(*PROJECT_READERS)),
                      db: AsyncSession = Depends(get_session)) -> dict:
    proj = await get_visible_project(db, p, pid)
    return await supplier_project_out(db, proj, p) if supplier_scoped(p) else project_out(proj, p)


@router.post("/{pid}/start", status_code=202)
@limiter.shared_limit(start_limit, scope="run_start")
async def start_project(pid: uuid.UUID, request: Request, p: Principal = Depends(require_role(R.BUYER)),
                        db: AsyncSession = Depends(get_session)) -> dict:
    await get_visible_project(db, p, pid)
    await db.rollback()
    await svc(request).start(pid)
    return {"id": str(pid), "status": "RUNNING"}


def _buyer_side_only(p: Principal) -> None:
    if supplier_scoped(p):
        raise ApiError(403, "FORBIDDEN", "role not permitted")


@router.post("/{pid}/resume", status_code=202)
@limiter.shared_limit(start_limit, scope="run_start")
async def resume_project(pid: uuid.UUID, request: Request, p: Principal = Depends(require_role(R.BUYER, R.ADMIN, R.SUPER_ADMIN)),
                         db: AsyncSession = Depends(get_session)) -> dict:
    _buyer_side_only(p)
    await get_visible_project(db, p, pid)
    await db.rollback()
    if svc(request).active(pid):  # idempotent
        return {"id": str(pid), "status": "RUNNING"}
    await svc(request).resume(pid)
    return {"id": str(pid), "status": "RUNNING"}


@router.post("/{pid}/stop")
async def stop_project(pid: uuid.UUID, request: Request, p: Principal = Depends(require_role(R.BUYER, R.ADMIN, R.SUPER_ADMIN)),
                       db: AsyncSession = Depends(get_session)) -> dict:
    _buyer_side_only(p)
    await get_visible_project(db, p, pid)
    await db.rollback()
    await svc(request).stop(pid, p)
    return {"id": str(pid), "status": "STOPPED", "outcome": "STOPPED_BY_USER"}


# ---------------------------------------------------------------- messages / audit (side-filtered)
def _msg_out(m: Message, seq: int | None = None) -> dict[str, Any]:
    return {"id": str(m.id), "seq": m.seq if seq is None else seq, "stage": m.stage.value, "type": m.type.value,
            "from_agent": m.from_agent, "to_agent": m.to_agent, "ai_generated": ai_generated(m.from_agent),
            "in_reply_to": str(m.in_reply_to) if m.in_reply_to else None, "payload": m.payload,
            "ts": m.ts.isoformat() if m.ts else None}


def _ev_from() -> Any:
    return AuditEvent.evidence[0]["from"].as_string()


def _ev_to() -> Any:
    return AuditEvent.evidence[0]["to"].as_string()


def _own_msg_pred(mine: list[str]) -> Any:
    return and_(AuditEvent.action == "message_delivered", or_(_ev_from().in_(mine), _ev_to().in_(mine)))


async def _last_own_seq(db: AsyncSession, pid: uuid.UUID, mine: list[str]) -> int | None:
    if not mine:
        return None
    return (await db.execute(select(func.max(AuditEvent.seq)).where(AuditEvent.project_id == str(pid), _own_msg_pred(mine)))).scalar_one()


@router.get("/{pid}/messages")
async def messages(pid: uuid.UUID, p: Principal = Depends(require_role(R.SUPPLIER, *INSPECTORS)), db: AsyncSession = Depends(get_session),
                   after_seq: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)) -> dict:
    await get_visible_project(db, p, pid)
    if supplier_scoped(p):  # SQL-level side filter, densely renumbered: gaps would reveal how much other traffic exists
        mine = [f"supplier:{s}" for s in await own_supplier_ids(db, p, pid)]
        if not mine:
            return {"items": [], "next_after_seq": after_seq}
        rn = func.row_number().over(order_by=Message.seq).label("rn")
        sub = select(Message.id.label("mid"), rn).where(Message.project_id == pid,
                                                        Message.from_agent.in_(mine) | Message.to_agent.in_(mine)).subquery()
        rows = (await db.execute(select(Message, sub.c.rn).join(sub, sub.c.mid == Message.id).where(sub.c.rn > after_seq)
                                 .order_by(sub.c.rn).limit(limit))).all()
        return {"items": [_msg_out(m, r) for m, r in rows], "next_after_seq": rows[-1][1] if rows else after_seq}
    rows2 = (await db.execute(select(Message).where(Message.project_id == pid, Message.seq > after_seq)
                              .order_by(Message.seq).limit(limit))).scalars().all()
    return {"items": [_msg_out(m) for m in rows2]}


def _supplier_digester(request: Request, p: Principal, pid: uuid.UUID) -> Callable[[AuditRecord], str]:
    s = request.app.state.settings

    def digest(rec: AuditRecord) -> str:
        view = {"seq": rec.seq, "ts": rec.ts, "action": rec.action, "rule_id": rec.rule_id, "outcome": rec.outcome,
                "evidence": list(rec.evidence), "config_version": rec.config_version}
        return security.supplier_audit_digest(s, p.org_id, str(pid), view)
    return digest


def _audit_out(e: AuditEvent) -> dict[str, Any]:
    return {"seq": e.seq, "ts": e.ts.isoformat(), "actor_type": e.actor_type, "actor_id": e.actor_id, "action": e.action,
            "rule_id": e.rule_id, "evidence": e.evidence, "outcome": e.outcome, "approver_id": e.approver_id,
            "config_version": e.config_version, "prev_hash": e.prev_hash, "hash": e.hash}


async def _supplier_audit(request: Request, db: AsyncSession, p: Principal, proj: Project, after: int, limit: int) -> dict[str, Any]:
    """Supplier audit view, paged in SQL. Visible = own message traffic + the stage changes up to the supplier's own last
    interaction + a final neutral `closed`. `seq` is the dense rank inside this view (cursor = rank), so neither gaps nor
    page sizes reveal how many events the supplier cannot see."""
    pid = proj.id
    mine = [f"supplier:{s}" for s in await own_supplier_ids(db, p, pid)]
    last = await _last_own_seq(db, pid, mine)
    dg = _supplier_digester(request, p, pid)
    items: list[dict[str, Any]] = []
    n = 0
    if mine and last:
        pred = or_(_own_msg_pred(mine), and_(AuditEvent.action == "stage_changed", AuditEvent.seq <= last))
        where = and_(AuditEvent.project_id == str(pid), pred)
        n = (await db.execute(select(func.count()).select_from(AuditEvent).where(where))).scalar_one()
        rn = func.row_number().over(order_by=AuditEvent.seq).label("rn")
        sub = select(AuditEvent.seq.label("aseq"), rn).where(where).subquery()
        rows = (await db.execute(select(AuditEvent, sub.c.rn).join(sub, sub.c.aseq == AuditEvent.seq)
                                 .where(AuditEvent.project_id == str(pid), sub.c.rn > after).order_by(sub.c.rn).limit(limit))).all()
        for e, rank in rows:
            o = {k: v for k, v in _audit_out(e).items() if k not in ("actor_id", "approver_id", "prev_hash", "hash")}
            o["seq"] = rank
            items.append({**o, "digest": dg(dataclasses.replace(to_record(e), seq=rank))})
    if proj.status.value in _TERMINAL and after + len(items) >= n and len(items) < limit and n + 1 > after:
        won = (await db.execute(select(Contract.supplier_id).where(Contract.project_id == pid))).scalar_one_or_none()
        owns = won is not None and won in {m.split(":", 1)[1] for m in mine}
        outcome = "DEAL" if (owns and proj.outcome and proj.outcome.value == "DEAL") else "CLOSED"
        ts = proj.updated_at
        rec = AuditRecord(project_id=str(pid), seq=n + 1, ts=ts, actor_type="orchestrator", actor_id="", action="closed", outcome=outcome)
        items.append({"seq": n + 1, "ts": ts.isoformat(), "actor_type": "orchestrator", "action": "closed", "rule_id": None,
                      "evidence": [], "outcome": outcome, "config_version": None, "digest": dg(rec)})
    nxt = items[-1]["seq"] if items else after
    return {"items": items, "next_after_seq": nxt}


@router.get("/{pid}/audit")
async def audit_list(pid: uuid.UUID, request: Request, p: Principal = Depends(require_role(R.SUPPLIER, *INSPECTORS)), db: AsyncSession = Depends(get_session),
                     after_seq: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)) -> dict:
    proj = await get_visible_project(db, p, pid)
    if supplier_scoped(p):
        return await _supplier_audit(request, db, p, proj, after_seq, limit)
    rows = (await db.execute(select(AuditEvent).where(AuditEvent.project_id == str(pid), AuditEvent.seq > after_seq)
                             .order_by(AuditEvent.seq).limit(limit))).scalars().all()
    return {"items": [_audit_out(e) for e in rows]}


@router.get("/{pid}/audit/verify")
async def audit_verify(pid: uuid.UUID, request: Request, p: Principal = Depends(require_role(R.EVALUATOR, R.ADMIN, R.SUPER_ADMIN)),
                       db: AsyncSession = Depends(get_session)) -> dict:
    _buyer_side_only(p)
    await get_visible_project(db, p, pid)
    rows = (await db.execute(select(AuditEvent).where(AuditEvent.project_id == str(pid)).order_by(AuditEvent.seq))).scalars().all()
    anchor = await db.get(AuditAnchor, str(pid))  # head recorded with the events: detects tail truncation
    ok, bad = verify_chain([to_record(e) for e in rows], anchor=(anchor.seq, anchor.hash) if anchor else None)
    out: dict = {"valid": ok, "first_bad_seq": bad, "events": len(rows), "head_hash": rows[-1].hash if rows else None}
    sink = request.app.state.service.anchors
    if sink is not None:  # the external anchor file lives outside the database: a consistent rewrite of the chain does not match it
        ext = sink.check(str(pid), {e.seq: e.hash for e in rows})
        out["external_anchor"] = ext
        out["valid"] = ok and ext["matches"]
    return out


@router.get("/{pid}/escalations")
async def escalations(pid: uuid.UUID, request: Request, p: Principal = Depends(require_role(*BUYER_VIEW)), db: AsyncSession = Depends(get_session),
                      status: str | None = Query(None, pattern="^(PENDING|APPROVED|REJECTED|EDITED)$")) -> dict:
    _buyer_side_only(p)
    await get_visible_project(db, p, pid)
    stmt = select(Approval).where(Approval.project_id == pid).order_by(Approval.created_at, Approval.core_id)
    if p.role in BUYER_SIDE:  # same role-scoped inbox as /approvals; EVALUATOR / ADMIN keep the full view
        stmt = stmt.where(Approval.target_role == p.role)
    if status:
        stmt = stmt.where(Approval.status == status)
    return {"items": [approval_out(a, request.app.state.clock.now()) for a in (await db.execute(stmt)).scalars()]}


@router.get("/{pid}/stream")
async def stream(pid: uuid.UUID, request: Request, p: Principal = Depends(require_role(R.SUPPLIER, *INSPECTORS)),
                 db: AsyncSession = Depends(get_session), last_event_id: str | None = Query(None)) -> StreamingResponse:
    await get_visible_project(db, p, pid)
    service, s = svc(request), request.app.state.settings
    supplier_view = (await own_supplier_ids(db, p, pid)) if supplier_scoped(p) else None
    raw = request.headers.get("last-event-id") or last_event_id or "0"
    try:
        after = max(0, int(raw))
    except ValueError:
        after = 0
    key = str(p.id)
    broker = request.app.state.broker
    if broker.streams[key] >= s.max_streams_per_user:  # check + take the slot with NO await in between (atomic on the loop)
        raise ApiError(429, "TOO_MANY_STREAMS", "too many open streams", {"Retry-After": "5"})
    broker.streams[key] += 1
    slot = _Slot(broker, key)
    try:
        await db.rollback()  # release the request-scoped connection before streaming
        return _build_stream(request, p, pid, service, s, supplier_view, after, db, slot)
    except BaseException:
        slot.release()
        raise


class _Slot:
    """One per-user stream slot, released exactly once (response end, client disconnect, or setup failure)."""

    def __init__(self, broker: Any, key: str) -> None:
        self.broker, self.key, self.done = broker, key, False

    def release(self) -> None:
        if not self.done:
            self.done = True
            self.broker.streams[self.key] -= 1


class _CountedStream(StreamingResponse):
    def __init__(self, *a: Any, slot: _Slot, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._slot = slot

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._slot.release()


def _build_stream(request: Request, p: Principal, pid: uuid.UUID, service: Any, s: Any, supplier_view: set[str] | None,
                  after: int, db: AsyncSession, slot: _Slot) -> StreamingResponse:
    key = str(p.id)
    _tok = request.cookies.get(ACCESS_COOKIE, "")
    claims = security.decode_access(s, _tok) or {}

    async def revalidate() -> bool:
        """Same checks as a fresh request: token alive and not revoked, user active with the same role, project still visible
        to them, and (supplier) still linked to the same supplier agents."""
        try:
            if not security.decode_access(s, _tok) or await _revoked(claims.get("jti", "")):
                return False
            async with dbm.get_sessionmaker()() as sess:
                u = (await sess.execute(select(User, Organization.kind).join(Organization, Organization.id == User.org_id)
                                        .where(User.id == p.id))).first()
                if u is None or u[0].role is not p.role or u[0].org_id != p.org_id:
                    return False
                if (await sess.execute(visible_projects(p).where(Project.id == pid))).scalar_one_or_none() is None:
                    return False
                if supplier_scoped(p) and not (supplier_view or set()) <= await own_supplier_ids(sess, p, pid):
                    return False
        except Exception:  # noqa: BLE001 - fail closed
            return False
        return True

    async def _revoked(jti: str) -> bool:
        async with dbm.get_sessionmaker()() as sess:
            return await is_revoked(sess, request.app, jti)

    digest = _supplier_digester(request, p, pid) if supplier_scoped(p) else None
    gen = sse.stream(service=service, broker=request.app.state.broker, pid=pid, user_key=key, after_seq=after,
                     supplier_view=supplier_view, is_disconnected=request.is_disconnected, revalidate=revalidate, digest=digest)
    return _CountedStream(gen, media_type="text/event-stream", slot=slot, headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})
