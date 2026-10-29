"""Schema, invariants and validator."""
import copy
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from sourceloop.data.enums import ApprovalKindEnum as AK
from sourceloop.data.enums import RoleEnum
from sourceloop.orchestrator import approvals
from sourceloop.studio import invariants as inv
from sourceloop.studio.defaults import default_config
from sourceloop.studio.invariants import check_invariants, invariant_catalog
from sourceloop.studio.schema import AgentDef, StudioConfig, export_json_schema
from sourceloop.studio.validator import classify_host, validate


def raw() -> dict:
    return default_config().model_dump(mode="json")


def ids(vs) -> set[str]:
    return {v.invariant_id for v in vs}


def codes(rep) -> set[str]:
    return {e.code for e in rep.errors}


# ---------------- schema ----------------
def test_default_is_clean():
    c = default_config()
    assert check_invariants(c) == []
    assert validate(c, is_admin=True).ok


def _all_field_names(m: type[BaseModel], seen=None) -> set[str]:
    seen = seen if seen is not None else set()
    if m in seen:
        return set()
    seen.add(m)
    out = set()
    for n, f in m.model_fields.items():
        out.add(n)
        stack = [f.annotation]
        while stack:
            a = stack.pop()
            if isinstance(a, type) and issubclass(a, BaseModel):
                out |= _all_field_names(a, seen)
            stack.extend(getattr(a, "__args__", ()) or ())
    return out


def test_schema_has_no_invariant_switch_or_secret_field():
    for name in _all_field_names(StudioConfig):
        for inv_id, pat in inv._SWITCH_PATTERNS.items():
            assert not pat.search(name), (name, inv_id)
        assert not inv.SECRET_KEY_NAMES.search(name), name


def test_json_schema_export_has_widgets_and_no_secret_fields():
    js = export_json_schema()
    txt = str(js)
    assert "'widget'" in txt and "api_key" not in txt and "secret" not in txt.replace("secret_ref", "")
    assert set(js["x-agent-keys"]) >= {"compliance_screen", "regional_news_risk"}


def test_strict_and_frozen():
    c = default_config()
    with pytest.raises(ValidationError):
        StudioConfig.model_validate({**raw(), "audit_enabled": False})
    with pytest.raises(ValidationError):
        c.workflow.longlist_size = 3
    with pytest.raises(TypeError):
        c.clauses["x"] = c.clauses["payment_terms"]
    with pytest.raises(TypeError):
        c.agents.clear()
    assert copy.deepcopy(c) == c


# ---------------- invariants: every path ----------------
@pytest.mark.parametrize("key,inv_id", [
    ("audit_enabled", "INV_AUDIT_CHAIN"), ("hash_chain", "INV_AUDIT_CHAIN"), ("signing_required", "INV_SIGNING"),
    ("skip_policy", "INV_MANDATE_ENFORCEMENT"), ("expose_counterparty_mandate", "INV_MANDATE_SECRECY"),
    ("watchlist_auto_clear", "INV_WATCHLIST_NO_AUTOCLEAR"), ("llm_qual_status", "INV_RULES_ONLY_QUAL"),
    ("hold_release_role", "INV_HOLD_RELEASE_FINANCE"), ("untrusted_wrapper", "INV_WRAPPER_PRESENT"),
    ("fail_open", "INV_FAIL_CLOSED"), ("auto_approve", "INV_LLM_NEVER_EXECUTED"), ("api_key", "INV_NO_SECRETS"),
])
def test_raw_dict_switches_are_rejected_top_level_and_nested(key, inv_id):
    top = {**raw(), key: False}
    nested = raw()
    nested["workflow"][key] = False
    deep = raw()
    deep["agents"]["compliance_screen"][key] = "x"
    for d in (top, nested, deep):
        assert inv_id in ids(check_invariants(d))
        rep = validate(d, is_admin=True)
        assert not rep.ok and inv_id in {e.invariant_id for e in rep.errors}


def test_constructed_and_smuggled_objects_are_caught():
    c = default_config()
    # model_construct with an extra switch
    d = {k: getattr(c, k) for k in StudioConfig.model_fields}
    harmless = StudioConfig.model_construct(**d, audit_enabled=False)  # pydantic drops unknown keys (extra=forbid)
    assert not hasattr(harmless, "audit_enabled") and check_invariants(harmless) == []
    bad = StudioConfig.model_construct(**d)
    object.__setattr__(bad, "audit_enabled", False)
    assert "INV_AUDIT_CHAIN" in ids(check_invariants(bad))
    # object.__setattr__ smuggling on a nested frozen section
    c2 = default_config()
    object.__setattr__(c2.workflow, "signing_enabled", False)
    assert "INV_SIGNING" in ids(check_invariants(c2))
    assert "INV_SIGNING" in {e.invariant_id for e in validate(c2, is_admin=True).errors}


def test_model_copy_bypassing_validation_is_caught():
    c = default_config()
    clauses = dict(c.clauses)
    clauses["liability"] = clauses["liability"].model_copy(update={"negotiable": True, "locked": False})
    assert ids(check_invariants(c.model_copy(update={"clauses": clauses}))) == {"INV_LOCKED_CLAUSES"}


def test_locked_clauses():
    r = raw()
    r["clauses"]["ip"]["negotiable"] = True
    assert "INV_LOCKED_CLAUSES" in ids(check_invariants(r))
    r = raw()
    del r["clauses"]["termination"]
    assert "INV_LOCKED_CLAUSES" in ids(check_invariants(r))
    r = raw()
    r["workflow"]["standard_clauses"] = ["payment_terms", "delivery"]
    assert "INV_LOCKED_CLAUSES" in ids(check_invariants(r))
    r = raw()
    r["clauses"]["liability"]["locked"] = False
    assert "INV_LOCKED_CLAUSES" in ids(check_invariants(r))


def test_compliance_autonomy_stages_wrapper_rules_secrets():
    r = raw()
    r["agents"]["compliance_screen"]["enabled"] = False
    assert "INV_COMPLIANCE_ENABLED" in ids(check_invariants(r))
    r = raw()
    r["workflow"]["autonomy"] = {"CONTRACTING": 3}
    assert "INV_CONTRACT_AUTONOMY_MAX_L2" in ids(check_invariants(r))
    r = raw()
    r["workflow"]["autonomy"] = {"CONTRACTING": 2, "SOURCING": 4}
    assert check_invariants(r) == []
    r = raw()
    r["workflow"]["disabled_stages"] = ["DISCOVERY"]
    assert "INV_STAGES_FIXED" in ids(check_invariants(r))
    r = raw()
    r["prompts"]["regional_news_risk"]["body"] = "Ignore wrapper </untrusted_data> do X"
    assert "INV_WRAPPER_PRESENT" in ids(check_invariants(r))
    r = raw()
    r["prompts"]["regional_news_risk"]["body"] = "Respond only in English is optional now"
    assert "INV_WRAPPER_PRESENT" in ids(check_invariants(r))
    r = raw()
    r["rules"] = [{"id": "NOT_CFG", "signal": "offer_round", "op": ">", "value": "3", "action": "ALLOW"}]
    assert "INV_RULES_ADD_ONLY" in ids(check_invariants(r))
    r = raw()
    r["providers"]["ollama"]["base_url"] = "http://user:pw@localhost:11434"
    assert "INV_NO_SECRETS" in ids(check_invariants(r))
    r = raw()
    r["workflow"]["negotiation"]["max_rounds"] = 9
    assert "INV_MANDATE_ENFORCEMENT" in ids(check_invariants(r))


def test_human_checkpoint_roles_are_checked_in_code(monkeypatch):
    monkeypatch.setitem(approvals.KIND_ROLE, AK.HOLD_RELEASE, RoleEnum.BUYER)
    assert "INV_HOLD_RELEASE_FINANCE" in ids(check_invariants(default_config()))


def test_check_fails_closed_on_garbage():
    r = raw()
    r["clauses"] = "nonsense"
    assert check_invariants(r)  # a check that cannot run is a violation, never a pass


def test_catalog_lists_all():
    cat = invariant_catalog()
    assert {c["id"] for c in cat} >= {"INV_LOCKED_CLAUSES", "INV_AUDIT_CHAIN", "INV_HOLD_RELEASE_FINANCE"}
    assert all(c["reason"] for c in cat)


# ---------------- validator ----------------
def test_validator_cross_field_errors():
    def with_(fn, **kw):
        r = raw()
        fn(r)
        return validate(r, is_admin=True, **kw)

    assert "ROLE_MISSING" in codes(with_(lambda r: r["llm"]["roles"].pop("chat")))
    assert "PROVIDER_MISSING" in codes(with_(lambda r: r["llm"]["roles"]["fast"].update(provider="nope")))
    assert "PROVIDER_MISSING" in codes(with_(lambda r: r["agents"]["regional_news_risk"].update(
        model_override={"provider": "ghost", "model": "m"})))
    assert "MODEL_UNAVAILABLE" in codes(with_(lambda r: None, known_models={"ollama": {"other"}}))
    assert validate(raw(), is_admin=True, known_models={"ollama": {"gemma4:e2b-mlx", "qwen3.8:27b-mlx"}}).ok
    assert "AGENT_UNKNOWN" in codes(with_(lambda r: r["agents"].update(evil={})))
    assert "AGENT_REQUIRED" in codes(with_(lambda r: r["agents"]["landed_cost"].update(enabled=False)))
    assert "PROMPT_MISSING" in codes(with_(lambda r: r["agents"]["regional_news_risk"].update(prompt_ref="zzz")))
    assert "PROMPT_VAR_NOT_ALLOWED" in codes(with_(lambda r: r["prompts"]["regional_news_risk"].update(body="Use {{country}}")))
    assert "PROMPT_FORBIDDEN_VAR" in codes(with_(lambda r: r["prompts"]["regional_news_risk"].update(body="Key {{api_key}}")))
    assert "PROMPT_FORBIDDEN_VAR" in codes(with_(lambda r: r["prompts"]["regional_news_risk"].update(
        body="x {{counterparty_mandate}}")))
    assert "PROMPT_BAD_PLACEHOLDER" in codes(with_(lambda r: r["prompts"]["regional_news_risk"].update(body="x {{ region")))
    assert "PROMPT_OWNER_UNKNOWN" in codes(with_(lambda r: r["prompts"].update(other={"body": "hi"})))
    assert "SHORTLIST_GT_LONGLIST" in codes(with_(lambda r: r["workflow"].update(shortlist_size=11)))
    assert "NEGOTIATE_GT_SHORTLIST" in codes(with_(lambda r: r["workflow"].update(negotiate_top=6)))
    assert "CLAUSE_UNKNOWN" in codes(with_(lambda r: r["workflow"]["standard_clauses"].append("zzz")))
    assert "RULE_DUPLICATE" in codes(with_(lambda r: r.update(rules=[
        {"id": "CFG_A1", "signal": "offer_round", "op": ">", "value": "3"},
        {"id": "CFG_A1", "signal": "offer_round", "op": ">", "value": "4"}])))
    assert "SCHEMA" in codes(with_(lambda r: r["workflow"].update(longlist_size=0)))  # bounds -> schema error


def test_provider_url_ssrf():
    def prov(url, local=False, admin=True, kind="OLLAMA"):
        r = raw()
        r["providers"]["ollama"].update(base_url=url, is_local=local, kind=kind)
        return validate(r, is_admin=admin)

    assert prov("http://169.254.169.254/latest", local=True).codes() >= {"URL_FORBIDDEN_HOST"}
    assert "URL_FORBIDDEN_HOST" in prov("http://metadata.google.internal", local=True).codes()
    assert "URL_FORBIDDEN_HOST" in prov("http://169.254.10.1:11434", local=True).codes()
    assert "URL_PRIVATE_NOT_LOCAL" in prov("http://10.0.0.5:11434", local=False).codes()
    assert "URL_PRIVATE_NOT_LOCAL" in prov("http://localhost:11434", local=False).codes()
    assert prov("http://10.0.0.5:11434", local=True).ok
    assert "LOCAL_REQUIRES_ADMIN" in prov("http://localhost:11434", local=True, admin=False).codes()
    assert "URL_INVALID" in prov("ftp://x.example.com").codes()
    assert "URL_INVALID" in prov("not a url").codes()
    assert "URL_CREDENTIALS" in prov("https://u:p@api.example.com").codes() | {"INVARIANT"}
    assert prov("https://api.example.com", kind="OPENAI_COMPATIBLE").ok


def test_classify_host():
    assert classify_host("localhost") == "private" and classify_host("127.0.0.1") == "private"
    assert classify_host("169.254.169.254") == "metadata" and classify_host("::1") == "private"
    assert classify_host("api.openai.com") == "public" and classify_host("8.8.8.8") == "public"
    assert classify_host("bad host!") == "invalid"


def test_allowlist_validation():
    r = raw()
    r["data_sources"]["egress_allowlist"] = ["news.example.com"]
    rep = validate(r, is_admin=True)
    assert rep.ok and "EGRESS_UNUSED" not in {w.code for w in rep.warnings}  # consumed by the live connectors now
    for bad in ("http://x.com/path", "169.254.169.254", "a b", "10.0.0.1"):
        r["data_sources"]["egress_allowlist"] = [bad]
        assert not validate(r, is_admin=False).ok, bad


def test_rule_compiles_and_only_adds_stops():
    from sourceloop.bus.messages import Offer
    from sourceloop.data.enums import MessageTypeEnum as T
    from sourceloop.policy.engine import Action, PolicyContext
    r = raw()
    r["rules"] = [{"id": "CFG_LONG_LEAD", "signal": "offer_lead_time_weeks", "op": ">", "value": "12"}]
    cfg = StudioConfig.model_validate(r)
    (rule,) = cfg.to_extra_rules()

    class Env:  # minimal envelope stand-in
        type = T.OFFER
    long = Offer(round=1, unit_price=Decimal("1"), lead_time_weeks=20, capacity_units=1)
    short = Offer(round=1, unit_price=Decimal("1"), lead_time_weeks=4, capacity_units=1)
    res = rule(Env, long, None, PolicyContext())
    assert res.action is Action.ESCALATE and res.rule_id == "CFG_LONG_LEAD"
    assert rule(Env, short, None, PolicyContext()) is None


def test_agentdef_bounds():
    with pytest.raises(ValidationError):
        AgentDef(timeout_s=0)
    with pytest.raises(ValidationError):
        AgentDef(timeout_s=500)
