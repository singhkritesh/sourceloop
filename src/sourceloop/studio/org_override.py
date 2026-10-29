"""Own-side overrides: what an ORGANIZATION admin may change, layered on the platform configuration.

A buyer organization may tune its own agents (timeouts, tool-call limits, model) and the wording of its own prompts; a supplier organization may
tune its supplier agent's tool-call limit. Nothing here can switch an agent off, change its allowed tools, touch a rule, a clause, a checkpoint,
the workflow or any invariant: those fields do not exist in the override format, and the layered result is run through the SAME publish validator
(invariants included) before it is saved and again whenever a project is built. The platform configuration stays what SBN published."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sourceloop.audit.canonical import dumps, sha256_hex
from sourceloop.studio.schema import LEAD_TOOLS, ModelDef, StudioConfig
from sourceloop.studio.validator import validate

BUYER_AGENTS = frozenset({"buyer_lead", *LEAD_TOOLS["buyer_lead"]})
SUPPLIER_AGENTS = frozenset({"supplier_lead"})  # the supplier tools make no model calls and share one timeout: only the lead's tool-call limit is applied, so only it is offered
LEADS = frozenset({"buyer_lead", "supplier_lead"})


class OverrideInvalid(ValueError):
    def __init__(self, issues: list[dict[str, str]]):
        super().__init__("override rejected")
        self.issues = issues


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentOverride(_Strict):
    timeout_s: int | None = Field(default=None, ge=1, le=120)
    max_tool_calls: int | None = Field(default=None, ge=1, le=50)   # lead agents only
    model_override: ModelDef | None = None


class PromptOverride(_Strict):
    body: str = Field(min_length=1, max_length=4000)                # the instruction BODY only; wrapper and schema stay code-owned


class OrgOverrideSpec(_Strict):
    schema_version: Literal[1] = 1
    agents: dict[str, AgentOverride] = Field(default_factory=dict)
    prompts: dict[str, PromptOverride] = Field(default_factory=dict)


def allowed_agents(kind: str) -> frozenset[str]:
    return BUYER_AGENTS if kind == "BUYER" else SUPPLIER_AGENTS


def parse(payload: Any) -> OrgOverrideSpec:
    try:
        return OrgOverrideSpec.model_validate(payload)
    except ValidationError as e:
        raise OverrideInvalid([{"path": ".".join(str(x) for x in err["loc"]) or "payload", "code": "SHAPE", "message": err["msg"][:200]}
                               for err in e.errors()[:20]]) from None


def content_hash(spec: OrgOverrideSpec) -> str:
    return sha256_hex(dumps(canonical(spec)))


def canonical(spec: OrgOverrideSpec) -> dict[str, Any]:
    """The stored form: only what was actually overridden."""
    return {k: v for k, v in spec.model_dump(mode="json", exclude_none=True).items() if v != {}}


def apply(cfg: StudioConfig, spec: OrgOverrideSpec, kind: str) -> StudioConfig:
    """The effective configuration for a project of this organization. Raises OverrideInvalid for anything outside the whitelist or anything
    the platform validator (invariants included) rejects."""
    issues: list[dict[str, str]] = []
    allowed = allowed_agents(kind)
    agents = dict(cfg.agents)
    for key, ov in spec.agents.items():
        if key not in allowed:
            issues.append({"path": f"agents.{key}", "code": "NOT_YOUR_AGENT", "message": "this agent does not belong to your organization's side"})
            continue
        if key not in agents:
            issues.append({"path": f"agents.{key}", "code": "UNKNOWN_AGENT", "message": "no such agent in the platform configuration"})
            continue
        if ov.max_tool_calls is not None and key not in LEADS:
            issues.append({"path": f"agents.{key}.max_tool_calls", "code": "LEAD_ONLY", "message": "tool-call limits apply to the lead agent only"})
            continue
        upd = {k: v for k, v in (("timeout_s", ov.timeout_s), ("max_tool_calls", ov.max_tool_calls), ("model_override", ov.model_override)) if v is not None}
        agents[key] = agents[key].model_copy(update=upd)
    prompts = dict(cfg.prompts)
    for pid, po in spec.prompts.items():
        if kind != "BUYER":
            issues.append({"path": f"prompts.{pid}", "code": "BUYER_ONLY", "message": "prompts belong to the buyer side"})
        elif pid not in prompts:
            issues.append({"path": f"prompts.{pid}", "code": "UNKNOWN_PROMPT", "message": "no such prompt in the platform configuration"})
        else:
            prompts[pid] = prompts[pid].model_copy(update={"body": po.body})
    if issues:
        raise OverrideInvalid(issues)
    try:
        eff = StudioConfig.model_validate(cfg.model_copy(update={"agents": agents, "prompts": prompts}).model_dump(mode="json"))
    except ValidationError as e:
        raise OverrideInvalid([{"path": ".".join(str(x) for x in err["loc"]), "code": "INVALID", "message": err["msg"][:200]} for err in e.errors()[:20]]) from None
    rep = validate(eff, is_admin=True)  # the SAME validator and invariants SBN's own publish uses. Provider and allowlist fields are SBN-approved and not overridable, so admin-mode is correct here
    if not rep.ok:
        raise OverrideInvalid([{"path": i.path, "code": i.code, "message": i.message[:200]} for i in rep.errors[:20]])
    return eff


def editable(cfg: StudioConfig, kind: str) -> dict[str, Any]:
    """What the UI may offer: the platform's current values for the fields this organization can override."""
    allowed = allowed_agents(kind)
    agents = {k: {"timeout_s": a.timeout_s, "max_tool_calls": a.max_tool_calls if k in LEADS else None, "lead": k in LEADS,
                  "model_override": a.model_override.model_dump(mode="json") if a.model_override else None}
              for k, a in sorted(cfg.agents.items()) if k in allowed}
    prompts = {k: {"body": p.body} for k, p in sorted(cfg.prompts.items())} if kind == "BUYER" else {}
    return {"agents": agents, "prompts": prompts,
            "models": sorted({f"{m.provider}:{m.model}" for m in cfg.llm.roles.values()}), "providers": sorted(cfg.providers)}
