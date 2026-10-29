"""SSE over fetch-friendly StreamingResponse. The stream is a view of the durable audit log: id = audit seq, resume with
Last-Event-ID, 15 s heartbeat comments. Events are derived from audit actions, so replay after reconnect is exact."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from sourceloop.api.broker import Broker
from sourceloop.audit.chain import AuditRecord
from sourceloop.bus.bus import ai_generated
from sourceloop.data import db as dbm
from sourceloop.data.enums import ProjectStatusEnum as PS
from sourceloop.data.models import Approval, Contract, Project, SubagentCall

HEARTBEAT_S = 15.0
POLL_S = 0.5
REVALIDATE_S = 15.0  # the principal is re-checked at least this often while a stream is open
TERMINAL_ACTIONS = {"run_finished"}


def _view_action(rec: AuditRecord, frames: list[tuple[str, dict[str, Any]]]) -> str:
    return "closed" if frames and frames[0][0] == "run_finished" else rec.action


def frame(event: str, seq: int, data: dict[str, Any]) -> str:
    return f"id: {seq}\nevent: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


@dataclass
class Ctx:
    pid: str
    supplier_view: set[str] | None  # None => full buyer-side view; set => supplier side (own supplier ids)
    stage: str = "NEED"
    approvals: dict[str, tuple[str, str]] = field(default_factory=dict)  # core id -> (uuid, target role)
    digest: Callable[[AuditRecord], str] | None = None  # supplier side: per-supplier digest instead of the chain hash
    # supplier side only: the view is renumbered densely (hidden events leave no gaps) and stage changes are held back
    # until the supplier's next own message, so nothing after its last interaction is revealed
    rank: int = 0
    pending_stage: list[AuditRecord] = field(default_factory=list)
    closed: bool = False

    def allowed_message(self, ev: dict[str, Any]) -> bool:
        if self.supplier_view is None:
            return True
        mine = {f"supplier:{s}" for s in self.supplier_view}
        return ev.get("from") in mine or ev.get("to") in mine


async def _refresh_approvals(ctx: Ctx) -> None:
    async with dbm.get_sessionmaker()() as s:
        rows = await s.execute(select(Approval.core_id, Approval.id, Approval.target_role)
                               .where(Approval.project_id == uuid.UUID(ctx.pid)))
        ctx.approvals = {c: (str(i), r.value) for c, i, r in rows}


async def _awarded_to(ctx: Ctx) -> bool:
    async with dbm.get_sessionmaker()() as s:
        sid = (await s.execute(select(Contract.supplier_id).where(Contract.project_id == uuid.UUID(ctx.pid)))).scalar_one_or_none()
    return sid is not None and sid in (ctx.supplier_view or set())


async def translate_supplier(rec: AuditRecord, ctx: Ctx) -> list[tuple[AuditRecord, list[tuple[str, dict[str, Any]]]]]:
    """Supplier view: one group per VISIBLE audit record (each consumes one dense view rank). Own message traffic, the stage
    changes that preceded it, and a final neutral `closed` (outcome DEAL only for the awarded supplier)."""
    ev0: dict[str, Any] = rec.evidence[0] if rec.evidence and isinstance(rec.evidence[0], dict) else {}
    groups: list[tuple[AuditRecord, list[tuple[str, dict[str, Any]]]]] = []
    if ctx.closed:
        return groups
    if rec.action == "stage_changed":
        ctx.pending_stage.append(rec)
    elif rec.action == "message_delivered" and ctx.allowed_message(ev0):
        for st in ctx.pending_stage:
            groups.append((st, [("stage_changed", {"project_id": ctx.pid, "from": ctx.stage, "to": st.outcome})]))
            ctx.stage = st.outcome or ctx.stage
        ctx.pending_stage = []
        groups.append((rec, [("message", {"message": {
            "id": ev0.get("id"), "type": ev0.get("type"), "from_agent": ev0.get("from"), "to_agent": ev0.get("to"),
            "stage": ctx.stage, "ai_generated": ai_generated(str(ev0.get("from", ""))),
            "summary": f"{ev0.get('type')}: {ev0.get('from')} -> {ev0.get('to')}"}})]))
    elif rec.action in ("run_finished", "run_stopped"):
        ctx.closed, ctx.pending_stage = True, []
        outcome = rec.outcome if (rec.action == "run_finished" and rec.outcome == "DEAL" and await _awarded_to(ctx)) else "CLOSED"
        groups.append((rec, [("run_finished", {"outcome": outcome})]))
    return groups


async def closed_group(ctx: Ctx, rec: AuditRecord) -> list[tuple[AuditRecord, list[tuple[str, dict[str, Any]]]]]:
    """Terminal project whose stream saw no run_finished (e.g. stopped): the neutral closing frame."""
    if ctx.closed:
        return []
    ctx.closed = True
    return [(rec, [("run_finished", {"outcome": "CLOSED"})])]


async def translate(rec: AuditRecord, ctx: Ctx) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    ev0: dict[str, Any] = rec.evidence[0] if rec.evidence and isinstance(rec.evidence[0], dict) else {}
    side = ctx.supplier_view is not None
    a = rec.action
    if a == "stage_changed":
        out.append(("stage_changed", {"seq": rec.seq, "project_id": ctx.pid, "from": ctx.stage, "to": rec.outcome}))
        ctx.stage = rec.outcome or ctx.stage
    elif a == "message_delivered":
        if ctx.allowed_message(ev0):
            out.append(("message", {"seq": rec.seq, "message": {
                "id": ev0.get("id"), "type": ev0.get("type"), "from_agent": ev0.get("from"), "to_agent": ev0.get("to"),
                "stage": ctx.stage, "ai_generated": ai_generated(str(ev0.get("from", ""))), "summary": f"{ev0.get('type')}: {ev0.get('from')} -> {ev0.get('to')}"}}))
        else:
            return []
    elif a in ("policy_block", "message_rejected") and not side:
        out.append(("policy_block", {"seq": rec.seq, "side": str(ev0.get("from", "")), "rule_id": rec.rule_id,
                                     "message_type": ev0.get("type")}))
    elif a == "escalation_created" and not side:
        if ev0.get("approval") not in ctx.approvals:
            await _refresh_approvals(ctx)
        aid = ctx.approvals.get(ev0.get("approval", ""), ("", ""))[0]
        out.append(("escalation_created", {"seq": rec.seq, "approval_id": aid, "kind": rec.rule_id,
                                           "target_role": ev0.get("role"), "reasons": ev0.get("reasons", [])}))
    elif a == "approval_resolved" and not side:
        if ev0.get("approval") not in ctx.approvals:
            await _refresh_approvals(ctx)
        aid, role = ctx.approvals.get(ev0.get("approval", ""), ("", ""))
        out.append(("approval_resolved", {"seq": rec.seq, "approval_id": aid, "status": rec.outcome, "resolver_role": role}))
    elif a == "run_finished":
        out.append(("run_finished", {"seq": rec.seq, "outcome": rec.outcome}))
    elif a == "run_error" and not side:
        out.append(("error", {"code": rec.rule_id or "RUN_ERROR", "message": "run failed; it can be resumed"}))
    elif side and a not in ("stage_changed", "run_finished"):
        return []
    out.append(("audit_event", {"seq": rec.seq, "action": a, "rule_id": rec.rule_id,
                                "hash": ctx.digest(rec) if ctx.digest else rec.hash}))
    if side:
        out = [o for o in out if o[0] in ("stage_changed", "message", "run_finished", "audit_event")]
    return out


async def stream(*, service: Any, broker: Broker, pid: uuid.UUID, user_key: str, after_seq: int,
                 supplier_view: set[str] | None, is_disconnected: Any,
                 revalidate: Callable[[], Awaitable[bool]] | None = None,
                 digest: Callable[[AuditRecord], str] | None = None) -> AsyncIterator[str]:
    ctx = Ctx(str(pid), supplier_view, digest=digest)
    checked = time.monotonic()
    waiter = broker.subscribe(ctx.pid)
    side = supplier_view is not None
    resume_rank = after_seq if side else 0  # supplier: Last-Event-ID is a VIEW rank; replay from 0 and skip what was seen
    after_seq = 0 if side else after_seq
    last = after_seq
    call_floor = after_seq  # subagent calls attached to audit seq >= call_floor are not yet emitted
    idle = 0.0
    try:
        if after_seq:  # restore stage context for the 'from' field
            for r in await service.store.read(ctx.pid, 0, limit=after_seq):
                if r.action == "stage_changed":
                    ctx.stage = r.outcome or ctx.stage
        yield ": connected\n\n"
        while True:
            if revalidate is not None and time.monotonic() - checked >= REVALIDATE_S:
                checked = time.monotonic()
                if not await revalidate():  # logged out / revoked / expired / user disabled / role or supplier link changed
                    yield frame("error", last, {"code": "UNAUTHENTICATED", "message": "session is no longer valid"})
                    return
            h = service.handles.get(ctx.pid)
            limit = h.synced_seq if h is not None else None
            recs = [r for r in await service.store.read(ctx.pid, last, limit=200) if limit is None or r.seq <= limit]
            if recs:
                idle = 0.0
                hi = recs[-1].seq
                finished = any(r.action in TERMINAL_ACTIONS for r in recs)
                calls: list[SubagentCall] = []
                if supplier_view is None:
                    upper = hi + 1 if finished else hi  # calls at seq==hi are only complete once hi+1 exists (or the run ended)
                    async with dbm.get_sessionmaker()() as s:
                        calls = list((await s.execute(
                            select(SubagentCall).where(SubagentCall.project_id == pid, SubagentCall.audit_seq >= call_floor,
                                                       SubagentCall.audit_seq < upper)
                            .order_by(SubagentCall.audit_seq, SubagentCall.ord))).scalars())
                    call_floor = upper
                ci = 0
                if side:
                    for r in recs:
                        for g_rec, frames in await translate_supplier(r, ctx):
                            ctx.rank += 1
                            if ctx.rank <= resume_rank:
                                continue
                            view_rec = dataclasses.replace(g_rec, seq=ctx.rank)
                            for ev, data in frames:
                                yield frame(ev, ctx.rank, {"seq": ctx.rank, **data})
                            if ctx.digest is not None:
                                yield frame("audit_event", ctx.rank, {"seq": ctx.rank, "action": _view_action(g_rec, frames),
                                                                      "rule_id": None if frames[0][0] == "run_finished" else g_rec.rule_id,
                                                                      "hash": ctx.digest(view_rec)})
                    recs_for_loop: list[AuditRecord] = []
                else:
                    recs_for_loop = recs
                for r in recs_for_loop:
                    while ci < len(calls) and calls[ci].audit_seq < r.seq:
                        c = calls[ci]
                        yield frame("subagent_call", c.audit_seq, {"seq": c.audit_seq, "agent": c.agent, "tool": c.tool,
                                                                   "input_summary": c.input_summary})
                        yield frame("subagent_result", c.audit_seq, {
                            "seq": c.audit_seq, "agent": c.agent, "tool": c.tool, "confidence": float(c.confidence),
                            "sources_count": c.output.get("sources", 0), "latency_ms": c.latency_ms})
                        ci += 1
                    for ev, data in await translate(r, ctx):
                        yield frame(ev, r.seq, data)
                for c in calls[ci:]:
                    yield frame("subagent_call", c.audit_seq, {"seq": c.audit_seq, "agent": c.agent, "tool": c.tool, "input_summary": ""})
                    yield frame("subagent_result", c.audit_seq, {"seq": c.audit_seq, "agent": c.agent, "tool": c.tool,
                                                                 "confidence": float(c.confidence), "sources_count": c.output.get("sources", 0),
                                                                 "latency_ms": c.latency_ms})
                last = hi
                if finished:
                    return
                continue
            async with dbm.get_sessionmaker()() as s:
                status = (await s.execute(select(Project.status).where(Project.id == pid))).scalar_one_or_none()
            if status in (PS.DONE, PS.STOPPED) and not recs and (h is None or h.synced_seq >= h.last_seq):
                if side:  # neutral closing frame when the run ended without a run_finished event (stopped)
                    async with dbm.get_sessionmaker()() as s2:
                        upd = (await s2.execute(select(Project.updated_at).where(Project.id == pid))).scalar_one_or_none()
                    for _g, frames in await closed_group(ctx, AuditRecord(
                            project_id=ctx.pid, seq=last, ts=upd, actor_type="", actor_id="", action="closed")):  # type: ignore[arg-type]
                        ctx.rank += 1
                        if ctx.rank > resume_rank:
                            for ev, data in frames:
                                yield frame(ev, ctx.rank, {"seq": ctx.rank, **data})
                return  # terminal and fully replayed
            waiter.clear()
            try:
                await asyncio.wait_for(waiter.wait(), POLL_S)
            except TimeoutError:
                idle += POLL_S
            if idle >= HEARTBEAT_S:
                idle = 0.0
                yield ": heartbeat\n\n"
            if await is_disconnected():
                return
    finally:
        broker.unsubscribe(ctx.pid, waiter)  # the per-user slot is owned (and released) by the route
