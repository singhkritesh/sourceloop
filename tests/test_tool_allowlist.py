"""Per-agent tool allowlist (security-mapping backlog 6): schema, invariant, validator, runner enforcement, audit."""
import pytest

from api_support import build_world, run_to_done, w  # noqa: F401
from sourceloop.agents.common import contracts as K
from sourceloop.agents.common.tools import Tool, ToolOutput, ToolRunner
from sourceloop.api.service import build_spec_from_config, registry
from sourceloop.audit.canonical import dumps
from sourceloop.data.enums import OutcomeEnum
from sourceloop.orchestrator.scenario import build_project, run_to_completion
from sourceloop.studio.defaults import default_config
from sourceloop.studio.invariants import check_invariants
from sourceloop.studio.lifecycle import ConfigResolver, config_payload, content_hash
from sourceloop.studio.schema import StudioConfig
from sourceloop.studio.validator import validate

pytestmark = pytest.mark.adversarial


def cfg_with(mutate) -> StudioConfig:
    raw = default_config().model_dump(mode="json")
    mutate(raw)
    rep = validate(raw, is_admin=True)
    assert rep.ok, rep.errors
    return StudioConfig.model_validate(raw)


async def build(cfg):
    spec = await build_spec_from_config(cfg, 1, "happy", None, 42)
    vis, full = registry(42)
    return build_project(spec, vis, full)


def test_unset_allowlist_keeps_dump_and_hash_unchanged():
    c = default_config()
    assert "allowed_tools" not in dumps(config_payload(c))  # old published versions keep their content hash
    assert c.allowed_tools("buyer_lead") == frozenset({K.RESEARCH, K.COMPLIANCE, K.NEWS, K.WEATHER, K.LANDED})
    assert c.allowed_tools("supplier_lead") == frozenset({K.DEMAND, K.BUYER_VERIFY, K.CAPACITY, K.QUOTE, K.DOCS})
    c2 = cfg_with(lambda r: r["agents"]["buyer_lead"].update(allowed_tools=[K.RESEARCH, K.COMPLIANCE, K.LANDED]))
    assert content_hash(c2) != content_hash(c)
    assert StudioConfig.model_validate_json(c2.model_dump_json()).agents["buyer_lead"].allowed_tools == (K.RESEARCH, K.COMPLIANCE, K.LANDED)


async def test_old_payload_without_field_still_loads_through_resolver(w):  # noqa: F811
    from sourceloop.data import db as dbm
    from sourceloop.studio.sql_store import SqlStudioStore

    async with dbm.session_scope() as s:
        store = SqlStudioStore(s)
        v = (await store.list_versions())[0]
        assert "allowed_tools" not in v.payload_json
        cfg = await ConfigResolver(store).get(v.number)  # verifies content hash + invariants
        assert cfg.agents["buyer_lead"].allowed_tools is None


def test_invariant_blocks_removing_compliance_screen():
    raw = default_config().model_dump(mode="json")
    raw["agents"]["buyer_lead"]["allowed_tools"] = [K.RESEARCH, K.NEWS, K.WEATHER, K.LANDED]
    assert "INV_COMPLIANCE_TOOL_ALLOWED" in {v.invariant_id for v in check_invariants(raw)}
    rep = validate(raw, is_admin=True)
    assert not rep.ok and "INV_COMPLIANCE_TOOL_ALLOWED" in {i.invariant_id for i in rep.errors}
    # also for a constructed (non-validated) object, as the runtime check sees it
    bad = StudioConfig.model_validate(default_config().model_dump(mode="json"))
    agents = dict(bad.agents)
    agents["buyer_lead"] = agents["buyer_lead"].model_copy(update={"allowed_tools": (K.RESEARCH,)})
    obj = StudioConfig.model_construct(**{**dict(bad), "agents": agents})
    assert "INV_COMPLIANCE_TOOL_ALLOWED" in {v.invariant_id for v in check_invariants(obj)}


def test_validator_warns_on_unknown_names_and_errors_on_required_removal():
    raw = default_config().model_dump(mode="json")
    raw["agents"]["buyer_lead"]["allowed_tools"] = [K.RESEARCH, K.COMPLIANCE, K.LANDED, "shell_exec", K.QUOTE]
    rep = validate(raw, is_admin=True)
    assert rep.ok and {x.code for x in rep.warnings} >= {"ALLOWED_TOOL_UNKNOWN"}
    raw["agents"]["buyer_lead"]["allowed_tools"] = [K.RESEARCH, K.COMPLIANCE]  # landed_cost is required
    assert "ALLOWED_TOOL_REQUIRED" in validate(raw, is_admin=True).codes()
    raw["agents"]["buyer_lead"].pop("allowed_tools")
    raw["agents"][K.LANDED]["allowed_tools"] = [K.LANDED]
    assert "ALLOWED_TOOLS_UNUSED" in {x.code for x in validate(raw, is_admin=True).warnings}


async def test_runner_rejects_unlisted_tool_and_audits():
    from pydantic import BaseModel

    class I(BaseModel):  # noqa: E742
        x: int = 1

    async def fn(i):
        return ToolOutput[I](value=I())

    seen, denied = [], []

    async def hook(kind, data):
        seen.append((kind, data.get("error")))

    async def on_denied(t):
        denied.append(t)

    r = ToolRunner([Tool("a", I, I, fn), Tool("b", I, I, fn)], hook=hook)
    r.on_denied = on_denied
    assert (await r.call("b", I())).ok  # no allowlist: legacy behaviour
    r.set_allowed({"a"})
    res = await r.call("b", I())
    assert not res.ok and res.error == "NOT_ALLOWED" and denied == ["b"] and ("subagent_result", "NOT_ALLOWED") in seen
    assert (await r.call("zzz", I())).error == "NOT_ALLOWED"  # unknown names are denied by the allowlist, not just unknown
    assert (await r.call("a", I())).ok
    r.set_allowed(set())  # empty = nothing allowed (fail closed)
    assert (await r.call("a", I())).error == "NOT_ALLOWED"


async def test_removed_tool_changes_new_projects_only_and_is_audited():
    base = await build(default_config())
    st0 = await run_to_completion(base)
    assert st0.outcome is OutcomeEnum.DEAL
    assert not [e for e in base.audit.store._rows[base.project_id] if e.action == "tool_denied"]

    no_news = cfg_with(lambda r: r["agents"]["buyer_lead"].update(
        allowed_tools=[K.RESEARCH, K.COMPLIANCE, K.WEATHER, K.LANDED]))
    rt = await build(no_news)
    await run_to_completion(rt)
    denied = [e for e in rt.audit.store._rows[rt.project_id] if e.action == "tool_denied"]
    assert denied and denied[0].rule_id == "NOT_ALLOWED" and denied[0].evidence[0] == {"agent": "buyer", "tool": K.NEWS}
    assert rt.buyer.tools.stats["not_allowed"] > 0
    # the previously built (pinned) project is untouched, and a fresh default build is identical to the first
    again = await build(default_config())
    assert again.buyer.tools._allowed == default_config().allowed_tools("buyer_lead")
    assert (await run_to_completion(again)).outcome is OutcomeEnum.DEAL


async def test_buyer_runner_always_keeps_compliance_even_if_spec_omits_it():
    spec = await build_spec_from_config(default_config(), 1, "happy", None, 42)
    spec.buyer_allowed_tools = frozenset({K.RESEARCH, K.LANDED})  # tampered spec
    vis, full = registry(42)
    rt = build_project(spec, vis, full)
    assert K.COMPLIANCE in rt.buyer.tools._allowed
