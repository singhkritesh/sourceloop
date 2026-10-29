"""Policy engine: pure functions. check(envelope, payload, sender_mandate, ctx) -> PolicyResult.

Only the SENDER's mandate is evaluated here; results carry a rule id and an action, never a
threshold, so nothing can leak across the side boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sourceloop.bus.messages import (
    Accept,
    ContractDraft,
    Counter,
    Envelope,
    Invite,
    Offer,
    Redline,
    Strict,
)
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.policy.mandates import BuyerMandate, Mandate, SupplierMandate


class Action(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"          # message not delivered; project escalated (MANDATE_BREACH)
    ESCALATE = "ESCALATE"    # message not delivered yet; human must decide (CLAUSE, RULE)


@dataclass(frozen=True, slots=True)
class PolicyResult:
    action: Action
    rule_id: str | None = None

    @property
    def passed(self) -> bool:
        return self.action is Action.ALLOW


ALLOW = PolicyResult(Action.ALLOW)


@dataclass
class PolicyContext:
    """Facts the engine needs that are not in the message. Built by the orchestrator, never by agents."""

    referenced_offer: Offer | None = None            # for ACCEPT
    previous_own_price: Decimal | None = None        # sender's last price in this negotiation
    index_price: Decimal | None = None               # synthetic material index for band checks
    price_band_pct: Decimal = Decimal("15")
    require_index: bool = False                      # a band check is configured: a missing index fails CLOSED
    supplier_country: str | None = None              # for INVITE exclusion filter
    clause_library: dict[str, dict[str, Any]] = field(default_factory=dict)  # key -> {negotiable, locked}
    extra_block_rules: list = field(default_factory=list)  # Studio-configured rule callables (non-invariant)


def _block(rule: str) -> PolicyResult:
    return PolicyResult(Action.BLOCK, rule)


def _esc(rule: str) -> PolicyResult:
    return PolicyResult(Action.ESCALATE, rule)


def check(env: Envelope, payload: Strict, mandate: Mandate, ctx: PolicyContext) -> PolicyResult:
    t = env.type
    if isinstance(mandate, BuyerMandate):
        res = _check_buyer(t, payload, mandate, ctx)
    else:
        res = _check_supplier(t, payload, mandate, ctx)
    if not res.passed:
        return res
    for rule in ctx.extra_block_rules:  # configured rules run after invariant rules, can only add stops
        extra = rule(env, payload, mandate, ctx)
        if extra is not None and not extra.passed:
            return extra
    return ALLOW


# ---------- shared price checks ----------

def _price_band(price: Decimal, ctx: PolicyContext) -> PolicyResult | None:
    if ctx.index_price is None:
        # fail closed (rule 5): when the band check is configured, "no index" is never a pass
        return _esc("PRICE_BAND_NO_INDEX") if ctx.require_index else None
    lo = ctx.index_price * (1 - ctx.price_band_pct / 100)
    hi = ctx.index_price * (1 + ctx.price_band_pct / 100)
    if price < lo or price > hi:
        return _esc("PRICE_BAND")
    return None


def _step_ok(new: Decimal, prev: Decimal | None, min_step_pct: Decimal) -> bool:
    if prev is None or prev == 0:
        return True
    return abs(new - prev) / prev * 100 >= min_step_pct


# ---------- buyer ----------

def _check_buyer(t: T, p: Strict, m: BuyerMandate, ctx: PolicyContext) -> PolicyResult:
    if t == T.INVITE:
        assert isinstance(p, Invite)
        if ctx.supplier_country and ctx.supplier_country in m.excluded_countries:
            return _block("EXCLUDED_COUNTRY")
        if p.max_lead_time_weeks > m.max_lead_time_weeks:
            return _block("LEAD_TIME_EXCEEDS_MANDATE")
    elif t == T.RFQ:
        if p.target_lead_time_weeks > m.max_lead_time_weeks:  # type: ignore[attr-defined]
            return _block("LEAD_TIME_EXCEEDS_MANDATE")
        if p.disclosed_target_price is not None and not m.disclose_target:  # type: ignore[attr-defined]
            return _block("TARGET_DISCLOSURE_NOT_ALLOWED")
    elif t == T.COUNTER:
        assert isinstance(p, Counter)
        if p.round > m.max_rounds:
            return _block("ROUND_CAP")
        if p.unit_price > m.max_unit_price:
            return _block("PRICE_ABOVE_MANDATE")
        if p.lead_time_weeks > m.max_lead_time_weeks:
            return _block("LEAD_TIME_EXCEEDS_MANDATE")
        if not _step_ok(p.unit_price, ctx.previous_own_price, m.min_step_pct):
            return _block("STEP_TOO_SMALL")
        if (r := _price_band(p.unit_price, ctx)) is not None:
            return r
    elif t == T.ACCEPT:
        assert isinstance(p, Accept)
        o = ctx.referenced_offer
        if o is None:
            return _block("UNKNOWN_OFFER")
        if o.unit_price > m.max_unit_price or o.lead_time_weeks > m.max_lead_time_weeks:
            return _block("ACCEPT_OUTSIDE_MANDATE")
        if (r := _price_band(o.unit_price, ctx)) is not None:
            return r
    elif t == T.CONTRACT_DRAFT:
        assert isinstance(p, ContractDraft)
        return _check_clauses(p.clause_keys, p.deviations, set(m.locked_clauses), ctx)
    elif t == T.REDLINE:
        assert isinstance(p, Redline)
        return _check_redline(p, set(m.locked_clauses), ctx)
    return ALLOW


# ---------- supplier ----------

def _check_supplier(t: T, p: Strict, m: SupplierMandate, ctx: PolicyContext) -> PolicyResult:
    if t in (T.OFFER, T.COUNTER):
        assert isinstance(p, Offer)
        if p.round > m.max_rounds:
            return _block("ROUND_CAP")
        if p.unit_price < m.min_unit_price:
            return _block("PRICE_BELOW_FLOOR")
        if p.capacity_units > m.max_capacity_units:
            return _block("CAPACITY_EXCEEDED")
        if p.lead_time_weeks < m.lead_time_weeks:
            return _block("LEAD_TIME_UNACHIEVABLE")
        if not _step_ok(p.unit_price, ctx.previous_own_price, m.min_step_pct):
            return _block("STEP_TOO_SMALL")
        if (r := _price_band(p.unit_price, ctx)) is not None:
            return r
    elif t == T.ACCEPT:
        o = ctx.referenced_offer
        if o is None:
            return _block("UNKNOWN_OFFER")
        if o.unit_price < m.min_unit_price:
            return _block("ACCEPT_BELOW_FLOOR")
    elif t == T.REDLINE:
        assert isinstance(p, Redline)
        return _check_redline(p, set(m.unchangeable_clauses), ctx)
    elif t == T.DOC_SUBMISSION:
        provable = set(m.provable_certificates)
        for c in p.certificates:  # type: ignore[attr-defined]
            if c.claimed and c.type not in provable:
                return _block("CLAIM_NOT_PROVABLE")
    return ALLOW


# ---------- clauses ----------

def _check_clauses(keys: tuple[str, ...], deviations: tuple[str, ...], locked: set[str], ctx: PolicyContext) -> PolicyResult:
    for k in keys:
        if k not in ctx.clause_library:
            return _esc("CLAUSE_NOT_IN_LIBRARY")
    required = locked | {k for k, meta in ctx.clause_library.items() if meta.get("locked")}
    if any(k not in keys for k in required):
        return _esc("LOCKED_CLAUSE")  # omitting a locked clause is a deviation: Legal decides
    for d in deviations:
        if d in locked or ctx.clause_library.get(d, {}).get("locked"):
            return _esc("LOCKED_CLAUSE")  # always routes to Legal
        if d not in ctx.clause_library:
            return _esc("CLAUSE_NOT_IN_LIBRARY")
    return ALLOW


def _check_redline(p: Redline, locked: set[str], ctx: PolicyContext) -> PolicyResult:
    meta = ctx.clause_library.get(p.clause_key)
    if meta is None:
        return _esc("CLAUSE_NOT_IN_LIBRARY")
    if p.action != "accept" and (p.clause_key in locked or meta.get("locked") or not meta.get("negotiable", False)):
        return _esc("LOCKED_CLAUSE" if (p.clause_key in locked or meta.get("locked")) else "CLAUSE_NOT_NEGOTIABLE")
    if p.alternative_key is not None and p.alternative_key not in ctx.clause_library:
        return _esc("CLAUSE_NOT_IN_LIBRARY")
    return ALLOW
