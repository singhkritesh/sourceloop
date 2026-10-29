"""Studio fields that used to be inert and are now consumed from the pinned config (rule 15a), plus the guarantee that no
new field can skip an invariant checkpoint (rule 15b)."""
import pytest

from api_support import build_world, run_to_done, w  # noqa: F401
from sourceloop.agents.common import contracts as K
from sourceloop.api.service import build_spec_from_config, registry
from sourceloop.data.enums import ApprovalKindEnum as AK
from sourceloop.data.enums import OutcomeEnum
from sourceloop.data.enums import ProjectStatusEnum as PS
from sourceloop.data.enums import StageEnum as S
from sourceloop.orchestrator.runtime import RunExecutor
from sourceloop.orchestrator.scenario import Resolver, build_project, run_to_completion
from sourceloop.studio.defaults import default_config
from sourceloop.studio.invariants import check_invariants
from sourceloop.studio.schema import StudioConfig
from sourceloop.studio.validator import validate

MANDATORY = {AK.TECHNICAL_QUAL, AK.AWARD, AK.SIGNATURE, AK.BANK_DETAILS, AK.HOLD_RELEASE}


def cfg_with(mutate) -> StudioConfig:
    raw = default_config().model_dump(mode="json")
    mutate(raw)
    rep = validate(raw, is_admin=True)
    assert rep.ok, rep.errors
    return StudioConfig.model_validate(raw)


def codes(raw_or_cfg) -> set[str]:
    return {x.code for x in validate(raw_or_cfg, is_admin=True).warnings}


async def build(cfg: StudioConfig, scenario: str = "happy"):
    spec = await build_spec_from_config(cfg, 1, scenario, None, 42)
    vis, full = registry(42)
    return build_project(spec, vis, full)


def kinds(rt) -> set[AK]:
    return {a.kind for a in rt.approvals.all(rt.project_id)}


# ---------------------------------------------------------------- workflow.autonomy
async def test_autonomy_l3_on_need_auto_confirms_requirement_audited():
    base = await build(default_config())
    await run_to_completion(base)
    assert AK.REQUIREMENT in kinds(base)
    rt = await build(cfg_with(lambda c: c["workflow"]["autonomy"].update(NEED=3)))
    st = await run_to_completion(rt)
    assert st.status is PS.DONE and AK.REQUIREMENT not in kinds(rt) and MANDATORY <= kinds(rt)
    events = [e for e in await _events(rt) if e.action == "checkpoint_skipped"]
    assert events and events[0].rule_id == "autonomy_act_then_report" and events[0].outcome == "AUTO_CONFIRMED"
    # L1/L2 keep the human in the loop
    rt2 = await build(cfg_with(lambda c: c["workflow"]["autonomy"].update(NEED=2)))
    await run_to_completion(rt2)
    assert AK.REQUIREMENT in kinds(rt2)


def test_autonomy_ceiling_and_no_effect_warning():
    raw = default_config().model_dump(mode="json")
    raw["workflow"]["autonomy"] = {"CONTRACTING": 3}
    assert "INV_CONTRACT_AUTONOMY_MAX_L2" in {v.invariant_id for v in check_invariants(raw)}
    raw["workflow"]["autonomy"] = {"SOURCING": 4, "CONTRACTING": 2}
    rep = validate(raw, is_admin=True)
    assert rep.ok and "AUTONOMY_NO_EFFECT" in {x.code for x in rep.warnings}


# ---------------------------------------------------------------- checkpoints.batched_async
async def test_batched_async_defers_requirement_but_blocks_before_any_mandatory_checkpoint():
    rt = await build(cfg_with(lambda c: c["workflow"]["checkpoints"].update(batched_async=True)))
    await RunExecutor(rt).run()
    assert rt.state.status is PS.ESCALATED and rt.state.longlist  # discovery already ran
    assert kinds(rt) == {AK.REQUIREMENT}                          # and nothing mandatory was raised yet
    pend = rt.approvals.pending(rt.project_id)
    assert [a.kind for a in pend] == [AK.REQUIREMENT]
    assert any(e.action == "checkpoint_deferred" for e in await _events(rt))
    st = await run_to_completion(rt)
    assert st.status is PS.DONE and MANDATORY <= kinds(rt) and AK.REQUIREMENT in kinds(rt)
    # default: the same run blocks BEFORE discovery
    rt0 = await build(default_config())
    await RunExecutor(rt0).run()
    assert not rt0.state.longlist


async def test_batched_async_rejection_stops_the_run():
    rt = await build(cfg_with(lambda c: c["workflow"]["checkpoints"].update(batched_async=True)))
    ex = RunExecutor(rt)
    await ex.run()
    a = rt.approvals.pending(rt.project_id)[0]
    from sourceloop.data.enums import ApprovalStatusEnum as AS
    from sourceloop.data.enums import RoleEnum
    await rt.approvals.resolve(a.id, AS.REJECTED, "u1", RoleEnum.BUYER, "requirement is wrong, stop")
    st = await ex.run()
    assert st.status is PS.STOPPED and st.outcome is OutcomeEnum.STOPPED_BY_USER and not (kinds(rt) & MANDATORY)


async def _events(rt):
    return await rt.audit.store.read(rt.project_id) if hasattr(rt.audit.store, "read") else []


# ---------------------------------------------------------------- agents.supplier_lead.max_tool_calls
async def test_supplier_max_tool_calls_limits_a_supplier_turn():
    ok = await run_to_completion(await build(default_config()))
    assert ok.outcome is OutcomeEnum.DEAL
    rt = await build(cfg_with(lambda c: c["agents"]["supplier_lead"].update(max_tool_calls=1)))
    st = await run_to_completion(rt)
    assert st.outcome is OutcomeEnum.NO_DEAL  # an RFQ turn needs 4 tool calls; the guard fails closed (no offers)
    assert rt.supplier(st.shortlist[0]).agent.max_tool_calls == 1


def test_max_tool_calls_on_a_subagent_is_flagged_unused():
    raw = default_config().model_dump(mode="json")
    raw["agents"][K.LANDED]["max_tool_calls"] = 3
    assert "MAX_TOOL_CALLS_UNUSED" in codes(raw)


# ---------------------------------------------------------------- agents.*.prompt_ref
def test_prompt_ref_resolves_to_a_prompt_body_and_is_validated():
    body = "Assess {{region}} risk from the articles; be strict."
    cfg = cfg_with(lambda c: (c["prompts"]["regional_news_risk"].update(body=body),
                              c["agents"]["regional_news_risk"].update(prompt_ref="regional_news_risk")))
    assert cfg.prompt_bodies()["regional_news_risk"] == body
    raw = default_config().model_dump(mode="json")
    raw["agents"]["regional_news_risk"]["prompt_ref"] = "nope"
    assert "PROMPT_MISSING" in validate(raw, is_admin=True).codes()
    raw["agents"]["regional_news_risk"]["prompt_ref"] = None
    raw["agents"][K.LANDED]["prompt_ref"] = "regional_news_risk"
    assert "PROMPT_REF_UNUSED" in codes(raw)


async def test_prompt_body_reaches_the_news_tool():
    body = "Assess {{region}} risk from the articles; be strict."
    rt = await build(cfg_with(lambda c: c["prompts"]["regional_news_risk"].update(body=body)))
    assert rt.llm is not None
    spec = await build_spec_from_config(cfg_with(lambda c: c["prompts"]["regional_news_risk"].update(body=body)), 1, "happy", None, 42)
    assert spec.prompts["regional_news_risk"] == body


# ---------------------------------------------------------------- news timeout precedence
async def test_agent_timeout_wins_over_workflow_news_timeout():
    cfg = cfg_with(lambda c: (c["agents"]["regional_news_risk"].update(timeout_s=7),
                              c["workflow"].update(news_timeout_s=55.0)))
    rt = await build(cfg)
    assert rt.buyer.tools._tools[K.NEWS].timeout_s == 7.0
    assert "NEWS_TIMEOUT_SUPERSEDED" in codes(cfg)
    only_wf = cfg_with(lambda c: (c["agents"].pop("regional_news_risk"), c["workflow"].update(news_timeout_s=33.0)))
    assert (await build(only_wf)).buyer.tools._tools[K.NEWS].timeout_s == 33.0  # fallback when the agent entry is absent
    assert "NEWS_TIMEOUT_SUPERSEDED" not in codes(default_config())


# ---------------------------------------------------------------- data_sources / llm informational + fallback
async def test_recorded_fallback_off_makes_feeds_unavailable_and_fails_closed():
    rt = await build(cfg_with(lambda c: (c["data_sources"].update(use_recorded_fallback=False),
                                         c["workflow"]["checkpoints"].update(requirement_confirmation=False))))
    assert not rt.buyer.tools._tools[K.NEWS].enabled and not rt.buyer.tools._tools[K.WEATHER].enabled
    await RunExecutor(rt).run()
    assert rt.state.status is PS.ESCALATED
    assert AK.COMPLIANCE_FLAG in kinds(rt)       # no news => REVIEW, a human decides; never a silent pass
    assert "NO_FALLBACK_NO_EGRESS" in codes(rt_cfg := cfg_with(lambda c: c["data_sources"].update(use_recorded_fallback=False)))
    assert rt_cfg.data_sources.use_recorded_fallback is False


async def test_informational_fields_do_not_change_the_run():
    base = await build(default_config())
    await run_to_completion(base)
    cfg = cfg_with(lambda c: (c["data_sources"].update(egress_allowlist=["news.example.com"]),
                              c["llm"].update(replay_fallback_live=True, mode="record")))
    assert {"REPLAY_FALLBACK_INFORMATIONAL", "LLM_MODE_INFORMATIONAL"} <= codes(cfg)
    assert "EGRESS_UNUSED" not in codes(cfg)   # the allowlist is consumed by live connectors, but widens nothing in a recorded run
    rt = await build(cfg)
    st = await run_to_completion(rt)
    b = base.state
    assert (st.awarded, st.shortlist, st.ranking) == (b.awarded, b.shortlist, b.ranking)


# ---------------------------------------------------------------- no new field can skip a mandatory checkpoint
def _max_skip(c):
    c["workflow"]["autonomy"] = {s.value: (2 if s is S.CONTRACTING else 4) for s in S if s is not S.DONE}
    c["workflow"]["checkpoints"].update(requirement_confirmation=False, batched_async=True)
    c["agents"]["supplier_lead"]["max_tool_calls"] = 50
    c["agents"]["buyer_lead"]["max_tool_calls"] = 50
    c["data_sources"].update(egress_allowlist=["news.example.com"])
    c["llm"].update(replay_fallback_live=True)


@pytest.mark.parametrize("scenario,extra,expected", [("poisoned_news", {AK.COMPLIANCE_FLAG}, PS.STOPPED),
                                                    ("locked_clause", {AK.CLAUSE}, PS.DONE), ("happy", set(), PS.DONE)])
async def test_no_invariant_checkpoint_is_skippable_through_any_wired_field(scenario, extra, expected):
    cfg = cfg_with(_max_skip)
    spec = await build_spec_from_config(cfg, 1, scenario, None, 42)
    vis, full = registry(42)
    rt = build_project(spec, vis, full)
    res = Resolver()
    st = await run_to_completion(rt, res)
    assert st.status is expected, (scenario, st.status, st.reason)  # not stopped early by a skipped/aborted gate
    if expected is PS.STOPPED:
        assert st.reason == "none_qualified"  # the poisoned supplier was held at the human gate, not waved through
    got = kinds(rt)
    assert extra <= got
    assert MANDATORY <= got or st.status is PS.STOPPED  # a stop is fine; skipping a gate is not
    roles = {a.kind: a.target_role.value for a in rt.approvals.all(rt.project_id)}
    assert roles.get(AK.TECHNICAL_QUAL, "QUALITY") == "QUALITY" and roles.get(AK.HOLD_RELEASE, "FINANCE") == "FINANCE"
    assert roles.get(AK.SIGNATURE, "LEGAL") == "LEGAL" and roles.get(AK.COMPLIANCE_FLAG, "COMPLIANCE") == "COMPLIANCE"
    if st.status is PS.DONE:
        assert AK.HOLD_RELEASE in got and rt.state.hold == "HELD"  # never released by an agent or a config field
    ok, _ = await rt.audit.verify(rt.project_id)
    assert ok


# ---------------------------------------------------------------- pinning through the API
async def _publish(world, mutate, note):
    a = await world.client("admin")
    cfg = (await a.get("/studio/draft")).json()["config"]
    mutate(cfg)
    assert (await a.put("/studio/draft", {"config": cfg})).status_code == 200
    r = await a.post("/studio/publish", {"note": note})
    assert r.status_code == 201, r.text


async def _create(world):
    b = await world.client("buyer")
    r = await b.post("/projects", {"title": "PCB", "need_text": "500 PCB"})
    assert r.status_code == 201, r.text
    return r.json()


async def test_new_fields_apply_to_new_projects_only_via_pinned_version(w):  # noqa: F811
    old = await _create(w)  # pinned to v1

    def v2(c):
        c["workflow"]["autonomy"]["NEED"] = 3
        c["agents"]["supplier_lead"]["max_tool_calls"] = 1
    await _publish(w, v2, "wired fields")
    new = await _create(w)
    rn, ro = w.service.handles[new["id"]].rt, w.service.handles[old["id"]].rt
    assert (rn.cfg.need_autonomy, ro.cfg.need_autonomy) == (3, 1)
    b = await w.client("buyer")
    for pid in (new["id"], old["id"]):
        assert (await b.post(f"/projects/{pid}/start")).status_code == 202
        await w.service.join(pid)
    done_new, done_old = await run_to_done(w, new["id"]), await run_to_done(w, old["id"])
    assert done_old["outcome"] == "DEAL" and done_new["outcome"] == "NO_DEAL"   # supplier turns capped only in v2
    ks = {pid: {a["kind"] for a in (await b.get(f"/projects/{pid}/escalations")).json()["items"]} for pid in (new["id"], old["id"])}
    assert "REQUIREMENT" in ks[old["id"]] and "REQUIREMENT" not in ks[new["id"]]


async def test_batched_async_through_the_api_resolves_while_running(w):  # noqa: F811
    await _publish(w, lambda c: c["workflow"]["checkpoints"].update(batched_async=True), "batched")
    p = await _create(w)
    assert w.service.handles[p["id"]].rt.cfg.batched_async is True
    b = await w.client("buyer")
    await b.post(f"/projects/{p['id']}/start")
    await w.service.join(p["id"])
    done = await run_to_done(w, p["id"])
    assert done["status"] == "DONE"
    ev = await w.client("evaluator")
    assert (await ev.get(f"/projects/{p['id']}/audit/verify")).json()["valid"] is True
