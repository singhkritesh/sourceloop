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

from fastapi import APIRouter, Depends, Request
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
from sourceloop.api.negotiation_summary import build as build_summary
from sourceloop.api.studio_support import pinned_config
from sourceloop.data.db import get_session
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import (
    Approval,
    AuditEvent,
    Contract,
    Mandate,
    Message,
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


async def _info_requests(db: AsyncSession, pid: uuid.UUID) -> dict[str, dict[str, Any]]:
    """Per supplier: did the buyer's agent ask for missing information, did the supplier answer (declared, not verified), was it chased."""
    out: dict[str, dict[str, Any]] = {}
    rows = (await db.execute(select(Message).where(Message.project_id == pid, Message.type.in_(("RFI", "RFI_REPLY", "FOLLOW_UP"))).order_by(Message.seq))).scalars()
    for m in rows:
        sid = (m.to_agent if m.from_agent == "buyer" else m.from_agent).split(":", 1)[-1]
        d = out.setdefault(sid, {"asked_for": [], "answered": False, "chased": False})
        if m.type.value == "RFI":
            d["asked_for"] = list(m.payload.get("fields", []))
        elif m.type.value == "RFI_REPLY":
            d["answered"] = True
        elif m.payload.get("about") == "RFQ":
            d["chased"] = True
    return out


async def _negotiation(db: AsyncSession, proj: Project, offers: list[Offer], names: dict[str, str]) -> dict[str, Any]:
    """How each negotiation went, step by step, from the buyer's side: every quote and counter, who chose the move (the model or the rule), the judge's
    verdict, the buyer's OWN limits, and the points where a person is asked. The supplier's limits never appear (the buyer cannot know them)."""
    rows = list((await db.execute(select(AuditEvent).where(AuditEvent.project_id == str(proj.id), AuditEvent.action.in_(("agent_decision", "negotiation_judged")))
                                  .order_by(AuditEvent.seq))).scalars()) if True else []
    dec: dict[tuple[str, int, str], dict[str, Any]] = {}
    verdict: dict[tuple[str, int, str], dict[str, Any]] = {}
    for r in rows:
        ev = (r.evidence or [{}])[0] if isinstance(r.evidence, list) else {}
        sid, rnd = ev.get("supplier"), ev.get("round")
        if sid is None or rnd is None:
            continue
        if r.action == "agent_decision":
            who = "buyer" if ev.get("agent") == "buyer" else "supplier"
            dec[(sid, int(rnd), who)] = {"chosen": ev.get("chosen"), "source": ev.get("source"), "rule": ev.get("rule"), "confidence": ev.get("confidence"),
                                         "fallback": ev.get("fallback")}
        else:
            verdict[(sid, int(rnd), str(ev.get("actor")))] = {"level": r.outcome, "notes": [CODES[c][1] for c in ev.get("codes", []) if c in CODES]}
    threads: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in offers:
        who = "supplier" if o.side == "supplier" else "buyer"
        # the move that PRODUCED this step is the one its author chose in the previous round (buyer counters at round r answer the supplier's round-r quote)
        step = {"round": o.round, "by": who, "unit_price": str(o.unit_price), "lead_time_weeks": o.lead_time_weeks,
                "move": dec.get((o.supplier_id, o.round if who == "buyer" else o.round - 1, who)), "judge": verdict.get((o.supplier_id, o.round, who))}
        threads[o.supplier_id].append(step)
    m = (await db.get(Mandate, proj.buyer_mandate_id)) if proj.buyer_mandate_id else None
    pl = m.payload if m else {}
    limits = {"target_unit_price": pl.get("target_unit_price"), "highest_unit_price": pl.get("max_unit_price"), "max_rounds": pl.get("max_rounds"),
              "min_step_pct": pl.get("min_step_pct")} if pl else None
    return {"limits": limits, "threads": [{"supplier_id": s, "name": names.get(s, s), "steps": sorted(v, key=lambda x: (x["round"], x["by"] != "supplier"))}  # the supplier quotes first in each round
                        for s, v in sorted(threads.items())],
            "when_a_person_decides": [
                {"kind": "COMPLIANCE_FLAG", "text": "A supplier cannot be cleared by the rules (sanctions hit, missing data that was not supplied, risk)."},
                {"kind": "AWARD", "text": "Always: choosing the winner commits the company."},
                {"kind": "SUPPLIER_ACCEPT", "text": "The awarded supplier's own person confirms the final terms."},
                {"kind": "CLAUSE", "text": "Only if a supplier tries to change liability, termination or IP."},
                {"kind": "HOLD_RELEASE", "text": "Always, once: confirm bank details and release payment."}],
            "agents_decide": "Counter-offers, accepting a quote and walking away are chosen by the agents inside the limits above; nothing outside them can be sent."}


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
    info = await _info_requests(db, pid)
    cands = []
    for ps, s in sups:
        lo = last.get(s.id)
        cands.append({"supplier_id": s.id, "name": s.name, "country": s.country,
                      "qualification": ps.qual_status.value if ps.qual_status else None, "reasons": list(ps.qual_reasons or []),
                      "rounds": ps.round_count, "latest_offer": _offer_out(lo) if lo else None, "info": info.get(s.id),
                      "awarded": bool(contract and contract.supplier_id == s.id)})
    award = None
    if contract:
        winner = next((c for c in cands if c["supplier_id"] == contract.supplier_id), None)
        award = {"supplier_id": contract.supplier_id, "name": winner["name"] if winner else contract.supplier_id,
                 "unit_price": winner["latest_offer"]["unit_price"] if winner and winner["latest_offer"] else None,
                 "lead_time_weeks": winner["latest_offer"]["lead_time_weeks"] if winner and winner["latest_offer"] else None}
    names = {s.id: s.name for _, s in sups}
    # the supplier whose confirmation is awaited is named, so the buyer sees WHICH supplier was awarded (the evidence holds its id only)
    decisions = [{"kind": a.kind.value, "role": a.target_role.value, "status": a.status.value, "stage": a.stage,
                  "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None, "comment": a.comment or "",
                  **({"supplier_id": sid, "supplier_name": names.get(sid, sid)} if a.kind.value == "SUPPLIER_ACCEPT" and (sid := (a.evidence or {}).get("supplier")) else {})}
                 for a in (await db.execute(select(Approval).where(Approval.project_id == pid).order_by(Approval.created_at, Approval.core_id))).scalars()]
    return {"perspective": "buyer", "candidates": cands, "offers": [_offer_out(o) for o in offers], "award": award,
            "contract": ({"status": contract.status, "signed": contract.status == "SIGNED", "clauses": list(contract.clauses or []),
                          "deviations": list(contract.deviations or [])} if contract else None),
            "decisions": decisions, "milestones": await _milestones(db, pid), "negotiation_review": await _judged_turns(db, pid),
            "negotiation": await _negotiation(db, proj, [o for o in offers], names)}


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


@router.get("/{pid}/negotiation-summary")
async def negotiation_summary(pid: uuid.UUID, request: Request, p: Principal = Depends(require_role(*PROJECT_READERS)), db: AsyncSession = Depends(get_session)) -> dict:
    """What this side reads and confirms before an award, final terms, signature or payment release. Each side gets only its own view."""
    proj = await get_visible_project(db, p, pid)
    sup = supplier_scoped(p)
    out = await build_summary(db, proj, "SUPPLIER" if sup else "BUYER", await own_supplier_ids(db, p, proj.id) if sup else None)
    pin = await pinned_config(db, request.app.state.settings, proj.config_version_id) if proj.config_version_id else None
    return {**out, "ack_required": bool(pin and pin.cfg.workflow.require_summary_ack)}


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
