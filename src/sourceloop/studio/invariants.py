"""Platform invariants as code (rule 15b). They are enforced by the publish validator AND at runtime
(ConfigResolver.get). No StudioConfig field can disable any of them; `check_invariants` additionally rejects configs that
were hand-crafted (raw dict, model_construct, smuggled attributes) to carry such a switch."""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError

from sourceloop.data.enums import ApprovalKindEnum as AK
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.enums import StageEnum as S
from sourceloop.llm.prompt_builder import ENGLISH_PREAMBLE
from sourceloop.orchestrator.approvals import KIND_ROLE
from sourceloop.orchestrator.transitions import OPTIONAL_STAGES
from sourceloop.policy.mandates import LOCKED_CLAUSES_DEFAULT
from sourceloop.studio.schema import PROMPT_AGENTS, StudioConfig

CONTRACTING_AUTONOMY_MAX = 2
WRAPPER_TOKENS = ("<untrusted_data", "</untrusted_data", ENGLISH_PREAMBLE.split(".")[0].strip())
SECRET_KEY_NAMES = re.compile(r"(api[_-]?key|secret|password|passwd|(^|_)(access_|auth_|bearer_)?token$|private[_-]?key)", re.I)


@dataclass(frozen=True, slots=True)
class Violation:
    invariant_id: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class Invariant:
    id: str
    description: str
    reason: str
    check: Callable[[Any, Any], list[Violation]]  # (cfg-or-None, raw dump) -> violations


# A config containing a key matching one of these is trying to switch an invariant off.
_SWITCH_PATTERNS: dict[str, re.Pattern[str]] = {
    "INV_AUDIT_CHAIN": re.compile(r"audit|hash_?chain", re.I),
    "INV_SIGNING": re.compile(r"sign(ing|ature)|schema_?valid", re.I),
    "INV_MANDATE_ENFORCEMENT": re.compile(r"enforce|policy_?engine|skip_?policy|mandate_?check", re.I),
    "INV_MANDATE_SECRECY": re.compile(r"secrecy|reveal|counterparty|expose|share_?mandate", re.I),
    "INV_WATCHLIST_NO_AUTOCLEAR": re.compile(r"watchlist|auto_?clear", re.I),
    "INV_RULES_ONLY_QUAL": re.compile(r"qual_?status|llm_?qual|auto_?qualif", re.I),
    "INV_HOLD_RELEASE_BUYER": re.compile(r"hold_?release|release_?role", re.I),
    "INV_WRAPPER_PRESENT": re.compile(r"wrapper|preamble|untrusted", re.I),
    "INV_FAIL_CLOSED": re.compile(r"fail_?(open|closed)|blank_?(is_)?pass|treat_?missing", re.I),
    "INV_LLM_NEVER_EXECUTED": re.compile(r"auto_?(exec|approve|award|sign)|execute_?llm", re.I),
}


def _field_names(model: type[BaseModel]) -> set[str]:
    return set(model.model_fields)


def _walk_models(obj: Any, path: str = ""):
    """Yield (path, model_instance) for every BaseModel reachable from obj, via its REAL attribute dict."""
    if isinstance(obj, BaseModel):
        yield path, obj
        for name, v in vars(obj).items():
            yield from _walk_models(v, f"{path}.{name}" if path else name)
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            yield from _walk_models(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _walk_models(v, f"{path}[{i}]")


def _structural(cfg: Any, raw: Any) -> list[Violation]:
    """Unknown keys/attributes anywhere (a smuggled switch) and secret-looking keys."""
    out: list[Violation] = []
    if isinstance(cfg, BaseModel):  # model_construct / setattr tricks leave extra entries in the real attribute dict
        for p, m in _walk_models(cfg):
            for e in sorted(set(vars(m)) - _field_names(type(m))):
                out.append(_switch_violation(f"{p}.{e}" if p else e, e))
            out.extend(_switch_violation(f"{p}.{e}", e) for e in (getattr(m, "__pydantic_extra__", None) or {}))
    elif isinstance(raw, Mapping):  # raw dict: let the strict schema name every unknown key
        try:
            StudioConfig.model_validate(raw)
        except ValidationError as e:
            for err in e.errors():
                if err["type"] == "extra_forbidden":
                    loc = ".".join(str(x) for x in err["loc"])
                    out.append(_switch_violation(loc, str(err["loc"][-1])))
    return out


def _switch_violation(path: str, key: str) -> Violation:
    for inv_id, pat in _SWITCH_PATTERNS.items():
        if pat.search(key):
            return Violation(inv_id, path, "no configuration field may alter this invariant")
    if SECRET_KEY_NAMES.search(key):
        return Violation("INV_NO_SECRETS", path, "secrets are never part of a Studio config")
    return Violation("INV_SCHEMA_STRICT", path, "unknown field")


def _get(cfg: Any, raw: Any, *names: str) -> Any:
    cur: Any = raw
    for n in names:
        if isinstance(cur, Mapping) and n in cur:
            cur = cur[n]
        else:
            return None
    return cur


def _inv_structural(cfg: Any, raw: Any) -> list[Violation]:
    return _structural(cfg, raw)


def _inv_locked_clauses(cfg: Any, raw: Any) -> list[Violation]:
    clauses = _get(cfg, raw, "clauses") or {}
    out: list[Violation] = []
    for k in LOCKED_CLAUSES_DEFAULT:
        c = clauses.get(k)
        if c is None:
            out.append(Violation("INV_LOCKED_CLAUSES", f"clauses.{k}", "locked clause missing from library"))
            continue
        if not c.get("locked") or c.get("negotiable"):
            out.append(Violation("INV_LOCKED_CLAUSES", f"clauses.{k}",
                                 "liability/termination/ip must be locked=true and negotiable=false"))
    std = set(_get(cfg, raw, "workflow", "standard_clauses") or ())
    for k in LOCKED_CLAUSES_DEFAULT:
        if k not in std:
            out.append(Violation("INV_LOCKED_CLAUSES", "workflow.standard_clauses",
                                 f"standard contract must carry locked clause {k}"))
    return out


def _inv_compliance_enabled(cfg: Any, raw: Any) -> list[Violation]:
    a = (_get(cfg, raw, "agents") or {}).get("compliance_screen")
    if a is not None and a.get("enabled") is False:
        return [Violation("INV_COMPLIANCE_ENABLED", "agents.compliance_screen.enabled",
                          "the compliance screen cannot be disabled")]
    return []


def _inv_compliance_tool_allowed(cfg: Any, raw: Any) -> list[Violation]:
    """The buyer lead's allowlist (when set) must keep the compliance screen; unset means all tools, which includes it."""
    from sourceloop.agents.common.contracts import COMPLIANCE

    a = (_get(cfg, raw, "agents") or {}).get("buyer_lead")
    allowed = a.get("allowed_tools") if isinstance(a, Mapping) else None
    if allowed is not None and COMPLIANCE not in allowed:
        return [Violation("INV_COMPLIANCE_TOOL_ALLOWED", "agents.buyer_lead.allowed_tools",
                          "the buyer lead's allowlist must contain compliance_screen")]
    return []


def _inv_autonomy(cfg: Any, raw: Any) -> list[Violation]:
    out = []
    for stage, lvl in (_get(cfg, raw, "workflow", "autonomy") or {}).items():
        s = getattr(stage, "value", stage)
        if s == S.CONTRACTING.value and lvl > CONTRACTING_AUTONOMY_MAX:
            out.append(Violation("INV_CONTRACT_AUTONOMY_MAX_L2", f"workflow.autonomy.{s}",
                                 f"contracting autonomy is capped at L{CONTRACTING_AUTONOMY_MAX}"))
        if not 1 <= lvl <= 4:
            out.append(Violation("INV_CONTRACT_AUTONOMY_MAX_L2", f"workflow.autonomy.{s}", "autonomy must be L1-L4"))
    return out


def _inv_stages(cfg: Any, raw: Any) -> list[Violation]:
    bad = [s for s in (_get(cfg, raw, "workflow", "disabled_stages") or ())
           if getattr(s, "value", s) not in {x.value for x in OPTIONAL_STAGES}]
    return [Violation("INV_STAGES_FIXED", "workflow.disabled_stages", f"only optional stages can be disabled: {bad}")] if bad else []


def _inv_human_roles(cfg: Any, raw: Any) -> list[Violation]:
    """Checks the code-owned role map (the config has no say in it). Fails if the runtime mapping was weakened."""
    out = []
    want = {AK.HOLD_RELEASE: R.BUYER, AK.CLAUSE: R.BUYER, AK.COMPLIANCE_FLAG: R.BUYER, AK.AWARD: R.BUYER}
    for kind, role in want.items():
        if KIND_ROLE.get(kind) is not role:
            out.append(Violation("INV_HOLD_RELEASE_BUYER" if kind is AK.HOLD_RELEASE else "INV_HUMAN_CHECKPOINTS",
                                 f"approvals.{kind.value}", f"{kind.value} must be resolved by {role.value}"))
    return out


def _inv_wrapper(cfg: Any, raw: Any) -> list[Violation]:
    out = []
    for key, p in (_get(cfg, raw, "prompts") or {}).items():
        body = p.get("body", "") if isinstance(p, Mapping) else ""
        low = body.lower()
        if any(t.lower() in low for t in WRAPPER_TOKENS):
            out.append(Violation("INV_WRAPPER_PRESENT", f"prompts.{key}.body",
                                 "prompt body may not contain the code-owned wrapper/preamble"))
        if key not in PROMPT_AGENTS:
            out.append(Violation("INV_WRAPPER_PRESENT", f"prompts.{key}", "unknown prompt owner"))
    return out


def _inv_rules_add_only(cfg: Any, raw: Any) -> list[Violation]:
    out = []
    for i, r in enumerate(_get(cfg, raw, "rules") or ()):
        if r.get("action", "ESCALATE") not in ("ESCALATE", "BLOCK") or not str(r.get("id", "")).startswith("CFG_"):
            out.append(Violation("INV_RULES_ADD_ONLY", f"rules[{i}]", "configured rules may only add ESCALATE/BLOCK stops"))
    return out


def _inv_no_secrets(cfg: Any, raw: Any) -> list[Violation]:
    out = []
    for name, p in (_get(cfg, raw, "providers") or {}).items():
        url = p.get("base_url", "") or ""
        if re.search(r"://[^/@]*@", url) or re.search(r"[?&](api[_-]?key|token|key)=", url, re.I):
            out.append(Violation("INV_NO_SECRETS", f"providers.{name}.base_url", "credentials must not be embedded in URLs"))
    return out


def _inv_mandate_bounds(cfg: Any, raw: Any) -> list[Violation]:
    mr = _get(cfg, raw, "workflow", "negotiation", "max_rounds")
    if mr is not None and not 1 <= mr <= 8:
        return [Violation("INV_MANDATE_ENFORCEMENT", "workflow.negotiation.max_rounds", "hard cap of 8 rounds")]
    return []


def _fixed(inv_id: str) -> Callable[[Any, Any], list[Violation]]:
    """Invariants with no configurable surface: violated only by a smuggled switch (reported by structural check)."""
    return lambda cfg, raw: [v for v in _structural(cfg, raw) if v.invariant_id == inv_id]


INVARIANTS: tuple[Invariant, ...] = (
    Invariant("INV_SCHEMA_STRICT", "Config contains only known fields", "Extra keys are how injected switches get through", _inv_structural),
    Invariant("INV_AUDIT_CHAIN", "Audit logging and hash chain are always on", "Without it actions cannot be proven", _fixed("INV_AUDIT_CHAIN")),
    Invariant("INV_SIGNING", "Message signing and schema validation are always on", "Unsigned messages are unattributable", _fixed("INV_SIGNING")),
    Invariant("INV_MANDATE_ENFORCEMENT", "Mandates are enforced by the policy engine; rounds hard-capped at 8", "Prompts can be talked around, code cannot", _inv_mandate_bounds),
    Invariant("INV_MANDATE_SECRECY", "Counterparty mandates are never visible", "Limits leak the floor/ceiling", _fixed("INV_MANDATE_SECRECY")),
    Invariant("INV_WATCHLIST_NO_AUTOCLEAR", "Watchlist hits are never auto-cleared", "Sanctions risk needs a human", _fixed("INV_WATCHLIST_NO_AUTOCLEAR")),
    Invariant("INV_RULES_ONLY_QUAL", "Qualification status is set only by compliance rules", "LLM output must not qualify suppliers", _fixed("INV_RULES_ONLY_QUAL")),
    Invariant("INV_LOCKED_CLAUSES", "liability, termination, ip are locked, non-negotiable and in every contract", "Legal must review them", _inv_locked_clauses),
    Invariant("INV_COMPLIANCE_ENABLED", "Compliance screen cannot be disabled", "Required for sanctions/forced-labor control", _inv_compliance_enabled),
    Invariant("INV_COMPLIANCE_TOOL_ALLOWED", "The compliance screen stays in the buyer lead's tool allowlist", "A removable screen is a bypass of sanctions/forced-labor control", _inv_compliance_tool_allowed),
    Invariant("INV_CONTRACT_AUTONOMY_MAX_L2", "Contracting autonomy <= L2", "Contract acts need human approval", _inv_autonomy),
    Invariant("INV_STAGES_FIXED", "Only optional stages may be disabled", "Stage order and mandatory stages are fixed", _inv_stages),
    Invariant("INV_HOLD_RELEASE_BUYER", "Payment release, the award, compliance flags and locked-clause changes stay with a human buyer user; admins never approve", "Admins set rules and mediate; they never approve", _inv_human_roles),
    Invariant("INV_WRAPPER_PRESENT", "Untrusted-data wrapper, English preamble and schema suffix are code-owned", "A body that replaces the wrapper is an injection tool", _inv_wrapper),
    Invariant("INV_RULES_ADD_ONLY", "Configured rules can only add stops", "Config must not weaken invariant rules", _inv_rules_add_only),
    Invariant("INV_FAIL_CLOSED", "Validation, parse and timeout errors fail closed", "Blank never means pass", _fixed("INV_FAIL_CLOSED")),
    Invariant("INV_LLM_NEVER_EXECUTED", "LLM output never executes an action", "Output trust is mandate bypass", _fixed("INV_LLM_NEVER_EXECUTED")),
    Invariant("INV_NO_SECRETS", "Secrets are never part of a config", "Keys live in the vault only", _inv_no_secrets),
)


def _dump(cfg: Any) -> Any:
    if isinstance(cfg, BaseModel):
        return _plain(cfg)
    return cfg


def _plain(obj: Any) -> Any:
    """Structure from the REAL attribute dict (so model_construct'ed junk is visible), enums to values."""
    if isinstance(obj, BaseModel):
        return {k: _plain(v) for k, v in vars(obj).items()}
    if isinstance(obj, Mapping):
        return {k.value if isinstance(k, Enum) else k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj.value if isinstance(obj, Enum) else obj


def check_invariants(cfg: StudioConfig | Mapping[str, Any]) -> list[Violation]:
    """Runtime-usable. Accepts a StudioConfig (even model_construct'ed) or a raw dict."""
    raw = _dump(cfg)
    model = cfg if isinstance(cfg, BaseModel) else None
    out: list[Violation] = []
    seen: set[tuple[str, str]] = set()
    for inv in INVARIANTS:
        try:
            vs = inv.check(model, raw)
        except Exception as e:  # noqa: BLE001  fail closed: a check that cannot run is a violation
            vs = [Violation(inv.id, "", f"invariant check could not run ({type(e).__name__})")]
        for v in vs:
            if (v.invariant_id, v.path) not in seen:
                seen.add((v.invariant_id, v.path))
                out.append(v)
    if isinstance(cfg, BaseModel):  # a constructed config must also survive full re-validation
        try:
            StudioConfig.model_validate(StudioConfig.model_dump(cfg, mode="json"))
        except (ValidationError, Exception) as e:  # noqa: BLE001
            out.append(Violation("INV_SCHEMA_STRICT", "", f"config does not re-validate ({type(e).__name__})"))
    return out


def invariant_catalog() -> list[dict[str, str]]:
    """Read-only list for GET /studio/invariants."""
    return [{"id": i.id, "description": i.description, "reason": i.reason} for i in INVARIANTS]
