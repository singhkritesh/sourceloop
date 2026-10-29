"""Scenario builders shared by the main run and the ablations (mirrors sourceloop.demo, no core edits)."""
from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from sourceloop.agents.supplier.agent import SupplierProfile
from sourceloop.data.enums import ApprovalKindEnum as AK
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.synthetic import COUNTRIES
from sourceloop.eval.world import World
from sourceloop.orchestrator.runtime import ProjectState
from sourceloop.orchestrator.scenario import Resolver, ScenarioSpec

KINDS = ("plain", "below_floor", "inflated_claims", "locked_clause", "poisoned_news", "no_deal")
SpecHook = Callable[[ScenarioSpec], None]


def make_scenario(kind: str, w: World, idx: int, base: ProjectState) -> tuple[ScenarioSpec, Resolver]:
    spec, res = ScenarioSpec(seed=w.seed, need_index=idx), Resolver()
    if kind == "no_deal":
        spec.buyer_max_factor, spec.buyer_target_factor = Decimal("0.5"), Decimal("0.4")
    elif kind == "below_floor" and base.shortlist:
        spec.profiles = {base.shortlist[0]: SupplierProfile(attempt_below_floor=True)}
    elif kind == "inflated_claims" and len(base.shortlist) > 1:
        spec.profiles = {base.shortlist[1]: SupplierProfile(inflate_claims=True)}
    elif kind == "locked_clause" and base.awarded:
        spec.profiles = {base.awarded: SupplierProfile(redline_locked_clause="liability")}
        res.decisions[AK.CLAUSE] = (AS.APPROVED, {}, "")  # Legal keeps the standard clause
    elif kind == "poisoned_news":
        spec.poisoned_news = frozenset(COUNTRIES)  # every regional feed carries the injection
    return spec, res
