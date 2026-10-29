"""Compile Studio RuleDefs into policy extra-rule callables. Pure; rules can only add a stop (BLOCK/ESCALATE)."""
from __future__ import annotations

import operator
from collections.abc import Callable, Iterable
from decimal import Decimal

from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.policy.engine import Action, PolicyResult
from sourceloop.studio.schema import RuleDef

_OPS: dict[str, Callable[[Decimal, Decimal], bool]] = {
    ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


def _signal(rule: RuleDef, payload, ctx) -> Decimal | None:
    if rule.signal == "offer_lead_time_weeks":
        return Decimal(payload.lead_time_weeks)
    if rule.signal == "offer_round":
        return Decimal(payload.round)
    if rule.signal == "offer_unit_price_vs_index_pct":
        if not ctx.index_price:
            return None
        return (payload.unit_price - ctx.index_price) / ctx.index_price * 100
    return None


def compile_rule(rule: RuleDef) -> Callable:
    action = Action.BLOCK if rule.action == "BLOCK" else Action.ESCALATE
    cmp = _OPS[rule.op]

    def _rule(env, payload, mandate, ctx) -> PolicyResult | None:  # noqa: ARG001
        if env.type not in (T.OFFER, T.COUNTER):
            return None
        sig = _signal(rule, payload, ctx)
        if sig is None:  # missing data never passes silently, but a rule cannot invent a stop either
            return None
        return PolicyResult(action, rule.id) if cmp(sig, rule.value) else None

    _rule.rule_id = rule.id  # type: ignore[attr-defined]
    return _rule


def compile_rules(rules: Iterable[RuleDef]) -> list[Callable]:
    return [compile_rule(r) for r in rules if r.enabled]
