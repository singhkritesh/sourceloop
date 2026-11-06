"""The negotiation summary a person reads and confirms before a supplier is chosen, terms are accepted, a deal is signed or payment is released.

Built by CODE from the project's own records (offers and the hash-chained audit), never written by a model, so it can be checked against the chain.
Each side sees only what it is entitled to: the buyer sees every thread and its own limits, a supplier sees only its own thread, its own agent's
reasoning and the judge's verdicts on its own conduct. The other side's limits and reasoning never appear. The `hash` covers the content that
matters (not the clock), so a confirmation binds to exactly what was read; if the record changes before the person answers, it must be read again."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.audit.canonical import dumps, sha256_hex
from sourceloop.data.enums import ApprovalKindEnum as K
from sourceloop.data.models import AuditEvent, Contract, Mandate, Offer, Project, ProjectSupplier, Supplier
from sourceloop.policy.judge import CODES
from sourceloop.policy.leverage import describe

ACK_KINDS = frozenset({K.AWARD, K.SIGNATURE, K.SUPPLIER_ACCEPT, K.HOLD_RELEASE})  # the points where a company is committed or money moves
Q1, Q4 = Decimal("0.1"), Decimal("0.0001")
MOVE_TEXT = {
    "COUNTER_STEADY": "the planned step towards the other side",
    "COUNTER_FIRM": "a firm counter that gives up little",
    "COUNTER_GENEROUS": "a counter that moves further towards the other side",
    "ACCEPT": "accepting the other side's latest price",
    "WALK": "ending the negotiation without a deal",
}


def _ev(r: AuditEvent) -> dict[str, Any]:
    return (r.evidence or [{}])[0] if isinstance(r.evidence, list) and r.evidence else {}


def _pct(a: Decimal, b: Decimal) -> str | None:
    return str(((a - b) / a * 100).quantize(Q1)) if a > 0 else None


def _thread(sid: str, name: str, offers: list[Offer], moves: dict[tuple[str, int, str], dict], verdicts: list[AuditEvent], you: bool,
            leverage: list[AuditEvent]) -> dict[str, Any]:
    quotes = [o for o in offers if o.side == "supplier"]
    counters = [o for o in offers if o.side != "supplier"]
    steps: list[dict[str, Any]] = []
    for o in sorted(offers, key=lambda x: (x.round, x.side != "supplier")):
        who = "supplier" if o.side == "supplier" else "buyer"
        # the move that produced a step is its author's choice in the previous round (the buyer's counter at round r answers the supplier's round-r quote)
        mv = moves.get((sid, o.round if who == "buyer" else o.round - 1, who))
        label = ("You" if you else name) if who == "supplier" else ("The buyer" if you else "The buyer's agent")
        s: dict[str, Any] = {"round": o.round, "by": who, "unit_price": str(o.unit_price), "lead_time_weeks": o.lead_time_weeks,
                             "text": f"Round {o.round}: {label} {'quoted' if who == 'supplier' else 'countered at'} {o.unit_price.quantize(Q4)} per unit, {o.lead_time_weeks} weeks."}
        if mv and ((you and who == "supplier") or (not you)):
            how = "the model chose it from the code's list" if mv.get("source") == "model" else "the standard rule chose it"
            s["why"] = f"Move: {MOVE_TEXT.get(str(mv.get('chosen')), str(mv.get('chosen')).lower().replace('_', ' '))} ({how})."
        steps.append(s)
    notes = []
    for r in verdicts:
        ev = _ev(r)
        if ev.get("supplier") != sid or (you and ev.get("actor") != "supplier"):
            continue
        for c in ev.get("codes", []):
            if c in CODES:
                notes.append({"round": ev.get("round"), "by": ("you" if you else "supplier") if ev.get("actor") == "supplier" else "buyer",
                              "level": r.outcome, "text": CODES[c][1]})
    said = []
    for r in leverage:
        ev = _ev(r)
        if ev.get("supplier") == sid:
            text = describe(tuple(ev.get("signals", ())), ev.get("price_gap_band"))
            said.append({"text": text.replace("The buyer said:", "The buyer told you:" if you else f"Your agent told {name}:")})
    out: dict[str, Any] = {"supplier_id": sid, "name": name, "rounds": max((o.round for o in offers), default=0), "steps": steps, "conduct": notes, "leverage": said}
    if quotes:
        first, last = quotes[0].unit_price, quotes[-1].unit_price
        out["opening_quote"], out["latest_quote"] = str(first), str(last)
        out["supplier_moved_pct"] = _pct(first, last)
    if counters:
        out["latest_counter"] = str(counters[-1].unit_price)
    return out


async def build(db: AsyncSession, proj: Project, side: str, mine: set[str] | None = None) -> dict[str, Any]:
    """`side` is BUYER or SUPPLIER; a supplier passes the ids of its own organization's suppliers in this project."""
    you = side == "SUPPLIER"
    pid = proj.id
    names = {s.id: s.name for s in (await db.execute(select(Supplier).join(ProjectSupplier, ProjectSupplier.supplier_id == Supplier.id)
                                                     .where(ProjectSupplier.project_id == pid))).scalars()}
    q = select(Offer).where(Offer.project_id == pid).order_by(Offer.round, Offer.supplier_id, Offer.side)
    if you:
        q = q.where(Offer.supplier_id.in_(mine or {"-"}))
    offers = list((await db.execute(q)).scalars())
    rows = list((await db.execute(select(AuditEvent).where(AuditEvent.project_id == str(pid), AuditEvent.action.in_(("agent_decision", "negotiation_judged", "payment_term_selected", "leverage_stated")))
                                  .order_by(AuditEvent.seq))).scalars())
    moves: dict[tuple[str, int, str], dict] = {}
    for r in rows:
        ev = _ev(r)
        if r.action == "agent_decision" and ev.get("supplier") is not None and ev.get("round") is not None:
            moves[(ev["supplier"], int(ev["round"]), "buyer" if ev.get("agent") == "buyer" else "supplier")] = ev
    judged = [r for r in rows if r.action == "negotiation_judged"]
    stated = [r for r in rows if r.action == "leverage_stated"]
    by_sid: dict[str, list[Offer]] = {}
    for o in offers:
        by_sid.setdefault(o.supplier_id, []).append(o)
    threads = [_thread(s, names.get(s, s), v, moves, judged, you, stated) for s, v in sorted(by_sid.items())]
    contract = (await db.execute(select(Contract).where(Contract.project_id == pid))).scalar_one_or_none()
    deal: dict[str, Any] | None = None
    if contract and (not you or contract.supplier_id in (mine or set())):
        t = next((x for x in threads if x["supplier_id"] == contract.supplier_id), None)
        days = next((_ev(r).get("payment_days") for r in reversed(rows) if r.action == "payment_term_selected" and _ev(r).get("supplier") == contract.supplier_id), None)
        deal = {"supplier": contract.supplier_id if not you else None, "name": names.get(contract.supplier_id) if not you else None,
                "unit_price": (t or {}).get("latest_quote"), "payment_days": days, "clauses": list(contract.clauses or []), "deviations": list(contract.deviations or []),
                "signed": contract.status == "SIGNED"}
    limits = None
    if not you and proj.buyer_mandate_id:
        m = await db.get(Mandate, proj.buyer_mandate_id)
        pl = m.payload if m else {}
        limits = {"target_unit_price": pl.get("target_unit_price"), "highest_unit_price": pl.get("max_unit_price")} if pl else None
    head = (await db.execute(select(AuditEvent.seq, AuditEvent.hash).where(AuditEvent.project_id == str(pid)).order_by(AuditEvent.seq.desc()).limit(1))).first()
    count = (await db.execute(select(func.count()).select_from(AuditEvent).where(AuditEvent.project_id == str(pid)))).scalar_one()
    content = {"side": side, "threads": threads, "deal": deal, "your_limits": limits,
               "how_to_read": ("These are the offers and your agent's reasons, taken from the project's tamper-evident record. The other company's limits are never shown."
                               if you else "Every quote and counter, who chose each move, the judge's conduct notes and your own limits. Suppliers' limits are never visible to anyone but the supplier.")}
    h = sha256_hex(dumps(content))
    return {**content, "hash": h, "record": {"events": count, "head_seq": head[0] if head else None, "head_hash": head[1] if head else None},
            "assumptions": ["Payment-term prices use an assumed financing rate."] if deal and deal.get("payment_days") else []}


async def for_principal(db: AsyncSession, proj_id: uuid.UUID, side: str, mine: set[str] | None) -> dict[str, Any]:
    proj = await db.get(Project, proj_id)
    assert proj is not None
    return await build(db, proj, side, mine)
