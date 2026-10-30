"""validate(cfg) -> ValidationReport(errors, warnings). Order: schema, invariants, dependencies, SSRF/URL sanity."""
from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from sourceloop.data.enums import ProviderKindEnum, StageEnum
from sourceloop.llm.prompt_builder import FORBIDDEN_VARS, PromptError, validate_body
from sourceloop.llm.prompts_default import ALLOWED_VARS
from sourceloop.studio.invariants import check_invariants
from sourceloop.studio.schema import (
    AGENT_KEYS,
    LEAD_TOOLS,
    PROMPT_AGENTS,
    REQUIRED_AGENTS,
    ROLES,
    AgentDef,
    StudioConfig,
)

_METADATA_HOSTS = {"169.254.169.254", "fd00:ec2::254", "100.100.100.200", "metadata.google.internal"}
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")
_DEF_MAX_CALLS = AgentDef.model_fields["max_tool_calls"].default
_DEF_TIMEOUT = AgentDef.model_fields["timeout_s"].default
_PROMPT_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


@dataclass(frozen=True, slots=True)
class Issue:
    path: str
    code: str
    message: str
    invariant_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "code": self.code, "message": self.message, "invariant_id": self.invariant_id}


@dataclass(frozen=True, slots=True)
class ValidationReport:
    errors: tuple[Issue, ...] = ()
    warnings: tuple[Issue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self) -> set[str]:
        return {i.code for i in self.errors}

    def as_dict(self) -> dict[str, Any]:
        return {"errors": [e.as_dict() for e in self.errors], "warnings": [w.as_dict() for w in self.warnings]}


def classify_host(host: str) -> str:
    """'metadata' | 'private' | 'public' | 'invalid'. Pure (no DNS): names other than localhost and Docker's host alias count as public."""
    h = host.strip().lower().strip("[]")
    if not h:
        return "invalid"
    if h in _METADATA_HOSTS:
        return "metadata"
    if h == "localhost" or h.endswith(".localhost") or h == "host.docker.internal":  # Docker's fixed alias for the machine running the containers
        return "private"
    try:
        ip = ipaddress.ip_address(h.split("%")[0])
    except ValueError:
        return "public" if _HOST_RE.match(h) else "invalid"
    if ip.is_link_local and ip.version == 4 or str(ip) in _METADATA_HOSTS or ip.is_multicast or ip.is_unspecified:
        return "metadata"
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
        return "private"
    return "public"


def validate(cfg: StudioConfig | Mapping[str, Any], *, is_admin: bool = False,
             known_models: Mapping[str, set[str]] | None = None) -> ValidationReport:
    """`is_admin`: private/loopback providers and allowlist hosts are accepted only when an ADMIN publishes.
    `known_models`: optional {provider: installed model names} from discovery; an unlisted model is an error."""
    errors: list[Issue] = []
    warnings: list[Issue] = []
    inv_violations = check_invariants(cfg)  # also covers raw dicts and constructed objects
    for v in inv_violations:
        errors.append(Issue(v.path, "INVARIANT", v.message, v.invariant_id))

    if isinstance(cfg, Mapping):
        try:
            cfg = StudioConfig.model_validate(cfg)
        except ValidationError as e:
            for err in e.errors():
                if err["type"] != "extra_forbidden":  # unknown keys already reported as invariant violations
                    errors.append(Issue(".".join(str(x) for x in err["loc"]), "SCHEMA", err["msg"]))
            return ValidationReport(tuple(errors), tuple(warnings))

    def err(path: str, code: str, msg: str) -> None:
        errors.append(Issue(path, code, msg))

    def warn(path: str, code: str, msg: str) -> None:
        warnings.append(Issue(path, code, msg))

    # ---- providers: URL / SSRF sanity ----
    for name, p in cfg.providers.items():
        path = f"providers.{name}"
        if p.kind is ProviderKindEnum.STUB:
            continue
        if not p.base_url:
            if p.kind is not ProviderKindEnum.ANTHROPIC:
                err(f"{path}.base_url", "URL_REQUIRED", "base_url is required for this provider kind")
            continue
        u = urlparse(p.base_url)
        host = u.hostname or ""
        if u.scheme not in ("http", "https") or not host:
            err(f"{path}.base_url", "URL_INVALID", "base_url must be an http(s) URL with a host")
            continue
        if u.username or u.password:
            err(f"{path}.base_url", "URL_CREDENTIALS", "credentials must not be embedded in the URL")
        kind = classify_host(host)
        if kind in ("metadata", "invalid"):
            err(f"{path}.base_url", "URL_FORBIDDEN_HOST", "host is never reachable (metadata/link-local/invalid)")
        elif kind == "private":
            if not p.is_local:
                err(f"{path}.base_url", "URL_PRIVATE_NOT_LOCAL", "private/loopback host requires the is_local flag")
            elif not is_admin:
                err(f"{path}.is_local", "LOCAL_REQUIRES_ADMIN", "only an ADMIN may mark a private-range provider as local")
        elif p.is_local:
            if not is_admin:
                err(f"{path}.is_local", "LOCAL_REQUIRES_ADMIN", "only an ADMIN may set is_local")
            warn(f"{path}.is_local", "LOCAL_ON_PUBLIC_HOST", "is_local is set but the host looks public")
        if u.scheme == "http" and not p.is_local and kind == "public":
            warn(f"{path}.base_url", "URL_INSECURE", "plain http to a non-local host")
        if p.kind is not ProviderKindEnum.OLLAMA and not p.is_local and p.key_ref is None:
            warn(f"{path}.key_ref", "NO_KEY", "hosted provider without an API key reference")

    # ---- model roles / overrides ----
    for role in ROLES:
        if role not in cfg.llm.roles:
            err(f"llm.roles.{role}", "ROLE_MISSING", f"role {role!r} must be assigned")
    for role, m in cfg.llm.roles.items():
        if role not in ROLES:
            err(f"llm.roles.{role}", "ROLE_UNKNOWN", f"unknown role {role!r}")
        _check_model(f"llm.roles.{role}", m, cfg, known_models, err)
    for key, a in cfg.agents.items():
        path = f"agents.{key}"
        if key not in AGENT_KEYS:
            err(path, "AGENT_UNKNOWN", f"unknown agent {key!r}")
            continue
        if a.model_override is not None:
            _check_model(f"{path}.model_override", a.model_override, cfg, known_models, err)
            if key not in PROMPT_AGENTS and key not in ("buyer_lead", "supplier_lead"):
                warn(f"{path}.model_override", "OVERRIDE_UNUSED", "this agent makes no LLM calls; override has no effect")
        if not a.enabled and key in REQUIRED_AGENTS:
            err(f"{path}.enabled", "AGENT_REQUIRED", f"{key} is required by the run and cannot be disabled")
        if a.prompt_ref is not None:
            if a.prompt_ref not in cfg.prompts:
                err(f"{path}.prompt_ref", "PROMPT_MISSING", f"prompt {a.prompt_ref!r} not defined")
            elif key not in PROMPT_AGENTS:
                warn(f"{path}.prompt_ref", "PROMPT_REF_UNUSED", "this agent makes no LLM calls; prompt_ref has no effect")
            else:  # the referenced body is rendered for THIS agent, so it may only use this agent's variables
                try:
                    validate_body(cfg.prompts[a.prompt_ref].body, ALLOWED_VARS.get(key, frozenset()))
                except PromptError as e:
                    err(f"{path}.prompt_ref", "PROMPT_VAR_NOT_ALLOWED", str(e))
        if a.allowed_tools is not None:
            if key not in LEAD_TOOLS:
                warn(f"{path}.allowed_tools", "ALLOWED_TOOLS_UNUSED", "only lead agents (buyer_lead, supplier_lead) read allowed_tools")
            else:
                for t in a.allowed_tools:
                    if t not in LEAD_TOOLS[key]:
                        warn(f"{path}.allowed_tools", "ALLOWED_TOOL_UNKNOWN", f"{t!r} is not a tool of {key}; it has no effect")
                if len(set(a.allowed_tools)) != len(a.allowed_tools):
                    warn(f"{path}.allowed_tools", "ALLOWED_TOOL_DUPLICATE", "duplicate tool name in allowlist")
                for t in LEAD_TOOLS[key]:
                    if t in REQUIRED_AGENTS and t not in a.allowed_tools and t != "compliance_screen":  # compliance: invariant
                        err(f"{path}.allowed_tools", "ALLOWED_TOOL_REQUIRED", f"{t} is required by the run and cannot be removed")
        if key not in ("buyer_lead", "supplier_lead") and a.max_tool_calls != _DEF_MAX_CALLS:
            warn(f"{path}.max_tool_calls", "MAX_TOOL_CALLS_UNUSED", "only lead agents (buyer_lead, supplier_lead) read max_tool_calls")
        if key in ("buyer_lead", "supplier_lead") and a.timeout_s != _DEF_TIMEOUT:
            warn(f"{path}.timeout_s", "TIMEOUT_UNUSED", "lead agents have no per-call timeout; timeout_s applies to subagent tools")
    if cfg.llm.mode != "replay":
        warn("llm.mode", "LLM_MODE_INFORMATIONAL", "a project's LLM mode comes from Settings, not from the config")
    if cfg.llm.replay_fallback_live:
        warn("llm.replay_fallback_live", "REPLAY_FALLBACK_INFORMATIONAL", "replay-mode projects use the stub provider and never go live")
    if cfg.llm.mode == "live" and all(p.kind is ProviderKindEnum.STUB for p in cfg.providers.values()):
        warn("llm.mode", "LIVE_STUB_ONLY", "live mode with only stub providers")

    # ---- prompts ----
    for key, pr in cfg.prompts.items():
        path = f"prompts.{key}.body"
        if key not in PROMPT_AGENTS:
            err(f"prompts.{key}", "PROMPT_OWNER_UNKNOWN", "no LLM-using agent owns this prompt")
            continue
        for v in _PROMPT_VAR.findall(pr.body):
            if v.lower() in FORBIDDEN_VARS:
                err(path, "PROMPT_FORBIDDEN_VAR", f"variable {v!r} is forbidden")
        try:
            validate_body(pr.body, ALLOWED_VARS.get(key, frozenset()))
        except PromptError as e:
            err(path, "PROMPT_VAR_NOT_ALLOWED", str(e))
        if "{{" in _PROMPT_VAR.sub("", pr.body):
            err(path, "PROMPT_BAD_PLACEHOLDER", "malformed placeholder; use {{variable}}")
    for key in PROMPT_AGENTS:
        if key not in cfg.prompts:
            warn(f"prompts.{key}", "PROMPT_DEFAULT_MISSING", "no prompt body configured for this agent")

    # ---- workflow ----
    w = cfg.workflow
    if w.shortlist_size > w.longlist_size:
        err("workflow.shortlist_size", "SHORTLIST_GT_LONGLIST", "shortlist cannot exceed longlist")
    if w.negotiate_top > w.shortlist_size:
        err("workflow.negotiate_top", "NEGOTIATE_GT_SHORTLIST", "cannot negotiate with more suppliers than shortlisted")
    if w.min_countries > w.shortlist_size:
        err("workflow.min_countries", "COUNTRIES_GT_SHORTLIST", "min_countries cannot exceed shortlist size")
    for k in w.standard_clauses:
        if k not in cfg.clauses:
            err("workflow.standard_clauses", "CLAUSE_UNKNOWN", f"clause {k!r} is not in the library")
    if len(set(w.standard_clauses)) != len(w.standard_clauses):
        err("workflow.standard_clauses", "CLAUSE_DUPLICATE", "duplicate clause in standard contract")
    for k, a in cfg.agents.items():
        if k in ("regional_news_risk",) and not a.enabled and cfg.data_sources.news_enabled:
            warn(f"agents.{k}.enabled", "NEWS_DISABLED_SOURCE_ON", "news source is on but its agent is disabled")
    news_a = cfg.agents.get("regional_news_risk")
    if news_a is not None and float(news_a.timeout_s) != w.news_timeout_s:
        warn("workflow.news_timeout_s", "NEWS_TIMEOUT_SUPERSEDED",
             "deprecated: agents.regional_news_risk.timeout_s takes precedence over workflow.news_timeout_s")
    for stage, lvl in w.autonomy.items():
        if stage is not StageEnum.NEED and lvl > 1:
            warn(f"workflow.autonomy.{stage.value}", "AUTONOMY_NO_EFFECT",
                 "this stage has only mandatory human checkpoints, so its autonomy level changes nothing")
    if w.checkpoints.batched_async and (not w.checkpoints.requirement_confirmation or w.autonomy.get(StageEnum.NEED, 1) >= 3):
        warn("workflow.checkpoints.batched_async", "BATCHED_NO_EFFECT", "requirement confirmation is already skipped")
    elif w.checkpoints.batched_async:
        warn("workflow.checkpoints.batched_async", "BATCHED_OUTREACH_FIRST",
             "discovery and invitations proceed before the requirement is confirmed; mandatory checkpoints stay blocking")
    if w.news_timeout_s < 1:
        err("workflow.news_timeout_s", "TIMEOUT_INSANE", "news timeout below 1 s")

    # ---- rules / clauses / scenarios ----
    ids = [r.id for r in cfg.rules]
    for dup in {i for i in ids if ids.count(i) > 1}:
        err("rules", "RULE_DUPLICATE", f"duplicate rule id {dup}")
    for i, r in enumerate(cfg.rules):
        if r.signal == "offer_round" and not 1 <= r.value <= 8:
            err(f"rules[{i}].value", "RULE_VALUE", "round must be within 1..8")
    for k in cfg.clauses:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", k):
            err(f"clauses.{k}", "CLAUSE_KEY", "clause key must be snake_case")
    for sid, sc in cfg.scenarios.items():
        if sc.buyer_target_factor > sc.buyer_max_factor:
            warn(f"scenarios.{sid}", "TARGET_ABOVE_MAX", "buyer target factor exceeds max factor")

    # ---- data sources: egress allowlist ----
    ds = cfg.data_sources
    for i, h in enumerate(ds.egress_allowlist):
        host = h.strip().lower()
        kind = classify_host(host) if re.fullmatch(r"[a-z0-9.-]+|\[[0-9a-f:]+\]", host) else "invalid"
        path = f"data_sources.egress_allowlist[{i}]"
        if kind in ("invalid", "metadata"):
            err(path, "ALLOWLIST_HOST", "allowlist entries must be bare, public host names")
        elif kind == "private" and not is_admin:
            err(path, "ALLOWLIST_PRIVATE", "private hosts in the allowlist require an ADMIN")
    if ds.weather_source == "open_meteo" and "api.open-meteo.com" not in {h.strip().lower() for h in ds.egress_allowlist}:
        warn("data_sources.weather_source", "WEATHER_HOST_NOT_ALLOWED",
             "open_meteo needs api.open-meteo.com in egress_allowlist; without it the tool falls back to the recorded table with low confidence")
    if (ds.news_enabled or ds.weather_enabled) and not ds.use_recorded_fallback:
        warn("data_sources.use_recorded_fallback", "NO_FALLBACK_NO_EGRESS",
             "with the recorded corpus off, news/weather are unavailable (weather also needs a live source) and the run fails closed")

    return ValidationReport(tuple(errors), tuple(warnings))


def _check_model(path: str, m, cfg: StudioConfig, known: Mapping[str, set[str]] | None, err) -> None:
    if m.provider not in cfg.providers:
        err(f"{path}.provider", "PROVIDER_MISSING", f"provider {m.provider!r} is not defined")
        return
    if known is not None and m.provider in known and m.model not in known[m.provider]:
        err(f"{path}.model", "MODEL_UNAVAILABLE", f"model {m.model!r} is not available on provider {m.provider!r}")
