"""Perspective endpoints: every login sees the project from ITS side only.

* BUSINESS users (buyer-side roles) get `/overview`: the need, the candidate suppliers and how they qualified, the offers, the award, the
  contract and the human decisions. No agents, no orchestrator, no agent-to-agent messages, no audit chain, no model or token internals.
* SUPPLIER users (and the admin of a supplier organization) get `/overview` too, but built from their own supplier's thread only.
* INSPECTORS get `/activity`: how the agents behaved (calls, confidence, latency, models). SUPER_ADMIN and EVALUATOR see every agent; the
  admin of an organization sees only its own side's agents.
Scoping is done in SQL (cross-tenant => 404); role checks sit at the route."""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.api.deps import (
    BUYER_SIDE,
    INSPECTORS,
    PROJECT_READERS,
    Principal,
    get_visible_project,
    own_supplier_ids,
    require_role,
    supplier_scoped,
)
from sourceloop.api.errors import ApiError
from sourceloop.data.db import get_session
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import (
    Approval,
    AuditEvent,
    Contract,
    Offer,
    Project,
    ProjectSupplier,
    SubagentCall,
    Supplier,
)
from sourceloop.policy.judge import CODES

router = APIRouter(prefix="/projects", tags=["perspective"])


def _offer_out(o: Offer, mine: bool | None = None) -> dict[str, Any]:
    d = {"supplier_id": o.supplier_id, "round": o.round, "side": o.side, "unit_price": str(o.unit_price), "lead_time_weeks": o.lead_time_weeks,
         "capacity_units": (o.terms or {}).get("capacity_units")}
    return d


async def _milestones(db: AsyncSession, pid: uuid.UUID) -> list[dict[str, Any]]:
    """Business milestones only: which stage the project reached and when. No event payloads, hashes or actors."""
    rows = (await db.execute(select(AuditEvent.outcome, AuditEvent.ts).where(AuditEvent.project_id == str(pid), AuditEvent.action == "stage_changed")
                             .order_by(AuditEvent.seq))).all()
    out: list[dict[str, Any]] = []
    for stage, ts in rows:
        if stage and (not out or out[-1]["stage"] != stage):
            out.append({"stage": stage, "at": ts.isoformat()})
    return out


async def _judged_turns(db: AsyncSession, pid: uuid.UUID, only_supplier_side_of: set[str] | None = None) -> list[dict[str, Any]]:
    """The negotiation judge's verdicts, in plain language. A supplier gets only the verdicts on its OWN conduct; the buyer gets both sides'.
    Codes and sentences only: the audit evidence carries no prices."""
    rows = (await db.execute(select(AuditEvent).where(AuditEvent.project_id == str(pid), AuditEvent.action == "negotiation_judged")
                             .order_by(AuditEvent.seq))).scalars()
    out: list[dict[str, Any]] = []
    for r in rows:
        ev = (r.evidence or [{}])[0] if isinstance(r.evidence, list) else {}
        sid, actor = ev.get("supplier"), ev.get("actor")
        if only_supplier_side_of is not None and not (actor == "supplier" and sid in only_supplier_side_of):
            continue
        out.append({"supplier_id": sid, "round": ev.get("round"), "actor": ("you" if actor == "supplier" else "buyer") if only_supplier_side_of is not None
                    else actor, "action": ev.get("action"), "level": r.outcome, "notes": [CODES[c][1] for c in ev.get("codes", []) if c in CODES]})
    return out


async def _buyer_overview(db: AsyncSession, proj: Project) -> dict[str, Any]:
    pid = proj.id
    sups = (await db.execute(select(ProjectSupplier, Supplier).join(Supplier, Supplier.id == ProjectSupplier.supplier_id)
                             .where(ProjectSupplier.project_id == pid).order_by(Supplier.name_norm, Supplier.id))).all()
    offers = list((await db.execute(select(Offer).where(Offer.project_id == pid).order_by(Offer.round, Offer.supplier_id, Offer.side))).scalars())
    contract = (await db.execute(select(Contract).where(Contract.project_id == pid))).scalar_one_or_none()
    last: dict[str, Offer] = {}
    for o in offers:
        if o.side == "supplier":
            last[o.supplier_id] = o
    cands = []
    for ps, s in sups:
        lo = last.get(s.id)
        cands.append({"supplier_id": s.id, "name": s.name, "country": s.country,
                      "qualification": ps.qual_status.value if ps.qual_status else None, "reasons": list(ps.qual_reasons or []),
                      "rounds": ps.round_count, "latest_offer": _offer_out(lo) if lo else None,
                      "awarded": bool(contract and contract.supplier_id == s.id)})
    award = None
    if contract:
        winner = next((c for c in cands if c["supplier_id"] == contract.supplier_id), None)
        award = {"supplier_id": contract.supplier_id, "name": winner["name"] if winner else contract.supplier_id,
                 "unit_price": winner["latest_offer"]["unit_price"] if winner and winner["latest_offer"] else None,
                 "lead_time_weeks": winner["latest_offer"]["lead_time_weeks"] if winner and winner["latest_offer"] else None}
    decisions = [{"kind": a.kind.value, "role": a.target_role.value, "status": a.status.value, "stage": a.stage,
                  "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None, "comment": a.comment or ""}
                 for a in (await db.execute(select(Approval).where(Approval.project_id == pid).order_by(Approval.created_at, Approval.core_id))).scalars()]
    return {"perspective": "buyer", "candidates": cands, "offers": [_offer_out(o) for o in offers], "award": award,
            "contract": ({"status": contract.status, "signed": contract.status == "SIGNED", "clauses": list(contract.clauses or []),
                          "deviations": list(contract.deviations or [])} if contract else None),
            "decisions": decisions, "milestones": await _milestones(db, pid), "negotiation_review": await _judged_turns(db, pid)}


async def _supplier_overview(db: AsyncSession, proj: Project, p: Principal) -> dict[str, Any]:
    """Only this supplier organization's own thread. Nothing about other suppliers, the buyer's qualification, limits or internal decisions."""
    mine = await own_supplier_ids(db, p, proj.id)
    offers = list((await db.execute(select(Offer).where(Offer.project_id == proj.id, Offer.supplier_id.in_(mine)).order_by(Offer.round, Offer.side))).scalars()) if mine else []
    contract = (await db.execute(select(Contract).where(Contract.project_id == proj.id))).scalar_one_or_none()
    won = bool(contract and contract.supplier_id in mine and proj.outcome and proj.outcome.value == "DEAL")
    closed = proj.status.value in ("DONE", "STOPPED")
    out: dict[str, Any] = {"perspective": "supplier", "offers": [{**_offer_out(o), "side": "you" if o.side == "supplier" else "buyer"} for o in offers],
                           "outcome": ("DEAL" if won else "CLOSED") if closed else None}
    out["negotiation_review"] = await _judged_turns(db, proj.id, set(mine))
    if won and contract:  # the KEY itself is absent for everyone else: a losing supplier's payload never mentions a contract
        out["contract"] = {"status": contract.status, "signed": contract.status == "SIGNED", "clauses": list(contract.clauses or [])}
    return out


@router.get("/{pid}/overview")
async def overview(pid: uuid.UUID, p: Principal = Depends(require_role(*PROJECT_READERS)), db: AsyncSession = Depends(get_session)) -> dict:
    proj = await get_visible_project(db, p, pid)
    if supplier_scoped(p):
        return await _supplier_overview(db, proj, p)
    return await _buyer_overview(db, proj)


@router.get("/{pid}/activity")
async def activity(pid: uuid.UUID, p: Principal = Depends(require_role(*INSPECTORS)), db: AsyncSession = Depends(get_session)) -> dict:
    """How the agents behaved on this project: one row per tool call, plus a per-tool summary."""
    await get_visible_project(db, p, pid)
    stmt = select(SubagentCall).where(SubagentCall.project_id == pid).order_by(SubagentCall.ord)
    rows = list((await db.execute(stmt)).scalars())
    if p.role is R.ADMIN:  # an organization admin sees ITS OWN side's agents only
        if supplier_scoped(p):
            mine = await own_supplier_ids(db, p, pid)
            rows = [r for r in rows if (r.output or {}).get("supplier_id") in mine]
        else:
            rows = [r for r in rows if r.agent == "buyer"]
    items = [{"ord": r.ord, "agent": r.agent, "supplier_id": (r.output or {}).get("supplier_id"), "tool": r.tool, "ok": (r.output or {}).get("ok"),
              "error": (r.output or {}).get("error"), "sources": (r.output or {}).get("sources", 0), "confidence": float(r.confidence),
              "latency_ms": r.latency_ms, "model_id": r.model_id, "tokens": r.tokens, "audit_seq": r.audit_seq} for r in rows]
    agg: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for i in items:
        agg[(i["agent"], i["tool"])].append(i)
    summary = [{"agent": a, "tool": t, "calls": len(v), "failed": sum(1 for x in v if x["ok"] is False),
                "avg_confidence": round(sum(x["confidence"] for x in v) / len(v), 3), "avg_latency_ms": round(sum(x["latency_ms"] for x in v) / len(v)),
                "tokens": sum(x["tokens"] for x in v)} for (a, t), v in sorted(agg.items())]
    return {"items": items, "summary": summary}


@router.get("/{pid}/agent-performance")
async def agent_performance(pid: uuid.UUID, p: Principal = Depends(require_role(*INSPECTORS)), db: AsyncSession = Depends(get_session)) -> dict:
    """How well the agents did their jobs on this project, measured from the audit record: per agent, how many choices the model made versus
    the rule-based fallback, how often it agreed with the rule, how sure it was, how long it took, and how the conduct judge rated each side's
    turns. Codes and probabilities only, never a price. An organization admin sees its own side's agents only."""
    await get_visible_project(db, p, pid)
    rows = list((await db.execute(select(AuditEvent).where(AuditEvent.project_id == str(pid), AuditEvent.action.in_(("agent_decision", "negotiation_judged")))
                                  .order_by(AuditEvent.seq))).scalars())
    mine: set[str] | None = None
    if p.role is R.ADMIN and supplier_scoped(p):
        mine = await own_supplier_ids(db, p, pid)
    buyer_only = p.role is R.ADMIN and not supplier_scoped(p)

    def visible(agent: str) -> bool:
        if mine is not None:
            return agent.startswith("supplier:") and agent.split(":", 1)[1] in mine
        return not buyer_only or not agent.startswith("supplier:")

    per: dict[str, dict[str, Any]] = {}

    def slot(agent: str) -> dict[str, Any]:
        return per.setdefault(agent, {"agent": agent, "decisions": 0, "model_chosen": 0, "rule_used": 0, "fallbacks": defaultdict(int), "agreed": 0,
                                      "conf": [], "p": [], "ms": [], "tokens": 0, "judge": defaultdict(int)})
    decisions: list[dict[str, Any]] = []
    for r in rows:
        ev = (r.evidence or [{}])[0] if isinstance(r.evidence, list) else {}
        if r.action == "agent_decision":
            agent = str(ev.get("agent", ""))
            if not visible(agent):
                continue
            a = slot(agent)
            a["decisions"] += 1
            a["model_chosen" if ev.get("source") == "model" else "rule_used"] += 1
            if ev.get("fallback"):
                a["fallbacks"][str(ev["fallback"]).split(":")[0]] += 1
            a["agreed"] += bool(ev.get("agrees_with_rule"))
            a["conf"].append(float(ev.get("confidence", 0))), a["p"].append(float(ev.get("p_chosen", 0))), a["ms"].append(int(ev.get("ms", 0)))
            a["tokens"] += int(ev.get("tokens", 0))
            decisions.append({"seq": r.seq, "agent": agent, "round": ev.get("round"), "rule": ev.get("rule"), "chosen": ev.get("chosen"), "source": ev.get("source"),
                              "agrees_with_rule": bool(ev.get("agrees_with_rule")), "p_chosen": ev.get("p_chosen"), "confidence": ev.get("confidence"),
                              "fallback": ev.get("fallback"), "ms": ev.get("ms"), "model": ev.get("model")})
        else:
            actor, sid = ev.get("actor"), ev.get("supplier")
            agent = "buyer" if actor == "buyer" else f"supplier:{sid}"
            if visible(agent):
                slot(agent)["judge"][r.outcome] += 1

    def avg(v: list[float]) -> float | None:
        return round(sum(v) / len(v), 3) if v else None
    agents = []
    for a in per.values():
        n = a["decisions"]
        agents.append({"agent": a["agent"], "decisions": n, "model_chosen": a["model_chosen"], "rule_used": a["rule_used"], "fallbacks": dict(a["fallbacks"]),
                       "agreement_with_rule_pct": round(100 * a["agreed"] / n) if n else None, "avg_confidence": avg(a["conf"]), "avg_p_chosen": avg(a["p"]),
                       "avg_ms": round(sum(a["ms"]) / len(a["ms"])) if a["ms"] else None, "tokens": a["tokens"], "judge": dict(a["judge"])})
    return {"live_model_decisions": any(d["source"] == "model" for d in decisions), "agents": sorted(agents, key=lambda x: x["agent"]), "decisions": decisions[:300]}


__all__ = ["router", "BUYER_SIDE", "ApiError"]
