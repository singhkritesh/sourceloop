"""StudioConfig: the single typed root for everything configurable from the UI (rules 15a-15d).

Design notes
* Strict (extra="forbid") and frozen: a validated config is immutable. Dict fields are FrozenDict, lists are tuples.
* There is deliberately NO field for any invariant (audit chain, signing, mandate enforcement, secrecy, ...); see
  studio/invariants.py. Provider keys never appear here: only `key_ref`, a name resolved by studio.vault.
* `json_schema_extra={"widget": ...}` drives the generic form renderer; `export_json_schema()` is what the UI loads.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sourceloop.agents.buyer.agent import BuyerPolicy
from sourceloop.agents.common import contracts as K
from sourceloop.data.enums import ProviderKindEnum, StageEnum
from sourceloop.llm.config import LlmConfig, ModelRef, ProviderCfg
from sourceloop.orchestrator.runtime import RunConfig

SCHEMA_VERSION = 1

# Agent catalog: lead agents + subagents (names are the ToolRunner keys in agents.common.contracts).
AGENT_KEYS: tuple[str, ...] = (
    "buyer_lead", "supplier_lead", K.RESEARCH, K.COMPLIANCE, K.NEWS, K.WEATHER, K.LANDED, K.QUOTE,
    K.BUYER_VERIFY, K.CAPACITY, K.DEMAND, K.DOCS,
)
# Agents whose absence breaks the run (validator names the dependency); the rest may be disabled.
REQUIRED_AGENTS: frozenset[str] = frozenset(
    {"buyer_lead", "supplier_lead", K.RESEARCH, K.COMPLIANCE, K.LANDED, K.QUOTE, K.BUYER_VERIFY, K.CAPACITY, K.DOCS})
# Agents that call an LLM and therefore own an editable prompt body.
TOOL_AGENTS: frozenset[str] = frozenset(AGENT_KEYS) - {"buyer_lead", "supplier_lead"}
PROMPT_AGENTS: frozenset[str] = frozenset({K.NEWS})
ROLES: tuple[str, ...] = ("fast", "chat")
# Tools each LEAD agent can call today (its ToolRunner contents). `AgentDef.allowed_tools = None` means exactly this set,
# so default behaviour is unchanged. The ToolRunner rejects anything else with NOT_ALLOWED (fail closed, audited).
LEAD_TOOLS: dict[str, tuple[str, ...]] = {
    "buyer_lead": (K.RESEARCH, K.COMPLIANCE, K.NEWS, K.WEATHER, K.LANDED),
    "supplier_lead": (K.DEMAND, K.BUYER_VERIFY, K.CAPACITY, K.QUOTE, K.DOCS),
}
# Tools a lead can never lose: the compliance screen (invariant) plus every REQUIRED_AGENTS tool of that lead (validator).
MANDATORY_LEAD_TOOLS: dict[str, frozenset[str]] = {"buyer_lead": frozenset({K.COMPLIANCE}), "supplier_lead": frozenset()}


class FrozenDict(dict):
    """dict that refuses mutation; keeps JSON/pydantic serialisation working."""

    def _ro(self, *_a: Any, **_k: Any) -> None:
        raise TypeError("published/validated Studio config is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _ro  # type: ignore[assignment]

    def __reduce__(self):
        return (FrozenDict, (dict(self),))

    def __deepcopy__(self, memo: dict) -> FrozenDict:
        from copy import deepcopy
        return FrozenDict({k: deepcopy(v, memo) for k, v in self.items()})

    def __copy__(self) -> FrozenDict:
        return self


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def _freeze(self) -> _Section:
        for name in type(self).model_fields:
            v = self.__dict__.get(name)
            if isinstance(v, dict) and not isinstance(v, FrozenDict):
                object.__setattr__(self, name, FrozenDict(v))
        return self


def W(widget: str, **extra: Any) -> dict[str, Any]:
    return {"widget": widget, **extra}


Ident = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")]


class ProviderDef(_Section):
    kind: ProviderKindEnum = Field(json_schema_extra=W("select"))
    base_url: str = Field("", max_length=300, json_schema_extra=W("url"))
    is_local: bool = Field(False, json_schema_extra=W("switch", admin_only=True,
                                                         help="Allows private/loopback hosts. ADMIN-set; validated as SSRF input."))
    key_ref: str | None = Field(None, max_length=60, json_schema_extra=W("secret_ref",
                                help="Name of the write-only vault entry. The key itself is never part of the config."))


class ModelDef(_Section):
    provider: str = Field(json_schema_extra=W("provider_picker"))
    model: str = Field(min_length=1, max_length=120, json_schema_extra=W("model_picker"))
    temperature: float = Field(0.0, ge=0, le=2, json_schema_extra=W("number"))
    max_tokens: int = Field(1024, ge=16, le=16000, json_schema_extra=W("number"))
    timeout_s: float = Field(20.0, gt=0, le=300, json_schema_extra=W("number"))
    thinking: Literal["auto", "on", "off"] = Field("auto", json_schema_extra=W("select"))

    def to_ref(self) -> ModelRef:
        return ModelRef(provider=self.provider, model=self.model, temperature=self.temperature,
                        max_tokens=self.max_tokens, timeout_s=self.timeout_s, thinking=self.thinking)


class LlmSection(_Section):
    mode: Literal["live", "replay", "record"] = Field("replay", json_schema_extra=W(
        "select", informational=True,
        help="Informational. A project's LLM mode comes from Settings (app_setting.llm_mode), not from this field."))
    roles: dict[str, ModelDef] = Field(default_factory=dict, json_schema_extra=W("role_models", keys=list(ROLES)))
    tokens_per_project: int = Field(400_000, ge=1000, le=10_000_000, json_schema_extra=W("number"))
    replay_fallback_live: bool = Field(False, json_schema_extra=W(
        "switch", informational=True,
        help="Informational. Replay-mode projects use the deterministic stub provider and never go live."))


class AgentDef(_Section):
    enabled: bool = Field(True, json_schema_extra=W("switch"))
    timeout_s: int = Field(20, ge=1, le=120, json_schema_extra=W("number"))
    max_tool_calls: int = Field(12, ge=1, le=50, json_schema_extra=W(
        "number", help="Lead agents only (buyer_lead, supplier_lead): tool calls allowed per turn / handled message."))
    model_override: ModelDef | None = Field(None, json_schema_extra=W("model_picker"))
    allowed_tools: tuple[str, ...] | None = Field(
        None, exclude_if=lambda v: v is None,  # omitted from dumps while unset: content hashes of older versions stay valid
        json_schema_extra=W("tool_multi", help=(
            "Lead agents only: the tools this agent may call. Unset = all of its current tools (unchanged behaviour). Anything "
            "not listed is rejected with NOT_ALLOWED and audited. buyer_lead must keep compliance_screen (invariant). "
            "Applies to projects started after publishing; running projects keep their pinned version.")))
    prompt_ref: str | None = Field(None, json_schema_extra=W(
        "prompt_ref", help="LLM-using agents only: which entry of `prompts` supplies the instruction body. "
                          "Wrapper, preamble and schema suffix stay code-owned."))


class PromptDef(_Section):
    """Instruction BODY only. Preamble, <untrusted_data> wrapper and output schema are code-owned (rule 17)."""
    body: str = Field(min_length=1, max_length=4000, json_schema_extra=W("prompt_body", variables_from="allowed_vars"))


class CheckpointCfg(_Section):
    # Only the requirement confirmation is configurable (off by default: the buyer typed the need). Human checkpoints that remain
    # are code-owned and always the buyer user: compliance flags (only when a supplier is flagged), the award, a changed locked
    # clause (only when one is touched) and the final bank-details + payment release. Technical pass/fail and signature are rule-settled.
    requirement_confirmation: bool = Field(False, json_schema_extra=W("switch"))
    # The awarded supplier's own user confirms the final terms before signature (the supplier-side counterpart of the award).
    supplier_acceptance: bool = Field(False, json_schema_extra=W(
        "switch", help="The awarded supplier's user confirms the final terms before the contract is signed. Off: the supplier agent's own agreement stands."))
    batched_async: bool = Field(False, json_schema_extra=W(
        "switch", help="Requirement confirmation no longer blocks discovery; it must be resolved before the first mandatory checkpoint. "
                       "Mandatory checkpoints are never batched."))


class NegotiationCfg(_Section):
    max_rounds: int = Field(4, ge=1, le=8, json_schema_extra=W("number"))
    min_step_pct: Decimal = Field(Decimal("0.5"), ge=0, le=20, json_schema_extra=W("number"))
    concession: Decimal = Field(Decimal("0.45"), gt=0, le=1, json_schema_extra=W("number"))
    opening_discount: Decimal = Field(Decimal("0.90"), gt=0, le=1, json_schema_extra=W("number"))
    delay_penalty_pct: Decimal = Field(Decimal("20"), ge=0, le=100, json_schema_extra=W("number"))
    llm_moves: bool = Field(True, exclude_if=lambda v: v is True, json_schema_extra=W(
        "switch", help=("In a live run, each agent's next move (accept, counter, walk) is chosen by the 'fast' model from a menu the code builds. "
                        "Every option is already inside the agent's own limits and the conduct judge still reviews each turn. If the model is "
                        "unavailable or unsure, the rule-based move is used and the reason is recorded. Off: rule-based moves only.")))
    judge_llm: bool = Field(False, exclude_if=lambda v: not v, json_schema_extra=W(
        "switch", help=("Every negotiation turn is always judged by code (direction of movement, stalling, lowballing, crossing, giving up early, "
                        "overpaying). Turn this on to also ask the 'fast' model for a second opinion. The model can only add a concern; it can "
                        "never end a negotiation or soften the code verdict.")))


class WorkflowCfg(_Section):
    disabled_stages: tuple[StageEnum, ...] = Field((), json_schema_extra=W("stage_toggles", optional_only=True))
    autonomy: dict[StageEnum, int] = Field(default_factory=dict, json_schema_extra=W(
        "autonomy", min=1, max=4,
        help="Only NEED is honoured: L3+ auto-confirms the requirement (audited). Every other checkpoint is mandatory and human."))
    checkpoints: CheckpointCfg = Field(default_factory=CheckpointCfg)
    negotiation: NegotiationCfg = Field(default_factory=NegotiationCfg)
    longlist_size: int = Field(10, ge=1, le=50, json_schema_extra=W("number"))
    shortlist_size: int = Field(5, ge=1, le=20, json_schema_extra=W("number"))
    negotiate_top: int = Field(2, ge=1, le=10, json_schema_extra=W("number"))
    min_countries: int = Field(2, ge=1, le=10, json_schema_extra=W("number"))
    concurrency: int = Field(8, ge=1, le=32, json_schema_extra=W("number"))
    news_timeout_s: float = Field(20.0, gt=0, le=300, json_schema_extra=W(
        "number", help="Deprecated: agents.regional_news_risk.timeout_s wins; this is only the fallback when that entry is absent."))
    news_escalate_severity: float = Field(0.7, ge=0, le=1, json_schema_extra=W("number"))
    news_min_confidence: float = Field(0.6, ge=0, le=1, json_schema_extra=W("number"))
    standard_clauses: tuple[str, ...] = Field(
        ("payment_terms", "delivery", "warranty", "liability", "termination", "ip"), json_schema_extra=W("clause_multi"))


RuleSignal = Literal["offer_lead_time_weeks", "offer_unit_price_vs_index_pct", "offer_round"]


class RuleDef(_Section):
    """Extra policy rule. Can only ADD a stop (BLOCK/ESCALATE) after invariant rules pass; never allows anything."""
    id: str = Field(pattern=r"^CFG_[A-Z0-9_]{2,40}$")
    description: str = Field("", max_length=200)
    signal: RuleSignal = Field(json_schema_extra=W("select"))
    op: Literal[">", ">=", "<", "<="] = Field(json_schema_extra=W("select"))
    value: Decimal = Field(json_schema_extra=W("number"))
    action: Literal["ESCALATE", "BLOCK"] = Field("ESCALATE", json_schema_extra=W("select"))
    enabled: bool = True


class ClauseDef(_Section):
    title: str = Field(min_length=1, max_length=80)
    negotiable: bool = Field(True, json_schema_extra=W("switch"))
    locked: bool = Field(False, json_schema_extra=W("switch", forced_for=["liability", "termination", "ip"]))


class DataSourcesCfg(_Section):
    synthetic_seed: int = Field(42, ge=0, le=2**31 - 1, json_schema_extra=W("number"))
    news_enabled: bool = Field(True, json_schema_extra=W("switch"))
    weather_enabled: bool = Field(True, json_schema_extra=W("switch"))
    use_recorded_fallback: bool = Field(True, json_schema_extra=W(
        "switch", help="The synthetic/recorded news and weather corpora are the only feeds. Off = no feed: news and weather are "
                       "unavailable and the run fails closed (REVIEW + escalation)."))
    weather_source: Literal["recorded", "open_meteo"] = Field(
        "recorded", exclude_if=lambda v: v == "recorded",  # omitted while default: content hashes of older versions stay valid
        json_schema_extra=W("select", help=(
            "recorded = the built-in climatology table (default; deterministic). open_meteo = a live 16-day severe-weather "
            "forecast blended into the delay probability. Only used in LIVE LLM mode, and only if api.open-meteo.com is in "
            "egress_allowlist; otherwise the tool falls back to the table with low confidence.")))
    profile_source: Literal["none", "simulated"] = Field(
        "none", exclude_if=lambda v: v == "none",  # omitted while default: content hashes of older versions stay valid
        json_schema_extra=W("select", help=(
            "none = supplier records are used as they are (blank lead time / capacity / years / financial risk send the supplier "
            "to human review). simulated = a stand-in for the SBN supplier profile + a credit-risk provider fills ONLY the blanks "
            "from the complete synthetic registry, so a run can finish end to end. Replace with real connectors when available.")))
    egress_allowlist: tuple[str, ...] = Field((), json_schema_extra=W(
        "host_list", admin_only=True,
        help="Hosts the live data connectors may call (e.g. api.open-meteo.com). A connector whose host is not listed here is "
             "unavailable and fails closed. Validated as SSRF input; private hosts need an ADMIN."))


class ScenarioDef(_Section):
    name: str = Field(min_length=1, max_length=80)
    need_index: int = Field(0, ge=0, le=999, json_schema_extra=W("number"))
    buyer_max_factor: Decimal = Field(Decimal("1.45"), gt=0, le=10, json_schema_extra=W("number"))
    buyer_target_factor: Decimal = Field(Decimal("1.0"), gt=0, le=10, json_schema_extra=W("number"))
    margin_pct: Decimal = Field(Decimal("15"), ge=0, le=500, json_schema_extra=W("number"))
    min_buyer_rating: Decimal = Field(Decimal("0.3"), ge=0, le=1, json_schema_extra=W("number"))
    poisoned_news: bool = Field(False, json_schema_extra=W("switch"))
    supplier_behaviours: dict[str, Literal["cooperative", "aggressive", "unreliable", "adversarial"]] = Field(
        default_factory=dict, json_schema_extra=W("behaviour_map"))


class StudioConfig(_Section):
    schema_version: int = SCHEMA_VERSION
    providers: dict[Ident, ProviderDef] = Field(default_factory=dict, json_schema_extra=W("providers"))
    llm: LlmSection = Field(default_factory=LlmSection)
    agents: dict[str, AgentDef] = Field(default_factory=dict, json_schema_extra=W("agents", keys=list(AGENT_KEYS)))
    prompts: dict[str, PromptDef] = Field(default_factory=dict, json_schema_extra=W("prompts"))
    workflow: WorkflowCfg = Field(default_factory=WorkflowCfg)
    rules: tuple[RuleDef, ...] = Field((), json_schema_extra=W("rule_builder"))
    clauses: dict[str, ClauseDef] = Field(default_factory=dict, json_schema_extra=W("clauses"))
    data_sources: DataSourcesCfg = Field(default_factory=DataSourcesCfg)
    scenarios: dict[Ident, ScenarioDef] = Field(default_factory=dict, json_schema_extra=W("scenarios"))

    # ---- derived runtime views (the runtime reads ONLY these, from the pinned version) ----
    def effective_model(self, agent_key: str, role: str) -> ModelDef:
        a = self.agents.get(agent_key)
        if a is not None and a.model_override is not None:
            return a.model_override
        return self.llm.roles[role]

    def to_llm_config(self, has_key: Mapping[str, bool] | None = None) -> LlmConfig:
        provs = {n: ProviderCfg(name=n, kind=p.kind, base_url=p.base_url, is_local=p.is_local,
                                has_key=bool((has_key or {}).get(n, p.key_ref is not None)))
                 for n, p in self.providers.items()}
        overrides = {k: a.model_override.to_ref() for k, a in self.agents.items() if a.model_override is not None}
        return LlmConfig(mode=self.llm.mode, providers=provs, roles={r: m.to_ref() for r, m in self.llm.roles.items()},
                         overrides=overrides, tokens_per_project=self.llm.tokens_per_project,
                         replay_fallback_live=self.llm.replay_fallback_live)

    def to_run_config(self, config_version: int = 1) -> RunConfig:
        w = self.workflow
        return RunConfig(longlist_size=w.longlist_size, shortlist_size=w.shortlist_size, negotiate_top=w.negotiate_top,
                         min_countries=w.min_countries, disabled_stages=frozenset(w.disabled_stages),
                         standard_clauses=tuple(w.standard_clauses), news_escalate_severity=w.news_escalate_severity,
                         news_min_confidence=w.news_min_confidence, config_version=config_version,
                         confirm_requirement=w.checkpoints.requirement_confirmation, supplier_acceptance=w.checkpoints.supplier_acceptance,
                         need_autonomy=int(w.autonomy.get(StageEnum.NEED, 1)), batched_async=w.checkpoints.batched_async,
                         judge_llm=w.negotiation.judge_llm, llm_moves=w.negotiation.llm_moves)

    def to_buyer_policy(self) -> BuyerPolicy:
        n, w = self.workflow.negotiation, self.workflow
        return BuyerPolicy(concession=n.concession, opening_discount=n.opening_discount,
                           delay_penalty_pct=n.delay_penalty_pct, news_escalate_severity=w.news_escalate_severity,
                           news_min_confidence=w.news_min_confidence,
                           max_tool_calls_per_turn=self.agents["buyer_lead"].max_tool_calls
                           if "buyer_lead" in self.agents else BuyerPolicy.max_tool_calls_per_turn)

    def allowed_tools(self, lead: str) -> frozenset[str]:
        """Effective tool allowlist of a lead agent: the configured set, or all of its current tools when unset."""
        a = self.agents.get(lead)
        chosen = a.allowed_tools if a is not None and a.allowed_tools is not None else LEAD_TOOLS[lead]
        return frozenset(chosen)

    def to_clause_library(self) -> dict[str, dict[str, Any]]:
        return {k: {"title": c.title, "negotiable": c.negotiable, "locked": c.locked} for k, c in self.clauses.items()}

    def prompt_bodies(self) -> dict[str, str]:
        """Instruction bodies per LLM-using agent. `agents.<k>.prompt_ref` picks which `prompts` entry feeds agent k."""
        out = {k: p.body for k, p in self.prompts.items()}
        for k in PROMPT_AGENTS:
            ref = self.agents[k].prompt_ref if k in self.agents else None
            if ref is not None and ref in self.prompts:
                out[k] = self.prompts[ref].body
        return out

    def to_extra_rules(self) -> list:
        from sourceloop.studio.rules import compile_rules
        return compile_rules(self.rules)

    def to_scenario_spec(self, scenario_id: str | None = None, config_version: int = 1):
        """ScenarioSpec for the orchestrator test/demo builder. Supplier behaviour profiles are applied by the caller."""
        from sourceloop.orchestrator.scenario import ScenarioSpec
        sc = self.scenarios[scenario_id] if scenario_id else ScenarioDef(name="default")
        disabled = {k for k, a in self.agents.items() if not a.enabled}
        if not self.data_sources.news_enabled or not self.data_sources.use_recorded_fallback:
            disabled.add(K.NEWS)  # no live feed exists: without the recorded corpus the tool is unavailable (fail closed)
        if not self.data_sources.weather_enabled or not self.data_sources.use_recorded_fallback:
            disabled.add(K.WEATHER)
        # precedence: agents.<tool>.timeout_s wins; workflow.news_timeout_s (deprecated) is only the fallback for news
        timeouts = {k: float(a.timeout_s) for k, a in self.agents.items() if k in TOOL_AGENTS}
        news_timeout = timeouts.get(K.NEWS, self.workflow.news_timeout_s)
        return ScenarioSpec(seed=self.data_sources.synthetic_seed, need_index=sc.need_index,
                            clauses=self.to_clause_library(), buyer_policy=self.to_buyer_policy(), prompts=self.prompt_bodies(),
                            min_step_pct=self.workflow.negotiation.min_step_pct, tool_timeouts=timeouts,
                            disabled_tools=frozenset(disabled),
                            buyer_max_factor=sc.buyer_max_factor, buyer_target_factor=sc.buyer_target_factor,
                            min_buyer_rating=sc.min_buyer_rating, margin_pct=sc.margin_pct,
                            run=self.to_run_config(config_version), max_rounds=self.workflow.negotiation.max_rounds,
                            extra_rules=self.to_extra_rules(), news_timeout_s=news_timeout,
                            news_concurrency=self.workflow.concurrency,
                            supplier_max_tool_calls=self.agents["supplier_lead"].max_tool_calls
                            if "supplier_lead" in self.agents else 12,
                            buyer_allowed_tools=self.allowed_tools("buyer_lead"),
                            supplier_allowed_tools=self.allowed_tools("supplier_lead"),
                            weather_source=self.data_sources.weather_source,
                            profile_source=self.data_sources.profile_source,
                            egress_hosts=frozenset(h.strip().lower() for h in self.data_sources.egress_allowlist))


def export_json_schema() -> dict[str, Any]:
    """JSON Schema for the UI (GET /studio/schema). Widget hints ride along as `widget` annotations."""
    js = StudioConfig.model_json_schema()
    js["x-agent-keys"] = list(AGENT_KEYS)
    js["x-required-agents"] = sorted(REQUIRED_AGENTS)
    js["x-prompt-agents"] = sorted(PROMPT_AGENTS)
    return js
