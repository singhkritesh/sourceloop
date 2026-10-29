"""Lifecycle, vault, defaults vs. demo behaviour."""
import json
import logging
import pickle

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from sourceloop.clock import FixedClock
from sourceloop.data.enums import OutcomeEnum, RoleEnum
from sourceloop.data.enums import ProjectStatusEnum as PS
from sourceloop.data.synthetic import generate
from sourceloop.demo import live_ollama
from sourceloop.orchestrator.runtime import RunConfig
from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion
from sourceloop.studio.defaults import DEFAULT_CHAT, DEFAULT_FAST, OLLAMA_URL, default_config
from sourceloop.studio.lifecycle import (
    Actor,
    ConfigResolver,
    ConfigVersion,
    InvariantViolationError,
    LifecycleError,
    MemoryStudioStore,
    StudioLifecycle,
    content_hash,
    diff_configs,
)
from sourceloop.studio.schema import StudioConfig
from sourceloop.studio.vault import ENV_KEY, MemoryVaultBackend, Vault, VaultError

ADMIN = Actor("admin1", RoleEnum.SUPER_ADMIN)
DESIGNER = Actor("des1", RoleEnum.DESIGNER)
SECRET = "sk-ant-SUPERSECRET-123456"  # noqa: S105


def make():
    events: list[dict] = []
    store = MemoryStudioStore()
    vault = Vault(MemoryVaultBackend(), FixedClock(), Fernet(Fernet.generate_key()))
    return StudioLifecycle(store, events.append, FixedClock(), vault), store, events, vault


def raw():
    return default_config().model_dump(mode="json")


async def pub(lc, note="v"):
    return await lc.publish(ADMIN, note, payload=raw())


# ---------- publish / immutability / pinning ----------
async def test_publish_versions_hash_and_audit():
    lc, store, events, _ = make()
    v1 = await pub(lc)
    assert v1.number == 1 and v1.content_hash == content_hash(default_config()) and await store.get_active() == 1
    r = raw()
    r["workflow"]["longlist_size"] = 12
    v2 = await lc.publish(ADMIN, "bigger", payload=r)
    assert v2.number == 2 and v2.content_hash != v1.content_hash
    assert [e["action"] for e in events] == ["studio.published"] * 2
    assert events[1]["version"] == 2 and events[1]["hash"] == v2.content_hash
    assert "workflow.longlist_size" in events[1]["detail"]["changed_paths"]
    assert (await store.list_versions())[0].status == "ARCHIVED"


async def test_published_objects_are_immutable():
    lc, store, _, _ = make()
    v = await pub(lc)
    with pytest.raises(ValidationError):
        v.note = "x"
    with pytest.raises(ValidationError):
        v.payload_json = "{}"
    res = ConfigResolver(store)
    cfg = await res.get(1)
    with pytest.raises(ValidationError):
        cfg.llm.mode = "live"
    with pytest.raises(TypeError):
        cfg.llm.roles["fast"] = cfg.llm.roles["chat"]
    assert (await res.get(1)) is cfg  # cached immutable instance


async def test_publish_requires_admin_and_valid():
    lc, store, _, _ = make()
    with pytest.raises(LifecycleError) as e:
        await lc.publish(DESIGNER, payload=raw())
    assert e.value.code == "FORBIDDEN"
    bad = raw()
    bad["clauses"]["ip"]["negotiable"] = True
    with pytest.raises(LifecycleError) as e:
        await lc.publish(ADMIN, payload=bad)
    assert e.value.code == "VALIDATION_FAILED" and "INV_LOCKED_CLAUSES" in {i.invariant_id for i in e.value.report.errors}
    bad = raw()
    bad["llm"]["roles"]["fast"]["provider"] = "nope"
    with pytest.raises(LifecycleError):
        await lc.publish(ADMIN, payload=bad)
    assert await store.latest_number() == 0


async def test_draft_flow_saves_invalid_blocks_publish():
    lc, store, events, _ = make()
    await pub(lc)
    d = await lc.start_draft(DESIGNER)
    d["clauses"]["liability"]["locked"] = False
    rep, ev = await lc.save_draft(DESIGNER, d)
    assert not rep.ok and ev["action"] == "studio.draft_saved"
    assert not (await lc.validate_draft(DESIGNER)).ok
    with pytest.raises(LifecycleError):
        await lc.publish(ADMIN)  # admin's own (empty) draft
    d["clauses"]["liability"]["locked"] = True
    await lc.save_draft(ADMIN, d)
    v = await lc.publish(ADMIN, "from draft")
    assert v.number == 2 and await store.get_draft(ADMIN.id) is None


async def test_rollback_is_revalidated_copy_as_new_version(monkeypatch):
    lc, store, events, _ = make()
    await pub(lc)
    r = raw()
    r["workflow"]["longlist_size"] = 20
    await lc.publish(ADMIN, payload=r)
    v3 = await lc.rollback(ADMIN, 1)
    assert v3.number == 3 and v3.rollback_of == 1 and v3.content_hash == (await store.get_version(1)).content_hash
    assert events[-1]["action"] == "studio.rolled_back" and await store.get_active() == 3
    assert (await ConfigResolver(store).get(3)).workflow.longlist_size == 10
    # invariants tightened later: an old version that no longer passes cannot be rolled back to
    old = ConfigVersion(number=4, content_hash="x", payload_json=json.dumps({**raw(), "agents": {
        **raw()["agents"], "compliance_screen": {**raw()["agents"]["compliance_screen"], "enabled": False}}}),
        author="legacy", created_at=FixedClock().now())
    await store.add_version(old)
    with pytest.raises(LifecycleError) as e:
        await lc.rollback(ADMIN, 4)
    assert e.value.code == "VALIDATION_FAILED"
    with pytest.raises(LifecycleError):
        await lc.rollback(ADMIN, 99)


async def test_resolver_rejects_tampering_and_runtime_invariant_breach():
    lc, store, _, _ = make()
    v = await pub(lc)
    # tampered payload (hash mismatch)
    r = raw()
    r["workflow"]["longlist_size"] = 3
    store._versions[1] = v.model_copy(update={"payload_json": json.dumps(r)})
    with pytest.raises(LifecycleError) as e:
        await ConfigResolver(store).get(1)
    assert e.value.code == "CONFIG_TAMPERED"
    # version inserted behind the validator's back, hash correct but violating an invariant
    r = raw()
    r["agents"]["compliance_screen"]["enabled"] = False
    cfg = StudioConfig.model_validate(r)
    from sourceloop.audit.canonical import dumps
    store._versions[2] = ConfigVersion(number=2, content_hash=content_hash(cfg), payload_json=dumps(cfg.model_dump(mode="json")),
                                       author="x", created_at=FixedClock().now())
    with pytest.raises(InvariantViolationError) as e:
        await ConfigResolver(store).get(2)
    assert "INV_COMPLIANCE_ENABLED" in e.value.ids


async def test_pinning_is_by_version_not_active():
    lc, store, _, _ = make()
    await pub(lc)
    res = ConfigResolver(store)
    n, _ = await res.active()
    r = raw()
    r["workflow"]["shortlist_size"] = 4
    await lc.publish(ADMIN, payload=r)
    assert (await res.get(n)).workflow.shortlist_size == 5  # pinned project still sees v1
    assert (await res.get(2)).workflow.shortlist_size == 4


async def test_roundtrip_published_config():
    lc, store, _, _ = make()
    r = raw()
    r["rules"] = [{"id": "CFG_LEAD", "signal": "offer_lead_time_weeks", "op": ">", "value": "12"}]
    r["workflow"]["autonomy"] = {"CONTRACTING": 2}
    r["workflow"]["negotiation"]["min_step_pct"] = "0.75"
    v = await lc.publish(ADMIN, payload=r)
    cfg = v.config()
    assert content_hash(cfg) == v.content_hash and StudioConfig.model_validate(cfg.model_dump(mode="json")) == cfg
    assert str(cfg.workflow.negotiation.min_step_pct) == "0.75"


# ---------- diff / export / import ----------
async def test_diff_and_export_have_no_secrets():
    lc, store, events, vault = make()
    r = raw()
    r["providers"]["hosted"] = {"kind": "ANTHROPIC", "base_url": "https://api.anthropic.com", "key_ref": "hosted"}
    await lc.publish(ADMIN, payload=r)
    await lc.set_provider_key(ADMIN, "hosted", SECRET)
    r2 = json.loads(json.dumps(r))
    r2["providers"]["hosted"]["key_ref"] = "hosted2"
    r2["workflow"]["longlist_size"] = 9
    await lc.publish(ADMIN, payload=r2)
    d = await lc.diff(1, 2)
    paths = {i.path: i for i in d}
    assert paths["workflow.longlist_size"].after == 9
    assert paths["providers.hosted.key_ref"].before == "****" and paths["providers.hosted.key_ref"].after == "****"
    bundle = await lc.export_bundle(ADMIN, 2)
    assert bundle["config"]["providers"]["hosted"]["key_ref"] is None
    blob = json.dumps(bundle, default=str) + json.dumps([[i.path, i.before, i.after] for i in d], default=str) + json.dumps(events, default=str)
    assert SECRET not in blob and "hosted2" not in json.dumps(bundle)
    ev = [e for e in events if e["action"] == "studio.provider_key_set"][0]
    assert ev["detail"]["provider"] == "hosted" and SECRET not in json.dumps(ev)


async def test_import_goes_to_draft_validated_and_strips_refs():
    lc, store, events, _ = make()
    r = raw()
    r["providers"]["hosted"] = {"kind": "ANTHROPIC", "base_url": "https://api.anthropic.com", "key_ref": "x"}
    await lc.publish(ADMIN, payload=r)
    b = await lc.export_bundle(ADMIN, 1)
    b["config"]["providers"]["hosted"]["key_ref"] = "sneaky"
    rep = await lc.import_bundle(ADMIN, b)
    assert rep.ok and (await store.get_draft(ADMIN.id))["providers"]["hosted"]["key_ref"] is None
    assert await store.latest_number() == 1  # import never publishes
    with pytest.raises(LifecycleError) as e:
        await lc.import_bundle(ADMIN, {**b, "schema_version": 99})
    assert e.value.code == "UNSUPPORTED_SCHEMA_VERSION" and e.value.detail["needed"] == 1
    bad = json.loads(json.dumps(b))
    bad["config"]["clauses"]["ip"]["negotiable"] = True
    assert not (await lc.import_bundle(ADMIN, bad)).ok
    huge = {**b, "pad": "x" * (2 * 1024 * 1024 + 1)}
    with pytest.raises(LifecycleError) as e:
        await lc.import_bundle(ADMIN, huge)
    assert e.value.code == "BUNDLE_TOO_LARGE"
    with pytest.raises(LifecycleError):
        await lc.import_bundle(DESIGNER, b)


def test_diff_configs_pure():
    a = default_config()
    b = StudioConfig.model_validate({**raw(), "llm": {**raw()["llm"], "mode": "live"}})
    assert [i.path for i in diff_configs(a, b)] == ["llm.mode"]


# ---------- vault ----------
def test_vault_never_leaks(monkeypatch, caplog):
    be = MemoryVaultBackend()
    v = Vault(be, FixedClock(), Fernet(Fernet.generate_key()))
    v.put_key("p", SECRET)
    assert v.has_key("p") and not v.has_key("q") and v.resolve("p") == SECRET
    for s in (repr(v), str(v), repr(be), f"{v!r}{v}"):
        assert SECRET not in s
    assert SECRET.encode() not in be.get("p")[0]
    with pytest.raises(TypeError):
        pickle.dumps(v)
    with pytest.raises(VaultError) as e:
        v.resolve("missing")
    assert SECRET not in str(e.value) and e.value.code == "KEY_NOT_SET"
    for bad in ("", "   ", "x" * 5000):
        with pytest.raises(VaultError) as e:
            v.put_key("p", bad)
        assert "xxxx" not in repr(e.value)
    other = Vault(be, FixedClock(), Fernet(Fernet.generate_key()))  # wrong master key
    with pytest.raises(VaultError) as e:
        other.resolve("p")
    assert e.value.code == "KEY_UNREADABLE"
    v.delete_key("p")
    assert not v.has_key("p")


def test_vault_master_key_env_and_dev_fallback(monkeypatch, caplog):
    key = Fernet.generate_key()
    monkeypatch.setenv(ENV_KEY, key.decode())
    v = Vault(MemoryVaultBackend(), FixedClock())
    v.put_key("p", SECRET)
    assert v.resolve("p") == SECRET
    monkeypatch.setenv(ENV_KEY, "not-a-fernet-key-" + SECRET)
    with pytest.raises(VaultError) as e:
        Vault(MemoryVaultBackend(), FixedClock())
    assert SECRET not in str(e.value) and SECRET not in repr(e.value) and e.value.__cause__ is None
    monkeypatch.delenv(ENV_KEY)
    with caplog.at_level(logging.WARNING):
        v = Vault(MemoryVaultBackend(), FixedClock())
    v.put_key("p", "abc")
    assert v.resolve("p") == "abc"


def test_config_has_no_key_material_in_json_schema_or_llm_config():
    c = default_config()
    llm = c.to_llm_config(has_key={"ollama": True})
    assert llm.providers["ollama"].has_key is True
    assert "key" not in {k for k in llm.providers["ollama"].model_dump() if k not in ("has_key",)}


# ---------- defaults reproduce demo ----------
def test_default_matches_demo_live_ollama_and_run_defaults():
    c = default_config()
    demo_cfg, _, _ = live_ollama(OLLAMA_URL, DEFAULT_FAST, DEFAULT_CHAT)
    mine = c.to_llm_config().model_copy(update={"mode": "live"})
    assert mine.roles == demo_cfg.roles and mine.providers == demo_cfg.providers and mine.overrides == {}
    assert c.to_run_config(1) == RunConfig()
    spec = c.to_scenario_spec()
    base = ScenarioSpec()
    for f in ("seed", "need_index", "buyer_max_factor", "buyer_target_factor", "margin_pct", "min_buyer_rating",
              "max_rounds", "news_timeout_s", "news_concurrency", "run"):
        assert getattr(spec, f) == getattr(base, f), f
    from sourceloop.agents.buyer.agent import BuyerPolicy
    from sourceloop.data.clauses import DEFAULT_CLAUSES
    assert c.to_buyer_policy() == BuyerPolicy()
    assert c.to_clause_library() == DEFAULT_CLAUSES
    from sourceloop.llm.prompts_default import DEFAULT_BODIES
    assert c.prompt_bodies() == DEFAULT_BODIES


async def test_happy_scenario_from_default_config_matches_baseline():
    vis, full, _ = generate(42)
    base = await run_to_completion(build_project(ScenarioSpec(), vis, full))
    rt = build_project(default_config().to_scenario_spec(), vis, full)
    st = await run_to_completion(rt)
    assert st.status is PS.DONE and st.outcome is OutcomeEnum.DEAL
    assert (st.awarded, st.shortlist, st.ranking) == (base.awarded, base.shortlist, base.ranking)
    ok, _ = await rt.audit.verify(rt.project_id)
    assert ok


async def test_config_changes_flow_into_run():
    vis, full, _ = generate(42)
    r = raw()
    r["workflow"]["shortlist_size"] = 3
    r["workflow"]["negotiate_top"] = 1
    cfg = StudioConfig.model_validate(r)
    st = await run_to_completion(build_project(cfg.to_scenario_spec(), vis, full))
    assert len(st.shortlist) <= 3 and st.outcome is OutcomeEnum.DEAL
