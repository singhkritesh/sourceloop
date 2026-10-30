"""Builds a runnable ProjectRuntime from a scenario spec, plus an automatic human-persona resolver for demos/tests."""
from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sourceloop.agents.buyer.agent import BuyerAgent, BuyerPolicy
from sourceloop.agents.common import contracts as K
from sourceloop.agents.common.tools import ToolRunner
from sourceloop.agents.supplier.agent import SupplierAgent, SupplierProfile
from sourceloop.audit.chain import AuditLog, MemoryAuditStore
from sourceloop.bus.bus import AgentChannel, BusConfig, MessageBus
from sourceloop.bus.signing import KeyRing
from sourceloop.clock import FixedClock
from sourceloop.connectors.simulated import SimulatedProfile
from sourceloop.data.clauses import DEFAULT_CLAUSES
from sourceloop.data.enums import ApprovalKindEnum as AK
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.enums import ProjectStatusEnum as PS
from sourceloop.data.news import NewsFeed, SyntheticNews, stub_extract
from sourceloop.data.registry import Need, Registry
from sourceloop.data.synthetic import TODAY, generate, make_needs
from sourceloop.llm.client import LLMClient, MemoryReplayStore
from sourceloop.llm.config import LlmConfig, stub_config
from sourceloop.llm.providers import StubProvider
from sourceloop.orchestrator.approvals import ApprovalService
from sourceloop.orchestrator.runtime import ProjectRuntime, RunConfig, RunExecutor, SupplierRuntime
from sourceloop.policy.mandates import BuyerMandate, SupplierMandate
from sourceloop.subagents.buyer_tools import build_buyer_tools
from sourceloop.subagents.deliberation import LLMDeliberator
from sourceloop.subagents.supplier_tools import BuyerProfile, build_supplier_tools


class NeedOverrideError(ValueError):
    """The structured need fields given at project creation cannot be honoured (e.g. target above the buyer ceiling)."""


NEED_OVERRIDE_KEYS = frozenset({"category", "industry", "volume_units", "required_certs", "excluded_countries",
                                "max_lead_time_weeks", "target_price"})


def apply_need_overrides(need: Need, overrides: dict[str, Any]) -> tuple[Need, Decimal | None]:
    """Deterministic, no parsing: explicit structured fields replace the synthetic need's. Unknown keys are rejected.
    Returns the need and the optional buyer target price."""
    if not overrides:
        return need, None
    if set(overrides) - NEED_OVERRIDE_KEYS:
        raise NeedOverrideError("unknown need field")
    from sourceloop.data.synthetic import CATEGORIES, INDUSTRY
    ch: dict[str, Any] = {}
    cat = overrides.get("category")
    if cat is not None:
        if cat not in CATEGORIES:
            raise NeedOverrideError("unknown category")
        ch["category"] = cat
        ch["industry"] = INDUSTRY[cat]  # follows the category unless given explicitly below
    if overrides.get("industry") is not None:
        ch["industry"] = overrides["industry"]
    for k in ("volume_units", "max_lead_time_weeks"):
        if overrides.get(k) is not None:
            ch[k] = int(overrides[k])
    for k in ("required_certs", "excluded_countries"):
        if overrides.get(k) is not None:
            ch[k] = tuple(overrides[k])
    target = Decimal(str(overrides["target_price"])) if overrides.get("target_price") is not None else None
    if target is not None:
        ch["target_unit_price"] = target
    return dataclasses.replace(need, **ch), target


class DerivedKeys(dict):
    """Deterministic per-org keys for the prototype (production: KMS)."""

    def __init__(self, secret: bytes) -> None:
        super().__init__()
        self._secret = secret

    def __missing__(self, org: str) -> bytes:
        k = hashlib.sha256(self._secret + org.encode()).digest()
        self[org] = k
        return k


@dataclass
class ScenarioSpec:
    seed: int = 42
    need_index: int = 0
    buyer_max_factor: Decimal = Decimal("1.45")
    buyer_target_factor: Decimal = Decimal("1.0")
    profiles: dict[str, SupplierProfile] = field(default_factory=dict)
    poisoned_news: frozenset[str] = frozenset()
    buyer_profile: BuyerProfile = field(default_factory=BuyerProfile)
    min_buyer_rating: Decimal = Decimal("0.3")
    margin_pct: Decimal = Decimal("15")
    run: RunConfig = field(default_factory=RunConfig)
    llm_cfg: LlmConfig | None = None
    llm_providers: dict | None = None
    news: NewsFeed | None = None
    weather: object | None = None            # live WeatherSource (connectors.base) set by the API in LIVE mode only
    weather_source: str = "recorded"
    supplier_tool_limits: dict = field(default_factory=dict)   # supplier id -> tool-call limit from that supplier ORGANIZATION's override
    org_pins: dict = field(default_factory=dict)               # which org override versions this run was built with (stored on the project)
    profile_source: str = "none"              # none | simulated (fills blank supplier fields from the complete registry)
    extra_watchlist: tuple = ()                # WatchEntry rows from the database (e.g. synced OFAC); merged into the buyer-visible registry
    egress_hosts: frozenset[str] = frozenset()
    max_rounds: int = 4
    extra_rules: list = field(default_factory=list)
    news_timeout_s: float = 20.0
    news_concurrency: int = 8
    # from the pinned Studio config (None / empty => built-in defaults, identical to the previous behaviour)
    clauses: dict | None = None
    buyer_policy: BuyerPolicy | None = None
    prompts: dict[str, str] | None = None
    min_step_pct: Decimal | None = None
    tool_timeouts: dict[str, float] = field(default_factory=dict)
    disabled_tools: frozenset[str] = frozenset()
    supplier_max_tool_calls: int = 12
    # per-lead tool allowlists from the pinned config (None = no restriction beyond the runner's own tools)
    buyer_allowed_tools: frozenset[str] | None = None
    supplier_allowed_tools: frozenset[str] | None = None
    # explicit structured need fields from the API (category, volume_units, required_certs, ..., target_price); {} = synthetic need
    need_overrides: dict = field(default_factory=dict)


def _apply_tool_cfg(runner: ToolRunner, spec: ScenarioSpec, allowed: frozenset[str] | None = None, who: str = "") -> None:
    if allowed is not None:
        if who == "buyer":  # defence in depth next to INV_COMPLIANCE_TOOL_ALLOWED: the screen cannot be removed
            allowed = allowed | {K.COMPLIANCE}
        runner.set_allowed(allowed)
    for name, t in spec.tool_timeouts.items():
        runner.configure(name, timeout_s=t)
    for name in spec.disabled_tools:
        runner.configure(name, enabled=False)


def _denied_audit(audit: AuditLog, project_id: str, agent: str, config_version: int):
    async def on_denied(tool: str) -> None:
        # audit first; the ToolRunner returns NOT_ALLOWED to the caller afterwards
        await audit.append(project_id, actor_type="orchestrator", actor_id="orchestrator", action="tool_denied",
                           rule_id="NOT_ALLOWED", evidence=[{"agent": agent, "tool": tool[:60]}], outcome="denied",
                           config_version=config_version)
    return on_denied


def build_project(spec: ScenarioSpec, vis: Registry | None = None, full: Registry | None = None,
                  project_id: str = "P1", audit: AuditLog | None = None) -> ProjectRuntime:
    if vis is None or full is None:
        vis, full, _ = generate(spec.seed)
    if spec.extra_watchlist:  # DB lists (e.g. synced OFAC) join the synthetic list; screening only ever gets stricter
        vis = Registry(vis.suppliers, [*vis.watchlist, *spec.extra_watchlist], vis.index_price)
    need: Need = make_needs(spec.seed)[spec.need_index]
    need, target_price = apply_need_overrides(need, spec.need_overrides)
    idx = vis.index_price[need.category]
    audit = audit or AuditLog(MemoryAuditStore(), FixedClock())
    ring = KeyRing(DerivedKeys(b"sourceloop-demo-secret"))
    extra_mandate = {} if spec.min_step_pct is None else {"min_step_pct": spec.min_step_pct}
    max_price = (idx * spec.buyer_max_factor).quantize(Decimal("0.0001"))
    target_unit = (idx * spec.buyer_target_factor).quantize(Decimal("0.0001"))
    if target_price is not None:
        target_unit = target_price.quantize(Decimal("0.0001"))
        if target_unit <= 0 or target_unit > max_price:
            raise NeedOverrideError("target_price must be positive and not above the buyer ceiling")
    buyer_mandate = BuyerMandate(max_unit_price=max_price,
                                 target_unit_price=target_unit,
                                 max_lead_time_weeks=need.max_lead_time_weeks, max_rounds=spec.max_rounds,
                                 excluded_countries=need.excluded_countries, **extra_mandate)
    bus = MessageBus(project_id, audit, ring, participants={"buyer": "buyer-org"}, mandates={"buyer": buyer_mandate},
                     config=BusConfig(index_price=idx, price_band_pct=Decimal("60"), require_index_price=True, clause_library=spec.clauses or DEFAULT_CLAUSES,
                                      config_version=spec.run.config_version, extra_rules=spec.extra_rules, quarantine_after=10),
                     clock=audit.clock)
    bus.enable_identity()   # before any AgentChannel exists: every channel is minted a capability for this run only
    ring.keys["buyer-org"]
    ring.keys["orchestrator"]

    llm_cfg = spec.llm_cfg or stub_config()
    providers = spec.llm_providers or {"stub": StubProvider({"supply-continuity risk": lambda u: stub_extract(u)})}
    llm = LLMClient(llm_cfg, providers, MemoryReplayStore())
    news = spec.news or SyntheticNews(spec.poisoned_news)
    btools = ToolRunner(build_buyer_tools(vis, llm, news, project_id, TODAY, prompts=spec.prompts,
                                          news_timeout_s=spec.news_timeout_s, weather=spec.weather, weather_wanted=spec.weather_source == "open_meteo",
                                          profile=SimulatedProfile(full) if spec.profile_source == "simulated" else None), max_concurrency=spec.news_concurrency)
    _apply_tool_cfg(btools, spec, spec.buyer_allowed_tools, "buyer")
    btools.on_denied = _denied_audit(audit, project_id, "buyer", spec.run.config_version)
    # live run + llm_moves: the model chooses each agent's move from the code's menu; anywhere else the rule-based move is used (replay, tests)
    live_model = llm_cfg.mode == "live" and not getattr(providers.get(llm_cfg.effective("negotiation_buyer", "fast").provider), "is_stub", False)
    deliberator = LLMDeliberator(llm, project_id) if (spec.run.llm_moves and live_model) else None
    buyer = BuyerAgent(AgentChannel("buyer", bus), btools, buyer_mandate.max_unit_price, buyer_mandate.target_unit_price,
                       buyer_mandate.max_rounds, buyer_mandate.min_step_pct, spec.buyer_policy or BuyerPolicy(), deliberator=deliberator)

    spec_need = K.NeedSpec(id=need.id, category=need.category, industry=need.industry, volume_units=need.volume_units,
                           required_certs=need.required_certs, excluded_countries=need.excluded_countries,
                           max_lead_time_weeks=need.max_lead_time_weeks, max_financial_risk=need.max_financial_risk,
                           min_years_in_business=need.min_years_in_business)
    brief = {"category": need.category, "industry": need.industry, "volume_units": need.volume_units,
             "required_certificates": list(need.required_certs), "excluded_countries": list(need.excluded_countries),
             "max_lead_time_weeks": need.max_lead_time_weeks, "target_unit_price": str(buyer_mandate.target_unit_price),
             "spec_summary": f"{need.category} for {need.industry}"}
    profiles = {"buyer-org": spec.buyer_profile}

    def make_supplier(sid: str) -> SupplierRuntime:
        rec = full.get(sid)
        assert rec is not None and rec.unit_cost is not None
        prof = spec.profiles.get(sid, SupplierProfile())
        mandate = SupplierMandate(
            unit_cost=rec.unit_cost, min_margin_pct=spec.margin_pct, max_capacity_units=max(rec.capacity_units or 1, 1),
            lead_time_weeks=rec.lead_time_weeks or 8, max_rounds=spec.max_rounds,
            provable_certificates=tuple(c.type for c in rec.certs if c.expires_on >= TODAY),
            min_buyer_rating=spec.min_buyer_rating)
        org = f"sup-{sid}"
        ring.keys[org]
        bus.register(f"supplier:{sid}", org, mandate, rec.country)
        tools = ToolRunner(build_supplier_tools(sid, mandate, profiles))
        _apply_tool_cfg(tools, spec, spec.supplier_allowed_tools, "supplier")
        tools.on_denied = _denied_audit(audit, project_id, f"supplier:{sid}", spec.run.config_version)
        agent = SupplierAgent(sid, AgentChannel(f"supplier:{sid}", bus), tools, mandate.min_unit_price, spec.max_rounds,
                              prof, buyer_org="buyer-org", index_price=idx,
                              max_tool_calls=spec.supplier_tool_limits.get(sid, spec.supplier_max_tool_calls),
                              **({"deliberator": deliberator} if deliberator is not None else {}))
        return SupplierRuntime(sid, rec.name, rec.country, agent, mandate)

    return ProjectRuntime(project_id, spec_need, brief, buyer, buyer_mandate, bus, audit, ApprovalService(audit), llm,
                          spec.run, make_supplier, deliberator=deliberator)


Decision = tuple[AS, dict[str, Any], str]


@dataclass
class Resolver:
    """Scripted human personas for demos/tests. Default: accept every agent recommendation."""
    decisions: dict[AK, Decision] = field(default_factory=dict)
    by_key: dict[str, Decision] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)

    async def resolve_pending(self, rt: ProjectRuntime) -> int:
        n = 0
        for a in list(rt.approvals.pending(rt.project_id)):
            st, edit, comment = self.by_key.get(a.key) or self.decisions.get(a.kind) or (AS.APPROVED, {}, "")
            await rt.approvals.resolve(a.id, st, f"persona-{a.target_role.value.lower()}", a.target_role, comment, edit=edit)
            self.log.append(f"{a.target_role.value}: {a.kind.value}/{a.key} -> {st.value}")
            n += 1
        return n


async def run_to_completion(rt: ProjectRuntime, resolver: Resolver | None = None, max_cycles: int = 30):
    resolver = resolver or Resolver()
    ex = RunExecutor(rt)
    for _ in range(max_cycles):
        await ex.run()
        if rt.state.status is not PS.ESCALATED:
            break
        if await resolver.resolve_pending(rt) == 0:
            break
    return rt.state
