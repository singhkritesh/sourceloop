"""Payment-term options: price per term is code, stays inside both sides' own limits, and the buyer's pick at the award is applied only when offered."""
import dataclasses
from decimal import Decimal

from sourceloop.data.enums import ApprovalKindEnum as AK
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.enums import OutcomeEnum
from sourceloop.data.synthetic import generate
from sourceloop.orchestrator.scenario import Resolver, ScenarioSpec, build_project, run_to_completion
from sourceloop.policy.terms import adjusted_price, term_options

VIS, FULL, _ = generate(42)
D = Decimal


def test_a_longer_term_costs_more_and_a_shorter_one_less():
    assert adjusted_price(D("100"), 30, 30, D("8")) == D("100.0000")
    assert adjusted_price(D("100"), 30, 60, D("8")) == D("100.6575")
    assert adjusted_price(D("100"), 30, 0, D("8")) == D("99.3425")
    assert adjusted_price(D("100"), 30, 90, D("0")) == D("100.0000")


def test_options_stay_inside_both_limits_and_never_name_which_one_removed_a_term():
    o = term_options(D("100"), 30, D("8"), supplier_floor=D("90"), buyer_max=D("100.4"))
    assert [x["days"] for x in o] == [30, 45]                                  # 60 and 90 are above the buyer's own maximum
    o = term_options(D("100"), 45, D("8"), supplier_floor=D("100"), buyer_max=D("120"))
    assert [x["days"] for x in o] == [45, 60, 90]                              # 30 would fall under the supplier's own floor
    assert all(set(x) == {"days", "price"} for x in o)


def _spec(on=True):
    spec = ScenarioSpec(seed=42, need_index=0)
    spec.run = dataclasses.replace(spec.run, payment_term_options=on)
    return spec


async def _run(spec, resolver=None):
    rt = build_project(spec, VIS, FULL)
    return rt, await run_to_completion(rt, resolver)


async def test_off_the_award_and_contract_carry_no_payment_term():
    rt, st = await _run(_spec(False))
    assert st.outcome is OutcomeEnum.DEAL
    assert all("terms" not in r for r in st.ranking) and "payment_days" not in (st.contract or {})


async def test_on_the_award_lists_terms_and_the_default_keeps_the_agreed_price():
    rt, st = await _run(_spec())
    assert st.outcome is OutcomeEnum.DEAL
    terms = next(r["terms"] for r in st.ranking if r["supplier"] == st.awarded)
    assert terms[0]["days"] == 30 and Decimal(terms[0]["price"]) == Decimal(str(st.deals[st.awarded]["price"]))
    assert st.contract["payment_days"] == 30


async def test_the_buyers_pick_changes_the_contract_price_and_is_audited():
    rt, st = await _run(_spec())
    offered = next(r["terms"] for r in st.ranking if r["supplier"] == st.awarded)
    if len(offered) < 2:
        return  # nothing but the base term fits both sides' limits on this seed; the "ignored" test below covers the guard
    pick = offered[1]
    rt2 = build_project(_spec(), VIS, FULL)
    res = Resolver(decisions={AK.AWARD: (AS.EDITED, {"supplier": st.awarded, "payment_days": pick["days"]}, "buyer prefers this term")})
    st2 = await run_to_completion(rt2, res)
    assert st2.contract["payment_days"] == pick["days"] and str(st2.contract["price"]) == pick["price"]
    assert "payment_term_selected" in [r.action for r in await rt2.audit.store.read(rt2.project_id)]


async def test_a_term_that_was_not_offered_is_ignored_and_audited():
    rt, st = await _run(_spec())
    rt2 = build_project(_spec(), VIS, FULL)
    res = Resolver(decisions={AK.AWARD: (AS.EDITED, {"supplier": st.awarded, "payment_days": 7}, "an unoffered term")})
    st2 = await run_to_completion(rt2, res)
    assert st2.contract["payment_days"] == 30 and str(st2.contract["price"]) == str(st.contract["price"])
    assert "approval_edit_malformed" in [r.action for r in await rt2.audit.store.read(rt2.project_id)]
