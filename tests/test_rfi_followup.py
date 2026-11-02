"""Agents close gaps themselves: a supplier held back only by blank registry fields gets an RFI and a declared pass is marked; a supplier that
ignores the RFQ is chased once. Both are plain bus messages, audited like every other."""
import dataclasses

import pytest
from pydantic import ValidationError

from sourceloop.agents.supplier.agent import SupplierProfile
from sourceloop.bus.messages import FollowUp, Rfi, RfiReply
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.enums import OutcomeEnum
from sourceloop.data.synthetic import generate
from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion

VIS, FULL, _ = generate(42)


async def run(spec):
    rt = build_project(spec, VIS, FULL)
    return rt, await run_to_completion(rt)


async def actions(rt):
    return [r.action for r in await rt.audit.store.read(rt.project_id)]


def test_rfi_payloads_are_closed_and_bounded():
    assert Rfi(fields=("years_in_business",)).fields == ("years_in_business",)
    for bad in ({"fields": ("password",)}, {"fields": ()}, {"fields": ("years_in_business",), "x": 1}):
        with pytest.raises(ValidationError):
            Rfi(**bad)
    assert RfiReply(values={"financial_risk": 0.2}).values == {"financial_risk": 0.2}
    for bad in ({"financial_risk": 7}, {"years_in_business": -1}, {"lead_time_weeks": 999}, {"capacity_units": 0}):
        with pytest.raises(ValidationError):
            RfiReply(values=bad)
    with pytest.raises(ValidationError):
        FollowUp(about="OFFER", attempt=1)


async def test_off_by_default_the_old_flow_is_unchanged():
    rt, st = await run(ScenarioSpec(seed=42, need_index=0))
    assert "rfi_round" not in await actions(rt) and not any(e.type is T.RFI for e in rt.bus.log)


async def test_rfi_fills_blanks_and_the_rules_decide_again():
    spec = ScenarioSpec(seed=42, need_index=0)
    spec.run = dataclasses.replace(spec.run, agent_rfi=True)
    rt, st = await run(spec)
    rfis = [e for e in rt.bus.log if e.type is T.RFI]
    replies = [e for e in rt.bus.log if e.type is T.RFI_REPLY]
    assert rfis and len(replies) == len(rfis) and "rfi_round" in await actions(rt)
    declared = {s for s, q in st.qual.items() if any(str(r).startswith("DECLARED_BY_SUPPLIER") for r in q["reasons"])}
    assert declared and st.outcome is OutcomeEnum.DEAL   # filled, marked as self-declared, and the run no longer waits on a person for them
    assert all(st.qual[s]["status"] == "QUALIFIED" for s in declared)


async def test_a_declaration_never_overrides_the_registry():
    from sourceloop.compliance.rules import qualify  # noqa: F401  (the tool fills only blanks: checked through the tool below)
    spec = ScenarioSpec(seed=42, need_index=0)
    spec.run = dataclasses.replace(spec.run, agent_rfi=True)
    rt, _ = await run(spec)
    rec = VIS.get(next(iter({e.to_agent for e in rt.bus.log if e.type is T.RFI})).split(":", 1)[1])
    res = await rt.buyer.screen(rt.need, rec.id, (("years_in_business", 999.0), ("lead_time_weeks", 1.0)))
    assert res.ok and res.value.status in {"QUALIFIED", "REVIEW", "REJECTED", "BLOCKED"}
    # fields the registry already holds are not replaced: the declared 1-week lead time cannot rescue a supplier whose registry value fails
    if rec.lead_time_weeks is not None and rec.lead_time_weeks > rt.need.max_lead_time_weeks:
        assert "LEAD_TIME" in res.value.reasons


async def test_a_silent_supplier_is_chased_once_and_still_quotes():
    _, base = await run(ScenarioSpec(seed=42, need_index=0))
    sid = base.shortlist[0]
    spec = ScenarioSpec(seed=42, need_index=0)
    spec.profiles = {sid: SupplierProfile(slow_reply=True)}
    rt, st = await run(spec)
    follow = [e for e in rt.bus.log if e.type is T.FOLLOW_UP]
    assert [e.to_agent for e in follow] == [f"supplier:{sid}"] and "follow_up_sent" in await actions(rt)
    assert sid in st.offers   # the chase worked: it answered the RFQ


async def test_a_supplier_that_ignores_twice_is_chased_twice_when_allowed_and_the_attempts_are_audited():
    _, base = await run(ScenarioSpec(seed=42, need_index=0))
    sid = base.shortlist[0]
    spec = ScenarioSpec(seed=42, need_index=0)
    spec.profiles = {sid: SupplierProfile(slow_reply=True, chases_needed=2)}
    spec.run = dataclasses.replace(spec.run, follow_up_attempts=2)
    rt, st = await run(spec)
    assert len([e for e in rt.bus.log if e.type is T.FOLLOW_UP]) == 2 and sid in st.offers
    attempts = [r.evidence[0]["attempt"] for r in await rt.audit.store.read(rt.project_id) if r.action == "follow_up_sent"]
    assert attempts == [1, 2]
    spec.run = dataclasses.replace(spec.run, follow_up_attempts=1)   # the default: one chase is not enough for this supplier
    _, st1 = await run(spec)
    assert sid not in st1.offers
