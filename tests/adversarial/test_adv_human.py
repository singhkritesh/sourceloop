"""ADV-18: human checkpoints cannot be skipped, forged or overridden by agents."""
import pytest

from sourceloop.audit.chain import AuditLog, MemoryAuditStore
from sourceloop.bus.bus import AgentChannel
from sourceloop.clock import FixedClock
from sourceloop.data.enums import ApprovalKindEnum as AK
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.enums import OutcomeEnum
from sourceloop.data.enums import ProjectStatusEnum as PS
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.enums import StageEnum as S
from sourceloop.data.synthetic import generate
from sourceloop.orchestrator.approvals import KIND_ROLE, ApprovalError, ApprovalService
from sourceloop.orchestrator.runtime import RunConfig, RunExecutor
from sourceloop.orchestrator.scenario import Resolver, ScenarioSpec, build_project, run_to_completion
from sourceloop.orchestrator.transitions import (
    STATUS_TRANSITIONS,
    IllegalTransition,
    check_status,
    enabled_stages,
    next_stage,
)

VIS, FULL, _KEY = generate(42)
LONG = "A sufficiently long human justification."


def svc() -> ApprovalService:
    return ApprovalService(AuditLog(MemoryAuditStore(), FixedClock()))


# ---- role binding
def test_adv18_required_roles_for_the_critical_checkpoints():
    assert KIND_ROLE[AK.AWARD] is R.BUYER and KIND_ROLE[AK.SIGNATURE] is R.LEGAL and KIND_ROLE[AK.CLAUSE] is R.LEGAL
    assert KIND_ROLE[AK.BANK_DETAILS] is R.FINANCE and KIND_ROLE[AK.HOLD_RELEASE] is R.FINANCE
    assert KIND_ROLE[AK.COMPLIANCE_FLAG] is R.COMPLIANCE and KIND_ROLE[AK.TECHNICAL_QUAL] is R.QUALITY
    assert set(KIND_ROLE) == set(AK)  # no kind without an owner


@pytest.mark.parametrize("kind", list(AK))
@pytest.mark.parametrize("role", list(R))
async def test_adv18_only_the_target_role_can_resolve(kind, role):
    s = svc()
    a = await s.ensure("p", kind, "k", ("R",))
    if role is KIND_ROLE[kind]:
        res = await s.resolve(a.id, AS.APPROVED, "u1", role)
        assert res.status is AS.APPROVED
        return
    with pytest.raises(ApprovalError) as e:
        await s.resolve(a.id, AS.APPROVED, "intruder", role)
    assert e.value.code == "FORBIDDEN"
    assert a.status is AS.PENDING and a.resolver_id is None and a.version == 1
    assert not [r for r in await s.audit.store.read("p") if r.action == "approval_resolved"]


@pytest.mark.parametrize("status", [AS.REJECTED, AS.EDITED])
@pytest.mark.parametrize("comment", ["", "short", "         ", "\n\t \n\t \n\t \n", "123456789"])
async def test_adv18_reject_and_edit_require_a_real_comment(status, comment):
    s = svc()
    a = await s.ensure("p", AK.AWARD, "k", ("R",))
    with pytest.raises(ApprovalError) as e:
        await s.resolve(a.id, status, "u", R.BUYER, comment)
    assert e.value.code == "COMMENT_REQUIRED" and a.status is AS.PENDING


async def test_adv18_approved_needs_no_comment_and_pending_is_not_a_decision():
    s = svc()
    a = await s.ensure("p", AK.AWARD, "k", ("R",))
    with pytest.raises(ApprovalError) as e:
        await s.resolve(a.id, AS.PENDING, "u", R.BUYER, LONG)
    assert e.value.code == "INVALID_STATUS" and a.status is AS.PENDING
    assert (await s.resolve(a.id, AS.APPROVED, "u", R.BUYER)).status is AS.APPROVED


async def test_adv18_decisions_are_final_and_version_locked():
    s = svc()
    a = await s.ensure("p", AK.AWARD, "k", ("R",))
    with pytest.raises(ApprovalError) as e:
        await s.resolve(a.id, AS.APPROVED, "u", R.BUYER, version=99)  # stale version
    assert e.value.code == "APPROVAL_ALREADY_RESOLVED" and a.status is AS.PENDING
    await s.resolve(a.id, AS.REJECTED, "u", R.BUYER, LONG, version=1)
    for st in (AS.APPROVED, AS.EDITED, AS.REJECTED):  # no flip-flopping after the decision
        with pytest.raises(ApprovalError) as e2:
            await s.resolve(a.id, st, "u2", R.BUYER, LONG)
        assert e2.value.code == "APPROVAL_ALREADY_RESOLVED"
    assert a.status is AS.REJECTED and a.version == 2 and a.resolver_id == "u"


async def test_adv18_ensure_is_idempotent_and_never_reopens_or_auto_approves():
    s = svc()
    a = await s.ensure("p", AK.SIGNATURE, "sign", ("R",))
    assert a.status is AS.PENDING
    await s.resolve(a.id, AS.REJECTED, "legal1", R.LEGAL, LONG)
    again = await s.ensure("p", AK.SIGNATURE, "sign", ("R",))
    assert again is a and again.status is AS.REJECTED  # asking again does not reset or flip it
    assert len([x for x in s.all("p")]) == 1


async def test_adv18_human_decisions_are_audited_with_the_human_identity():
    s = svc()
    a = await s.ensure("p", AK.BANK_DETAILS, "bank", ("R",))
    await s.resolve(a.id, AS.APPROVED, "fin-42", R.FINANCE)
    rec = [r for r in await s.audit.store.read("p") if r.action == "approval_resolved"][0]
    assert rec.actor_type == "human" and rec.approver_id == "fin-42" and rec.outcome == "APPROVED"


async def test_adv18_rubber_stamping_is_flagged():
    s = svc()
    for i in range(25):
        a = await s.ensure("p", AK.AWARD, f"k{i}", ("R",))
        await s.resolve(a.id, AS.APPROVED, "lazy", R.BUYER)
    flag = [r for r in s.override_rates() if r["resolver"] == "lazy"][0]
    assert flag["flag"] == "POSSIBLE_RUBBER_STAMP" and flag["override_rate"] == 0


# ---- the run can never move past an unresolved checkpoint
async def test_adv18_run_stops_at_the_first_checkpoint_and_stays_stopped():
    rt = build_project(ScenarioSpec(), VIS, FULL)
    ex = RunExecutor(rt)
    for _ in range(3):
        await ex.run()
        assert rt.state.status is PS.ESCALATED and rt.state.stage is S.NEED
    pend = rt.approvals.pending("P1")
    assert [a.kind for a in pend] == [AK.REQUIREMENT]
    assert {e.type for e in rt.bus.log} == {T.BRIEF}  # nothing downstream was sent


async def test_adv18_no_approval_is_ever_resolved_without_a_human_call():
    rt = build_project(ScenarioSpec(), VIS, FULL)
    await RunExecutor(rt).run()
    assert all(a.status is AS.PENDING for a in rt.approvals.all("P1"))
    assert not [r for r in await rt.audit.store.read("P1") if r.action == "approval_resolved"]


GATES = [
    (AK.REQUIREMENT, "invite"), (AK.TECHNICAL_QUAL, "rfq"), (AK.AWARD, "draft"), (AK.SIGNATURE, "sign"), (AK.BANK_DETAILS, "hold"),
]


@pytest.mark.parametrize("kind,downstream", GATES)
async def test_adv18_a_rejected_checkpoint_never_proceeds(kind, downstream):
    rt = build_project(ScenarioSpec(), VIS, FULL)
    res = Resolver(decisions={kind: (AS.REJECTED, {}, LONG)})
    st = await run_to_completion(rt, res)
    assert st.status in (PS.STOPPED,) and st.outcome in (OutcomeEnum.STOPPED_BY_USER, OutcomeEnum.NO_VIABLE_SUPPLIER)
    types = {e.type for e in rt.bus.log}
    actions = [r.action for r in await rt.audit.store.read("P1")]
    if downstream == "invite":
        assert T.INVITE not in types
    if downstream == "rfq":
        assert T.RFQ not in types and st.awarded is None
    if downstream == "draft":
        assert T.CONTRACT_DRAFT not in types and st.contract is None
    if downstream == "sign":
        assert st.contract and "signature_hash" not in st.contract
    if downstream == "hold":
        assert st.hold is None and "ledger_hold_created" not in actions
        assert not any(a.kind is AK.HOLD_RELEASE for a in rt.approvals.all("P1"))
    with pytest.raises(IllegalTransition):  # and it cannot be restarted behind the human's back
        await RunExecutor(rt).run()


async def test_adv18_rejected_award_does_not_even_open_contracting():
    rt = build_project(ScenarioSpec(), VIS, FULL)
    st = await run_to_completion(rt, Resolver(decisions={AK.AWARD: (AS.REJECTED, {}, LONG)}))
    assert st.stage is S.SOURCING and st.outcome is OutcomeEnum.STOPPED_BY_USER and st.awarded is None


async def test_adv18_hold_release_stays_pending_after_a_complete_run():
    rt = build_project(ScenarioSpec(), VIS, FULL)
    st = await run_to_completion(rt)
    assert st.status is PS.DONE and st.hold == "HELD"
    hold = [a for a in rt.approvals.all("P1") if a.kind is AK.HOLD_RELEASE][0]
    assert hold.status is AS.PENDING  # only a FINANCE human can ever release it
    for role in (R.BUYER, R.ADMIN, R.LEGAL, R.EVALUATOR, R.SUPPLIER):
        with pytest.raises(ApprovalError):
            await rt.approvals.resolve(hold.id, AS.APPROVED, "x", role)
    assert (await rt.approvals.resolve(hold.id, AS.APPROVED, "fin", R.FINANCE)).status is AS.APPROVED


async def test_adv18_agents_have_no_channel_to_resolve_approvals_or_release_holds(bus):
    ch = AgentChannel("buyer", bus)
    assert not any(k in dir(ch) for k in ("resolve", "approve", "release", "approvals", "hold"))


async def test_adv18_edited_award_cannot_name_an_arbitrary_supplier():
    rt = build_project(ScenarioSpec(), VIS, FULL)
    st = await run_to_completion(rt, Resolver(decisions={AK.AWARD: (AS.EDITED, {"supplier": "S-NOT-IN-DEALS"}, LONG)}))
    assert st.awarded in st.deals


async def test_adv18_edited_technical_qualification_can_only_remove_suppliers():
    base = await run_to_completion(build_project(ScenarioSpec(), VIS, FULL))
    drop = base.included[0]
    rt = build_project(ScenarioSpec(), VIS, FULL)
    st = await run_to_completion(rt, Resolver(decisions={AK.TECHNICAL_QUAL: (AS.EDITED, {"fail": [drop], "include": ["S99999"]}, LONG)}))
    assert drop not in st.included and "S99999" not in st.included


async def test_adv18_clause_waiver_requires_legal_edited_with_explicit_flag():
    base = await run_to_completion(build_project(ScenarioSpec(), VIS, FULL))
    from sourceloop.agents.supplier.agent import SupplierProfile
    spec = lambda: ScenarioSpec(profiles={base.awarded: SupplierProfile(redline_locked_clause="ip")})  # noqa: E731
    for decision in ((AS.APPROVED, {}, ""), (AS.EDITED, {"accept_change": False}, LONG), (AS.REJECTED, {}, LONG)):
        rt = build_project(spec(), VIS, FULL)
        st = await run_to_completion(rt, Resolver(decisions={AK.CLAUSE: decision}))
        assert (st.contract or {}).get("deviations", []) == [] and not rt.bus.waivers, decision
    rt = build_project(spec(), VIS, FULL)
    st = await run_to_completion(rt, Resolver(decisions={AK.CLAUSE: (AS.EDITED, {"accept_change": True}, LONG)}))
    assert st.contract["deviations"] == ["ip"] and not rt.bus.waivers  # waiver is revoked right after use


async def test_adv18_flagged_suppliers_are_excluded_unless_a_human_explicitly_clears_them():
    rt = build_project(ScenarioSpec(poisoned_news=frozenset({"VN", "CN", "DE", "MX", "PL", "US", "KR", "TH", "MY", "IN", "TR"})), VIS, FULL)
    st = await run_to_completion(rt, Resolver(decisions={AK.COMPLIANCE_FLAG: (AS.APPROVED, {}, "")}))  # "approve" != "clear"
    flagged = {s for s, q in st.qual.items() if q["status"] in ("REVIEW", "BLOCKED")}
    assert flagged and not (flagged & set(st.included))


# ---- stages / statuses cannot be skipped or disabled
@pytest.mark.parametrize("stage", [S.NEED, S.DISCOVERY, S.QUALIFICATION, S.SOURCING, S.CONTRACTING, S.DONE])
def test_adv18_mandatory_stages_cannot_be_disabled(stage):
    with pytest.raises(ValueError):
        enabled_stages(frozenset({stage}))
    with pytest.raises(ValueError):
        RunExecutor(build_project(ScenarioSpec(run=RunConfig(disabled_stages=frozenset({stage}))), VIS, FULL))


def test_adv18_stage_order_is_linear_and_done_is_terminal():
    stages = enabled_stages()
    walked = [S.NEED]
    while walked[-1] is not S.DONE:
        walked.append(next_stage(walked[-1], stages))
    assert walked == [S.NEED, S.DISCOVERY, S.QUALIFICATION, S.SOURCING, S.CONTRACTING, S.ONBOARDING, S.DONE]
    with pytest.raises(IllegalTransition):
        next_stage(S.DONE, stages)


def test_adv18_every_undeclared_status_transition_is_illegal():
    for a in PS:
        for b in PS:
            if b in STATUS_TRANSITIONS[a]:
                check_status(a, b)
            else:
                with pytest.raises(IllegalTransition):
                    check_status(a, b)
    assert not STATUS_TRANSITIONS[PS.DONE] and not STATUS_TRANSITIONS[PS.STOPPED]
    assert PS.DONE not in STATUS_TRANSITIONS[PS.ESCALATED] and PS.DONE not in STATUS_TRANSITIONS[PS.DRAFT]
