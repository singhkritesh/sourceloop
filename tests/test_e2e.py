import json
from decimal import Decimal

import pytest

from sourceloop.agents.supplier.agent import SupplierProfile
from sourceloop.audit.canonical import dumps
from sourceloop.data.enums import ApprovalKindEnum as AK
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.enums import OutcomeEnum
from sourceloop.data.enums import ProjectStatusEnum as PS
from sourceloop.data.synthetic import generate
from sourceloop.orchestrator.runtime import RunExecutor
from sourceloop.orchestrator.scenario import Resolver, ScenarioSpec, build_project, run_to_completion

VIS, FULL, KEY = generate(42)


async def baseline():
    rt = build_project(ScenarioSpec(), VIS, FULL)
    st = await run_to_completion(rt)
    return rt, st


async def test_happy_path_deal_with_all_controls():
    rt, st = await baseline()
    assert st.status is PS.DONE and st.outcome is OutcomeEnum.DEAL and st.awarded in st.deals
    assert st.contract and st.contract["signature_hash"] and st.hold == "HELD"
    kinds = {a.kind for a in rt.approvals.all("P1")}
    assert {AK.AWARD, AK.HOLD_RELEASE} <= kinds  # the buyer decides the award and, at the end, bank details + payment release
    assert not kinds & {AK.REQUIREMENT, AK.TECHNICAL_QUAL, AK.SIGNATURE, AK.BANK_DETAILS}  # rule-settled and audited, not asked
    assert await rt.audit.verify("P1") == (True, None)
    touches = sum(1 for a in rt.approvals.all("P1") if a.status is not AS.PENDING)
    assert touches <= 4  # minimal human intervention: a flagged supplier, the award, and the final payment release
    # no sanctioned / forced-labor supplier ever reached RFQ
    assert not (set(st.shortlist) & KEY.sanctioned_ids)
    assert all(not FULL.get(s).forced_labor_flag for s in st.shortlist)


async def test_replay_determinism_identical_audit_chain():
    heads = []
    for _ in range(3):
        rt, _ = await baseline()
        recs = await rt.audit.store.read("P1")
        heads.append(recs[-1].hash)
    assert len(set(heads)) == 1


async def test_no_deal_when_limits_do_not_overlap():
    rt = build_project(ScenarioSpec(buyer_max_factor=Decimal("0.5"), buyer_target_factor=Decimal("0.4")), VIS, FULL)
    st = await run_to_completion(rt)
    assert st.status is PS.STOPPED and st.outcome is OutcomeEnum.NO_DEAL
    audit_text = json.dumps([r.evidence for r in await rt.audit.store.read("P1")], default=str)
    assert "no_overlap" in " ".join(r.action for r in await rt.audit.store.read("P1"))
    assert str(rt.buyer_mandate.max_unit_price) not in audit_text  # NO_DEAL reveals no numbers


async def test_below_floor_offer_blocked_and_supplier_dropped():
    _, base = await baseline()
    victim = base.shortlist[0]
    rt = build_project(ScenarioSpec(profiles={victim: SupplierProfile(attempt_below_floor=True)}), VIS, FULL)
    st = await run_to_completion(rt)
    assert st.dropped[victim] == "PRICE_BELOW_FLOOR" and victim not in st.offers
    assert any(a.kind is AK.MANDATE_BREACH for a in rt.approvals.all("P1"))
    assert st.status is PS.DONE  # the rest of the field still produced a deal
    floor = rt.suppliers[victim].mandate.min_unit_price
    for e in rt.bus.log:
        if e.from_agent == f"supplier:{victim}" and "unit_price" in e.payload:
            assert Decimal(str(e.payload["unit_price"])) >= floor


async def test_inflated_certificate_claims_never_reach_buyer():
    _, base = await baseline()
    liar = base.shortlist[1]
    rt = build_project(ScenarioSpec(profiles={liar: SupplierProfile(inflate_claims=True)}), VIS, FULL)
    st = await run_to_completion(rt)
    assert liar in st.dropped and st.dropped[liar] == "CLAIM_NOT_PROVABLE"
    assert liar not in st.shortlist


async def test_locked_clause_goes_to_the_buyer_and_requires_explicit_override():
    _, base = await baseline()
    win = base.awarded
    spec = ScenarioSpec(profiles={win: SupplierProfile(redline_locked_clause="liability")})
    rt = build_project(spec, VIS, FULL)  # default persona: accept recommendation = keep standard clause
    st = await run_to_completion(rt)
    clause = [a for a in rt.approvals.all("P1") if a.kind is AK.CLAUSE]
    assert clause and clause[0].target_role.value == "BUYER" and st.contract["deviations"] == []

    rt2 = build_project(spec, VIS, FULL)
    res = Resolver(decisions={AK.CLAUSE: (AS.EDITED, {"accept_change": True}, "Buyer accepts capped liability variant")})
    st2 = await run_to_completion(rt2, res)
    assert st2.contract["deviations"] == ["liability"]
    actions = [r.action for r in await rt2.audit.store.read("P1")]
    assert "policy_waived" in actions and "approval_resolved" in actions


async def test_poisoned_news_cannot_move_a_decision():
    _, base = await baseline()
    countries = {FULL.get(s).country for s in base.shortlist}
    rt = build_project(ScenarioSpec(poisoned_news=frozenset(countries)), VIS, FULL)
    st = await run_to_completion(rt)
    cs = {s for s, q in st.qual.items() if q["country"] in countries and q["status"] == "REVIEW" and "NEWS_LOW_CONFIDENCE" in q["reasons"]}
    assert cs, "injected content must lower confidence and route to a human"
    assert not (cs & set(st.shortlist))  # excluded unless a human explicitly overrides
    flagged = [r for r in await rt.audit.store.read("P1") if r.action == "policy_block"]
    assert all("POISON" not in dumps(r.evidence) for r in flagged)


async def test_resolver_cannot_approve_outside_role():
    rt = build_project(ScenarioSpec(), VIS, FULL)
    await RunExecutor(rt).run()
    a = rt.approvals.pending("P1")[0]
    from sourceloop.data.enums import RoleEnum
    from sourceloop.orchestrator.approvals import ApprovalError

    with pytest.raises(ApprovalError) as e:
        await rt.approvals.resolve(a.id, AS.APPROVED, "intruder", RoleEnum.SUPPLIER)
    assert e.value.code == "FORBIDDEN"
    with pytest.raises(ApprovalError) as e2:
        await rt.approvals.resolve(a.id, AS.REJECTED, "buyer1", RoleEnum.BUYER, "short")
    assert e2.value.code == "COMMENT_REQUIRED"


async def test_all_twelve_needs_run_clean_and_fast():
    import time

    t = time.perf_counter()
    outcomes = []
    for i in range(12):
        rt = build_project(ScenarioSpec(need_index=i), VIS, FULL, project_id=f"P{i}")
        st = await run_to_completion(rt)
        outcomes.append(st.outcome)
        assert await rt.audit.verify(f"P{i}") == (True, None)
        assert not (set(st.shortlist) & KEY.sanctioned_ids)
    assert time.perf_counter() - t < 20
    assert OutcomeEnum.DEAL in outcomes
