"""ProjectService: creates projects from scenarios, runs them as background tasks and persists everything.

Run safety (TRD section 8): claim in a short transaction (compare-and-set on status), do the long work OUTSIDE any
transaction, one asyncio task per project, bounded concurrency, re-throw after recording a failure. The in-memory
`ProjectRuntime` (agents, bus, approvals) lives in a `RunHandle` while the project is active; the database is the system of
record for what is shown to users, and the audit log is written first (SQL store), the derived rows after.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import time
import types
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.agents.common import par
from sourceloop.agents.supplier.agent import SupplierProfile
from sourceloop.api import security
from sourceloop.api.broker import Broker
from sourceloop.api.config import Settings
from sourceloop.api.deps import Principal
from sourceloop.api.errors import ApiError
from sourceloop.api.studio_support import active_pin, make_ctx, pinned_config
from sourceloop.audit.anchor_sink import FileAnchorSink
from sourceloop.audit.canonical import dumps, sha256_hex
from sourceloop.audit.chain import AuditLog, AuditRecord, verify_chain
from sourceloop.audit.replay import ReplayMismatch, ReplayStore
from sourceloop.clock import Clock, FixedClock, SystemClock
from sourceloop.data import db as dbm
from sourceloop.data.audit_store import SqlAuditStore, append_in_session
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.enums import HoldStatusEnum, OrgKindEnum, OutcomeEnum, QualStatusEnum, RoleEnum
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.enums import ProjectStatusEnum as PS
from sourceloop.data.enums import StageEnum as S
from sourceloop.data.models import (
    Approval,
    AuditEvent,
    Contract,
    LedgerHold,
    Mandate,
    Message,
    Offer,
    Organization,
    OrgOverride,
    Project,
    ProjectSupplier,
    RevokedToken,
    SubagentCall,
    Supplier,
    WatchlistEntry,
)
from sourceloop.data.registry import Registry, SupplierRec, WatchEntry
from sourceloop.data.synthetic import generate
from sourceloop.llm.config import stub_config
from sourceloop.orchestrator.approvals import ApprovalEditError, ApprovalError
from sourceloop.orchestrator.runtime import ProjectRuntime, RunExecutor
from sourceloop.orchestrator.scenario import NeedOverrideError, ScenarioSpec, build_project, run_to_completion
from sourceloop.orchestrator.tamper_guard import TamperSuspected, assert_chain_ok
from sourceloop.orchestrator.transitions import IllegalTransition, check_status
from sourceloop.studio.discovery import ProviderConfigError, build_providers, probe, used_providers
from sourceloop.studio.schema import StudioConfig

log = structlog.get_logger("sourceloop.run")

SCENARIOS = ("happy", "no_deal", "below_floor", "locked_clause", "poisoned_news", "inflated_claims")
SUPPLIER_ORG_NS = uuid.UUID("5a0f2c1e-6b1d-4c3a-9a55-0000000000a1")
STARTABLE = (PS.DRAFT,)
RESUMABLE = (PS.ESCALATED, PS.FAILED_RECOVERABLE, PS.STALLED, PS.BUDGET_EXCEEDED)
TERMINAL = (PS.DONE, PS.STOPPED)  # no transitions out (check_status); their run handle is evicted
NON_TERMINAL = (PS.DRAFT, PS.RUNNING, PS.ESCALATED, PS.FAILED_RECOVERABLE, PS.STALLED, PS.BUDGET_EXCEEDED)
LOST_RUNTIME = "RESTART_LOST_RUNTIME"
NO_PINS: dict[str, Any] = {"buyer": None, "suppliers": {}}  # projects created before org overrides existed ran with none
STOPPED_WHY = "project stopped"  # comment on approvals cancelled by stop()
RUNTIME_GONE_MSG = ("the in-memory run state of this project is gone (the server restarted); the project cannot be resumed - "
                    "create a new project")


def supplier_org_id(sid: str) -> uuid.UUID:
    return uuid.uuid5(SUPPLIER_ORG_NS, sid)


@functools.lru_cache(maxsize=4)
def registry(seed: int) -> tuple[Registry, Registry]:
    vis, full, _key = generate(seed)
    return vis, full


async def build_spec(name: str, need_index: int, seed: int) -> ScenarioSpec:
    """Same scenario recipes as `sourceloop.demo` (some need a baseline run to pick which supplier to perturb)."""
    vis, full = registry(seed)
    spec = ScenarioSpec(seed=seed, need_index=need_index)
    if name == "happy":
        return spec
    if name == "no_deal":
        spec.buyer_max_factor, spec.buyer_target_factor = Decimal("0.5"), Decimal("0.4")
        return spec
    base = await run_to_completion(build_project(ScenarioSpec(seed=seed, need_index=need_index), vis, full))
    if name == "below_floor" and base.shortlist:
        spec.profiles = {base.shortlist[0]: SupplierProfile(attempt_below_floor=True)}
    elif name == "inflated_claims" and len(base.shortlist) > 1:
        spec.profiles = {base.shortlist[1]: SupplierProfile(inflate_claims=True)}
    elif name == "locked_clause" and base.awarded:
        spec.profiles = {base.awarded: SupplierProfile(redline_locked_clause="liability")}
    elif name == "poisoned_news":
        spec.poisoned_news = frozenset(full.get(s).country for s in base.shortlist)  # type: ignore[union-attr]
    return spec


_BEHAVIOUR_PROFILES = {"aggressive": {"stubborn": True}, "unreliable": {"decline_rfq": True}}


def _profile_for(slot: str, behaviour: str) -> SupplierProfile | None:
    """Scenario supplier_behaviours -> SupplierProfile. 'adversarial' attacks by slot: even shortlist slots send an offer
    below their own floor, odd ones inflate certificate claims, 'awarded' redlines the locked liability clause."""
    if behaviour == "adversarial":
        if slot == "awarded":
            return SupplierProfile(redline_locked_clause="liability")
        idx = int(slot.rsplit("_", 1)[1]) if slot.startswith("shortlist_") and slot.rsplit("_", 1)[1].isdigit() else 0
        return SupplierProfile(attempt_below_floor=True) if idx % 2 == 0 else SupplierProfile(inflate_claims=True)
    if behaviour in _BEHAVIOUR_PROFILES:
        return SupplierProfile(**_BEHAVIOUR_PROFILES[behaviour])
    return None


async def build_spec_from_config(cfg: StudioConfig, number: int, scenario: str, need_index: int | None, seed: int,
                                 baseline: Any = None, need_overrides: dict[str, Any] | None = None,
                                 extra_watchlist: tuple = ()) -> ScenarioSpec:
    """ScenarioSpec for a NEW project from the pinned config version (recipes mirror `build_spec`, but every knob -
    list sizes, negotiation, clauses, prompts, rules, scenario factors / behaviours - comes from `cfg`)."""
    vis, full = registry(seed)
    sc = cfg.scenarios[scenario]
    idx = sc.need_index if need_index is None else need_index
    spec = cfg.to_scenario_spec(scenario, number)
    spec.seed, spec.need_index = seed, idx
    spec.need_overrides = dict(need_overrides or {})
    spec.extra_watchlist = extra_watchlist
    if sc.supplier_behaviours or sc.poisoned_news:
        base_spec = cfg.to_scenario_spec(None, number)
        base_spec.seed, base_spec.need_index = seed, idx
        base_spec.need_overrides = dict(need_overrides or {})
        base_spec.extra_watchlist = extra_watchlist
        base = await (baseline(base_spec, vis, full) if baseline else run_to_completion(build_project(base_spec, vis, full)))
        slots = {f"shortlist_{i}": sid for i, sid in enumerate(base.shortlist)}
        if base.awarded:
            slots["awarded"] = base.awarded
        for slot, behaviour in sc.supplier_behaviours.items():
            prof = _profile_for(slot, behaviour)
            if slot in slots and prof is not None:
                spec.profiles[slots[slot]] = prof
        if sc.poisoned_news:
            spec.poisoned_news = frozenset(full.get(sid).country for sid in base.shortlist)  # type: ignore[union-attr]
    return spec


@dataclass
class RunHandle:
    pid: uuid.UUID
    rt: ProjectRuntime
    audit: AuditLog
    scenario: str
    task: asyncio.Task[Any] | None = None
    n_msgs: int = 0
    approval_ids: dict[str, uuid.UUID] = field(default_factory=dict)
    supplier_rows: set[str] = field(default_factory=set)
    pending_calls: list[dict[str, Any]] = field(default_factory=list)
    call_ord: int = 0
    last_seq: int = 0
    synced_seq: int = 0
    last_state: tuple[Any, ...] = ()
    rounds: dict[str, int] = field(default_factory=dict)
    contract_done: bool = False
    hold_done: bool = False
    stopping: bool = False
    config_version: int = 1
    sequential: bool = False  # replay-mode runs execute their fan-outs one after another (see agents/common/par.py)
    release: list[Any] = field(default_factory=list)  # async callables run when a live run segment ends (free VRAM)
    sync_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    resolve_lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # one approval resolution at a time per project
    needs_resync: bool = False  # a derived-row sync failed: memory is ahead of the DB until the next successful sync
    resolved_rows: set[str] = field(default_factory=set)  # core ids whose resolved state is confirmed in the approval table
    touched: float = field(default_factory=time.monotonic)  # last activity (idle-DRAFT eviction)

    def hook(self, agent: str, supplier_id: str | None = None):
        async def _hook(kind: str, data: dict[str, Any]) -> None:
            if kind != "subagent_result":
                return
            extra = {"supplier_id": supplier_id} if supplier_id else {}  # which supplier's agent made the call (scopes the admin view)
            self.pending_calls.append({"agent": agent, "audit_seq": self.last_seq, **extra, **data})
        return _hook


class ProjectService:
    def __init__(self, settings: Settings, broker: Broker, clock: Clock | None = None) -> None:
        self.settings = settings
        self.broker = broker
        self.clock: Clock = clock or SystemClock()  # wall clock for row timestamps (not decision code)
        self.store = SqlAuditStore()
        self.anchors: FileAnchorSink | None = FileAnchorSink(settings.audit_anchor_path) if settings.audit_anchor_path else None
        self.handles: dict[str, RunHandle] = {}
        self._sem = asyncio.Semaphore(settings.max_concurrent_runs)
        self._mandate_lock = asyncio.Lock()
        self._baseline_sem = asyncio.Semaphore(max(1, settings.max_baseline_runs))
        self._baselines: dict[tuple[Any, ...], Any] = {}
        self._rebuild_lock = asyncio.Lock()
        self._offline_locks: dict[str, asyncio.Lock] = {}  # audit writers for projects without a live handle
        self.studio_lock = asyncio.Lock()  # serialises writers of the 'studio' audit stream / config_version numbers
        self.last_recovery: dict[str, int] = {"restored": 0, "lost": 0}

    # ------------------------------------------------------------------ settings (DB first, env fallback)
    async def effective_settings(self, db: AsyncSession) -> dict[str, Any]:
        row = await dbm.get_setting(db)
        return {"llm_mode": (row.llm_mode if row and row.llm_mode else self.settings.llm_mode),
                "token_budget_per_project": (row.token_budget_per_project if row and row.token_budget_per_project
                                             else self.settings.token_budget_per_project),
                "kill_switch": bool(row and row.kill_switch)}

    # ------------------------------------------------------------------ create
    def _evict_idle_drafts(self) -> int:
        """Drop the runtime of DRAFT projects nobody started within the TTL. The row stays; `start` rebuilds the runtime from
        the pinned config + stored scenario/seed (a draft has no run state)."""
        ttl, now, n = self.settings.draft_ttl_minutes * 60, time.monotonic(), 0
        for pid, h in list(self.handles.items()):
            if h.rt.state.status is PS.DRAFT and not self.active(pid) and now - h.touched > ttl:
                self.handles.pop(pid, None)
                n += 1
        return n

    def _check_capacity(self) -> None:
        if len(self.handles) >= self.settings.max_live_handles:
            self._evict_idle_drafts()
        if len(self.handles) >= self.settings.max_live_handles:
            raise ApiError(503, "CAPACITY_EXCEEDED", "the server is at its limit of live runs; retry later", {"Retry-After": "30"})

    async def _read_need(self, db: AsyncSession, cfg: StudioConfig, number: int, scenario: str, pid: str, need_text: str) -> dict[str, Any]:
        """Live mode: the fast model turns the typed need into structured fields. The model is unloaded as soon as it has answered."""
        from sourceloop.llm.client import LLMClient
        from sourceloop.subagents.need_intake import NeedUnclear, read_need

        spec = cfg.to_scenario_spec(scenario, number)
        release: list[Any] = []
        await self._live_llm(db, cfg, spec, release)
        try:
            return await read_need(LLMClient(spec.llm_cfg, spec.llm_providers or {}), pid, need_text)  # type: ignore[arg-type]
        except NeedUnclear as e:
            raise ApiError(422, "NEED_UNCLEAR", e.message) from None
        finally:
            await self._release_models(types.SimpleNamespace(release=release))  # type: ignore[arg-type]

    async def _build_runtime(self, db: AsyncSession, cfg: StudioConfig, number: int, *, mode: str, scenario: str,
                             need_index: int | None, seed: int, need_fields: dict[str, Any], token_cap: int | None,
                             pid: uuid.UUID, buyer_org_id: uuid.UUID, org_pins: dict[str, Any] | None = None,
                             audit_store: Any = None) -> tuple[ScenarioSpec, ProjectRuntime, AuditLog, list[Any], int]:
        wl_rows, wl_sig = await self._db_watchlist(db, seed)
        cfg, sup_limits, pins = await self._org_layers(db, cfg, buyer_org_id, org_pins)  # the platform config + this org's own-side overrides
        spec = await build_spec_from_config(cfg, number, scenario, need_index, seed, need_overrides=need_fields, extra_watchlist=wl_rows,
                                            baseline=functools.partial(self._baseline, (number, scenario, need_index, seed,
                                                                                         dumps(need_fields), wl_sig, dumps(pins))))
        spec.supplier_tool_limits, spec.org_pins = sup_limits, pins
        release: list[Any] = []
        if mode == "live":
            await self._live_llm(db, cfg, spec, release)
            self._live_weather(spec)
        # R2-7: the effective token_budget_per_project setting can only LOWER the pinned config's cap
        base = spec.llm_cfg or stub_config()
        cap = min(cfg.llm.tokens_per_project, token_cap) if token_cap else cfg.llm.tokens_per_project
        spec.llm_cfg = base.model_copy(update={"tokens_per_project": max(1000, min(base.tokens_per_project, cap))})
        vis, full = registry(seed)
        clock: Clock = FixedClock() if mode == "replay" else SystemClock()  # replay => deterministic chain
        audit = AuditLog(audit_store or self.store, clock)
        try:
            rt = build_project(spec, vis, full, project_id=str(pid), audit=audit)
        except NeedOverrideError:
            raise ApiError(422, "INVALID_NEED", "the structured need fields are inconsistent (e.g. target_price above the ceiling)") from None
        return spec, rt, audit, release, spec.llm_cfg.tokens_per_project

    async def create_project(self, db: AsyncSession, p: Principal, *, title: str, need_text: str, scenario: str,
                             need_index: int | None, seed: int, project_id: uuid.UUID | None = None,
                             need_fields: dict[str, Any] | None = None) -> Project:
        await self._check_quota(db, p)
        self._check_capacity()
        eff = await self.effective_settings(db)
        mode = eff["llm_mode"]
        if mode not in ("replay", "live"):
            raise ApiError(409, "LLM_MODE_UNSUPPORTED", "only 'replay' and 'live' runs are supported")
        # ---- pin: the ACTIVE version at creation time; from here on the run reads ONLY this version (rule 15a)
        pin = await active_pin(db, self.settings, self.studio_lock)
        cfg = pin.cfg
        if scenario not in cfg.scenarios:
            raise ApiError(422, "UNKNOWN_SCENARIO", "scenario is not defined in the active configuration")
        pid = project_id or uuid.uuid4()
        fields = need_fields or {}
        need_source = "structured" if fields else "sample"
        if mode == "live" and not fields and need_text.strip():  # a real run: the model reads what the buyer typed
            fields, need_source = await self._read_need(db, cfg, pin.number, scenario, str(pid), need_text), "typed_text_read_by_model"
        spec, rt, audit, release, cap = await self._build_runtime(
            db, cfg, pin.number, mode=mode, scenario=scenario, need_index=need_index, seed=seed, need_fields=fields,
            token_cap=int(eff["token_budget_per_project"]), pid=pid, buyer_org_id=p.org_id)
        now = self.clock.now()
        mandate_payload = json.loads(dumps(rt.buyer_mandate))
        async with self._mandate_lock:
            await self._check_quota(db, p)  # authoritative re-check under the lock (the early one is only a cheap fast-fail)
            version = (await db.execute(select(func.coalesce(func.max(Mandate.version), 0))
                                        .where(Mandate.org_id == p.org_id))).scalar_one() + 1
            mandate = Mandate(org_id=p.org_id, side="buyer", version=version, payload=mandate_payload,
                              signature=security.sign_payload(self.settings, p.org_id, mandate_payload), created_at=now)
            db.add(mandate)
            await db.flush()
            proj = Project(id=pid, buyer_org_id=p.org_id, title=title, need_text=need_text,
                           brief={**rt.brief, "scenario": scenario, "seed": seed, "need_index": spec.need_index,
                                  "config_version": pin.number, "token_cap": cap, "need_fields": json.loads(dumps(fields)), "need_source": need_source,
                                  "watchlist_sig": self._wl_sig(spec.extra_watchlist), "org_overrides": spec.org_pins},
                           stage=S.NEED, status=PS.DRAFT, buyer_mandate_id=mandate.id, llm_mode=mode,
                           config_version_id=pin.row_id, created_by=p.id, created_at=now, updated_at=now)
            db.add(proj)
            await db.commit()
        h = RunHandle(pid, rt, audit, scenario, config_version=pin.number, release=release, sequential=(mode == "replay"))
        self._attach(h)
        return proj

    async def _rebuild_draft(self, pid: uuid.UUID) -> RunHandle:
        """Recreate the runtime of a DRAFT whose handle was evicted. Deterministic from the pinned config + the stored
        scenario/seed/need fields; if anything does not line up (e.g. the stored mandate differs) it fails closed."""
        async with self._rebuild_lock:
            h = self.handles.get(str(pid))
            if h is not None:
                return h
            self._check_capacity()
            async with dbm.get_sessionmaker()() as db:
                proj = await db.get(Project, pid)
                if proj is None or proj.status is not PS.DRAFT:
                    raise ApiError(409, "RUNTIME_UNAVAILABLE", RUNTIME_GONE_MSG)
                b = proj.brief if isinstance(proj.brief, dict) else {}
                scenario, seed = b.get("scenario"), b.get("seed")
                if not isinstance(scenario, str) or not isinstance(seed, int) or proj.config_version_id is None:
                    raise ApiError(409, "DRAFT_EXPIRED", "this draft's runtime expired and cannot be rebuilt; create a new project")
                pin = await pinned_config(db, self.settings, proj.config_version_id)
                mandate = await db.get(Mandate, proj.buyer_mandate_id)
                try:
                    _spec, rt, audit, release, _cap = await self._build_runtime(
                        db, pin.cfg, pin.number, mode=proj.llm_mode or "replay", scenario=scenario,
                        need_index=b.get("need_index"), seed=seed, need_fields=dict(b.get("need_fields") or {}),
                        token_cap=b.get("token_cap"), pid=pid, buyer_org_id=proj.buyer_org_id, org_pins=b.get("org_overrides") or NO_PINS)
                except ApiError as e:
                    if e.code == "INVALID_NEED":
                        raise ApiError(409, "DRAFT_EXPIRED", "this draft's runtime expired and cannot be rebuilt; create a new project") from None
                    raise
                if mandate is None or json.loads(dumps(rt.buyer_mandate)) != mandate.payload:
                    raise ApiError(409, "DRAFT_EXPIRED", "this draft's runtime expired and cannot be rebuilt; create a new project")
            h = RunHandle(pid, rt, audit, scenario, config_version=pin.number, release=release,
                          sequential=((proj.llm_mode or "replay") == "replay"))
            self._attach(h)
            return h

    async def _check_quota(self, db: AsyncSession, p: Principal) -> None:
        """Per-org cap on non-terminal projects: bounds run handles / memory per tenant."""
        n = (await db.execute(select(func.count()).select_from(Project)
                              .where(Project.buyer_org_id == p.org_id, Project.status.in_(NON_TERMINAL)))).scalar_one()
        if n >= self.settings.max_active_projects_per_org:
            raise ApiError(429, "QUOTA_EXCEEDED", "too many active projects for this organisation; finish or stop some first",
                           {"Retry-After": "60"})

    async def _baseline(self, key: tuple[Any, ...], base_spec: ScenarioSpec, vis: Registry, full: Registry) -> Any:
        """Baseline run for behaviour scenarios (picks which suppliers to perturb). It is deterministic per (config version,
        scenario, need, seed), so it is cached; concurrent baselines are bounded and each has a timeout, so a burst of
        create requests cannot pin the event loop. (It still runs on the request path: the spec must exist before the
        runtime is built.)"""
        hit = self._baselines.get(key)
        if hit is not None:
            return hit
        async with self._baseline_sem:
            try:
                res = await asyncio.wait_for(run_to_completion(build_project(base_spec, vis, full)),
                                             self.settings.baseline_timeout_s)
            except TimeoutError:
                raise ApiError(503, "BASELINE_TIMEOUT", "scenario preparation timed out; retry later") from None
            except NeedOverrideError:
                raise ApiError(422, "INVALID_NEED", "the structured need fields are inconsistent (e.g. target_price above the ceiling)") from None
        slim = SimpleNamespace(shortlist=list(res.shortlist), awarded=res.awarded)  # do not cache the whole runtime
        if len(self._baselines) >= 64:
            self._baselines.pop(next(iter(self._baselines)))
        self._baselines[key] = slim
        return slim

    async def _live_llm(self, db: AsyncSession, cfg: StudioConfig, spec: ScenarioSpec, release: list[Any]) -> None:
        """Live mode: the PINNED config's providers/roles/overrides, all HTTP through SafeHttp. 409 unless every provider the
        config uses is reachable (Ollama: /api/tags, and the configured models installed)."""
        ctx = await make_ctx(db, self.settings)
        for name in sorted(used_providers(cfg)):
            d = cfg.providers.get(name)
            if d is None:
                raise ApiError(409, "LLM_MODE_UNSUPPORTED", "the pinned configuration references an undefined provider")
            res = await probe(name, d, ctx.vault)
            if not res["ok"]:
                raise ApiError(409, "LLM_PROVIDER_UNREACHABLE", f"provider '{name}' is not reachable ({res['error']})")
            if d.kind.value == "OLLAMA":
                wanted = {m.model for m in cfg.llm.roles.values() if m.provider == name}
                wanted |= {a.model_override.model for a in cfg.agents.values() if a.model_override and a.model_override.provider == name}
                missing = sorted(wanted - set(res["models"]))
                if missing:
                    raise ApiError(409, "MODEL_NOT_FOUND", f"model(s) not installed on provider '{name}'")
        try:
            providers = build_providers(cfg, ctx.vault)
        except (ProviderConfigError, Exception) as e:  # noqa: BLE001 - codes only
            raise ApiError(409, "LLM_MODE_UNSUPPORTED", f"provider configuration rejected ({getattr(e, 'code', 'ERROR')})") from None
        spec.llm_cfg = cfg.to_llm_config({n: ctx.vault.has_key(n) for n in cfg.providers}).model_copy(update={"mode": "live"})
        spec.llm_providers = providers
        for name, prov in providers.items():
            if hasattr(prov, "release"):
                models = {m.model for m in cfg.llm.roles.values() if m.provider == name}
                models |= {a.model_override.model for a in cfg.agents.values() if a.model_override and a.model_override.provider == name}
                release.extend(functools.partial(prov.release, m) for m in sorted(models))

    async def _org_layers(self, db: AsyncSession, cfg: StudioConfig, buyer_org_id: uuid.UUID,
                          pins: dict[str, Any] | None) -> tuple[StudioConfig, dict[str, int], dict[str, Any]]:
        """Layer the organizations' own-side overrides on the platform config. `pins=None` resolves the ACTIVE versions now (a new project);
        a given `pins` replays exactly those immutable versions (a rebuild / restore). Anything that no longer validates fails closed."""
        from sourceloop.studio import org_override as OO

        async def load(org: uuid.UUID, version: int) -> Any:
            row = (await db.execute(select(OrgOverride).where(OrgOverride.org_id == org, OrgOverride.version == version))).scalar_one_or_none()
            if row is None:
                raise ApiError(409, "ORG_OVERRIDE_MISSING", "a recorded organization setting version no longer exists")
            return OO.parse(row.payload)

        try:
            if pins is None:
                bo = (await db.execute(select(Organization.override_version).where(Organization.id == buyer_org_id))).scalar_one_or_none()
                rows = (await db.execute(select(Supplier.id, Organization.id, Organization.override_version).join(Organization, Organization.id == Supplier.org_id)
                                         .where(Organization.override_version.is_not(None)).order_by(Supplier.id))).all()
                pins = {"buyer": {"org": str(buyer_org_id), "version": bo} if bo else None,
                        "suppliers": {sid: {"org": str(oid), "version": v} for sid, oid, v in rows}}
            eff, limits = cfg, {}
            if pins.get("buyer"):
                eff = OO.apply(cfg, await load(uuid.UUID(pins["buyer"]["org"]), int(pins["buyer"]["version"])), "BUYER")
            for sid, ref in sorted((pins.get("suppliers") or {}).items()):
                sup_eff = OO.apply(cfg, await load(uuid.UUID(ref["org"]), int(ref["version"])), "SUPPLIER")
                limits[sid] = sup_eff.agents["supplier_lead"].max_tool_calls
        except OO.OverrideInvalid:
            raise ApiError(409, "ORG_OVERRIDE_INVALID", "an organization setting no longer passes the platform checks; its admin must fix it") from None
        return eff, limits, pins

    @staticmethod
    async def _db_watchlist(db: AsyncSession, seed: int) -> tuple[tuple[WatchEntry, ...], str]:
        """Watchlist lists that live only in the database (e.g. a synced OFAC_SDN) join the run's screening input. Lists the synthetic
        registry already carries are skipped (seed stores them too). Returns (entries, signature) - the signature keys the baseline cache."""
        own = {w.list_name for w in registry(seed)[0].watchlist}
        rows = (await db.execute(select(WatchlistEntry).where(WatchlistEntry.list_name.not_in(own)).order_by(WatchlistEntry.id))).scalars().all()
        ents = tuple(WatchEntry(f"DB:{r.id}", r.name, r.country, r.list_name, aliases=tuple(str(a) for a in (r.aliases or ()))) for r in rows)
        return ents, ProjectService._wl_sig(ents)

    @staticmethod
    def _wl_sig(ents: tuple) -> str:
        """Fingerprint of the DB watchlist rows a run screened against ('' = none). Kept in the project brief so a restore can tell
        that the screening input changed since the run started."""
        return hashlib.sha256("\n".join(f"{e.list_name}|{e.name}" for e in ents).encode()).hexdigest()[:16] if ents else ""

    @staticmethod
    def _live_weather(spec: ScenarioSpec) -> None:
        """LIVE mode + Studio weather_source=open_meteo + host allowed by the pinned egress_allowlist => live forecast; otherwise the
        tool falls back (low confidence). Recorded/replay runs never touch the network."""
        from sourceloop.connectors.open_meteo import OpenMeteoWeather
        from sourceloop.net.safe_http import SafeHttp

        host = "api.open-meteo.com"
        if spec.weather_source == "open_meteo" and host in spec.egress_hosts:
            spec.weather = OpenMeteoWeather(SafeHttp({host}, timeout_s=8.0))

    def _wire_hooks(self, h: RunHandle) -> None:
        rt = h.rt
        rt.buyer.tools._hook = h.hook("buyer")  # additive: ToolRunner already supports a hook
        orig = rt.make_supplier

        def make(sid: str):
            sr = orig(sid)
            sr.agent.tools._hook = h.hook("supplier", sid)
            return sr

        rt.make_supplier = make

    def _subscribe(self, h: RunHandle) -> None:
        async def on_audit(rec: AuditRecord) -> None:
            h.last_seq = max(h.last_seq, rec.seq)
            await self.sync(h)  # a failure is logged by the audit log (project, seq, error class); h.needs_resync retries it
            self.broker.notify(str(h.pid))

        h.audit.subscribe(on_audit)
        self.handles[str(h.pid)] = h

    def _attach(self, h: RunHandle) -> None:
        self._wire_hooks(h)
        self._subscribe(h)

    # ------------------------------------------------------------------ persistence (derived rows)
    async def _ensure_supplier(self, s: AsyncSession, sid: str, seed: int = 42) -> Supplier:
        sup = await s.get(Supplier, sid)
        if sup is None:
            vis, full = registry(seed)
            rec: SupplierRec = vis.get(sid) or full.get(sid)  # type: ignore[assignment]
            sup = Supplier(id=sid, name=rec.name, name_norm=rec.name.lower(), aliases=list(rec.aliases), country=rec.country,
                           tax_id=rec.tax_id, categories=list(rec.categories), capacity_units=rec.capacity_units,
                           lead_time_weeks=rec.lead_time_weeks,
                           financial_risk=None if rec.financial_risk is None else Decimal(str(rec.financial_risk)),
                           years_in_business=rec.years_in_business, forced_labor_flag=rec.forced_labor_flag,
                           subtier_origin=list(rec.subtier_origin), network_status=rec.network_status)
            s.add(sup)
        if sup.org_id is None:
            org_id = supplier_org_id(sid)
            if await s.get(Organization, org_id) is None:
                s.add(Organization(id=org_id, name=sup.name, kind=OrgKindEnum.SUPPLIER, created_at=self.clock.now()))
                await s.flush()
            sup.org_id = org_id
        await s.flush()
        return sup

    async def sync(self, h: RunHandle) -> None:
        """Write derived rows (messages, offers, approvals, subagent calls, supplier mandates, contract, hold, project).

        All handle counters advance only AFTER the transaction committed (they are computed in locals first), so a failed
        commit leaves memory and DB consistent and the next sync simply redoes the same work."""
        async with h.sync_lock:
            try:
                await self._sync_locked(h)
            except BaseException:
                h.needs_resync = True
                raise
            h.needs_resync = False

    async def _sync_with_retry(self, h: RunHandle, attempts: int = 3) -> None:
        for i in range(attempts):
            try:
                await self.sync(h)
                return
            except Exception as e:  # noqa: BLE001 - logged; the handle stays needs_resync and the next sync converges
                log.error("sync.failed", project_id=str(h.pid), seq=h.last_seq, attempt=i + 1, error=type(e).__name__)
                await asyncio.sleep(0.05 * (i + 1))

    async def _sync_locked(self, h: RunHandle) -> None:
        rt, st, now = h.rt, h.rt.state, self.clock.now()
        seed = int(rt.brief.get("seed", 42)) if isinstance(rt.brief, dict) else 42
        target_seq = h.last_seq
        new_suppliers: set[str] = set()
        rounds = dict(h.rounds)
        added_approvals: dict[str, uuid.UUID] = {}
        newly_resolved: set[str] = set()
        calls = list(h.pending_calls)  # removed from the handle only after the commit
        call_ord = h.call_ord
        contract_done, hold_done = h.contract_done, h.hold_done
        new = list(rt.bus.log[h.n_msgs:])
        async with dbm.session_scope() as s:
            for sid, sr in list(rt.suppliers.items()):
                if sid in h.supplier_rows:
                    continue
                sup = await self._ensure_supplier(s, sid, seed)
                assert sup.org_id is not None
                payload = json.loads(dumps(sr.mandate))
                async with self._mandate_lock:
                    ver = (await s.execute(select(func.coalesce(func.max(Mandate.version), 0))
                                           .where(Mandate.org_id == sup.org_id))).scalar_one() + 1
                    m = Mandate(org_id=sup.org_id, side="supplier", version=ver, payload=payload,
                                signature=security.sign_payload(self.settings, sup.org_id, payload), created_at=now)
                    s.add(m)
                    await s.flush()
                s.add(ProjectSupplier(project_id=h.pid, supplier_id=sid, supplier_mandate_id=m.id, qual_reasons=[]))
                new_suppliers.add(sid)
            for env in new:
                s.add(Message(id=env.id, project_id=h.pid, seq=env.seq, stage=env.stage, type=env.type,
                              from_agent=env.from_agent, to_agent=env.to_agent, in_reply_to=env.in_reply_to,
                              payload=json.loads(dumps(env.payload)), signature=env.signature, ts=env.ts))
            if new:
                await s.flush()
            for env in new:
                if env.type in (T.OFFER, T.COUNTER):
                    sup_side = env.from_agent.startswith("supplier:")
                    sid = (env.from_agent if sup_side else env.to_agent).split(":", 1)[1]
                    s.add(Offer(project_id=h.pid, supplier_id=sid, round=int(env.payload["round"]),
                                side="supplier" if sup_side else "buyer", unit_price=Decimal(str(env.payload["unit_price"])),
                                lead_time_weeks=int(env.payload["lead_time_weeks"]),
                                terms={"capacity_units": env.payload.get("capacity_units"), "note": env.payload.get("note", "")},
                                message_id=env.id))
                    rounds[sid] = max(rounds.get(sid, 0), int(env.payload["round"]))
            mem_approvals = list(rt.approvals.all(str(h.pid)))
            for a in mem_approvals:
                if a.id not in h.approval_ids:
                    row = Approval(project_id=h.pid, core_id=a.id, key=a.key, kind=a.kind, stage=a.stage,
                                   target_role=a.target_role, status=a.status, reasons=list(a.reasons),
                                   evidence=json.loads(dumps(a.evidence)), recommendation=json.loads(dumps(a.recommendation)),
                                   version=a.version, created_at=now)
                    s.add(row)
                    added_approvals[a.id] = row.id
                    if a.status is not AS.PENDING:
                        newly_resolved.add(a.id)
            await s.flush()
            for c in calls:
                call_ord += 1
                llm = [x for x in (c.get("llm") or []) if isinstance(x, dict)]  # provenance only; never in hash path
                s.add(SubagentCall(project_id=h.pid, ord=call_ord, audit_seq=c["audit_seq"], agent=c["agent"], tool=c["tool"],
                                   input_summary="", output={"ok": c.get("ok"), "error": c.get("error"), "sources": c.get("sources", 0),
                                           **({"llm": llm} if llm else {}), **({"supplier_id": c["supplier_id"]} if c.get("supplier_id") else {})},
                                   model_id=(f"{llm[0]['provider']}:{llm[0]['model']}"[:80] if llm else None),
                                   tokens=sum(int(x.get("tokens", 0)) for x in llm),
                                   confidence=Decimal(str(round(float(c.get("confidence", 0)), 4))),
                                   latency_ms=int(c.get("latency_ms", 0)), created_at=now))
            if st.contract and not contract_done and st.awarded:
                s.add(Contract(project_id=h.pid, supplier_id=st.awarded, clauses=list(st.contract["clauses"]),
                               deviations=list(st.contract["deviations"]), status="DRAFT"))
                contract_done = True
            if st.contract and st.contract.get("signature_hash") and st.awarded:
                await s.flush()
                await s.execute(update(Contract).where(Contract.project_id == h.pid)
                                .values(status="SIGNED", signature_hash=st.contract["signature_hash"]))
            if st.hold == "HELD" and not hold_done and st.awarded:
                s.add(LedgerHold(project_id=h.pid, supplier_id=st.awarded, status=HoldStatusEnum.HELD))
                hold_done = True
            for a in mem_approvals:  # converge: resolved in memory (audit written) but the row still says PENDING
                if a.status is not AS.PENDING and a.id not in h.resolved_rows and a.id not in newly_resolved:
                    await self._apply_resolution(s, h.pid, a.id, a.kind, a.status, a.resolver_id or "", a.comment, a.edit,
                                                 a.version, now, by_core=True)
                    newly_resolved.add(a.id)
            for sid, q in st.qual.items():
                await s.execute(update(ProjectSupplier).where(ProjectSupplier.project_id == h.pid, ProjectSupplier.supplier_id == sid)
                                .values(qual_status=QualStatusEnum(q["status"]), qual_reasons=list(q["reasons"]),
                                        round_count=rounds.get(sid, 0)))
            tokens = int(rt.llm.used.get(str(h.pid), 0))
            state = (st.stage, st.status, st.outcome, st.reason, tokens)
            wrote_state = state != h.last_state and not h.stopping
            if wrote_state:
                await s.execute(update(Project).where(Project.id == h.pid).values(
                    stage=st.stage, status=st.status, outcome=st.outcome, reason=st.reason, tokens_used=tokens,
                    version=Project.version + 1, updated_at=now))
        # ---- committed: only now does the in-memory bookkeeping advance
        h.supplier_rows |= new_suppliers
        h.n_msgs += len(new)
        h.approval_ids.update(added_approvals)
        h.resolved_rows |= newly_resolved
        del h.pending_calls[:len(calls)]  # calls appended meanwhile stay queued
        h.call_ord = call_ord
        h.rounds = rounds
        h.contract_done, h.hold_done = contract_done, hold_done
        if wrote_state:
            h.last_state = state
        h.synced_seq = max(h.synced_seq, target_seq)

    async def _apply_resolution(self, s: AsyncSession, pid: uuid.UUID, ref: uuid.UUID | str, kind: Any, status: AS,
                                resolver: str, comment: str, edit: dict[str, Any], new_version: int, now: Any, *,
                                by_core: bool = False, expect_version: int | None = None) -> bool:
        """Inside the caller's transaction: mark the approval resolved (compare-and-set on PENDING) and, for an approved
        HOLD_RELEASE, release the ledger hold - one unit. Returns whether this call flipped the row."""
        cond = [Approval.project_id == pid, Approval.status == AS.PENDING]
        cond.append(Approval.core_id == ref if by_core else Approval.id == ref)
        if expect_version is not None:
            cond.append(Approval.version == expect_version)
        res = await s.execute(update(Approval).where(*cond).values(
            status=status, resolver_id=resolver, comment=comment, edit=edit, resolved_at=now, version=new_version))
        flipped = res.rowcount == 1
        if flipped and kind.value == "HOLD_RELEASE" and status is AS.APPROVED:
            await s.execute(update(LedgerHold).where(LedgerHold.project_id == pid, LedgerHold.status == HoldStatusEnum.HELD)
                            .values(status=HoldStatusEnum.RELEASED, released_by=resolver, released_at=now,
                                    version=LedgerHold.version + 1))
        return flipped

    # ------------------------------------------------------------------ run lifecycle
    def active(self, pid: uuid.UUID | str) -> bool:
        h = self.handles.get(str(pid))
        return bool(h and h.task and not h.task.done())

    async def claim(self, pid: uuid.UUID, allowed: tuple[PS, ...]) -> bool:
        """Short transaction, compare-and-set on status; the long work happens after the commit."""
        async with dbm.session_scope() as s:
            res = await s.execute(update(Project).where(Project.id == pid, Project.status.in_(allowed))
                                  .values(status=PS.RUNNING, version=Project.version + 1, updated_at=self.clock.now()))
            return res.rowcount == 1

    async def _anchor(self, pid: str) -> None:
        """Publish the chain head to the external anchor file. Best effort by design: a sink problem is logged loudly, never fails a run."""
        if self.anchors is None:
            return
        try:
            async with dbm.get_sessionmaker()() as s:
                row = (await s.execute(select(AuditEvent.seq, AuditEvent.hash).where(AuditEvent.project_id == pid)
                                       .order_by(AuditEvent.seq.desc()).limit(1))).first()
            if row is not None:
                self.anchors.publish(pid, int(row[0]), row[1], self.clock.now().isoformat())
        except Exception as e:  # noqa: BLE001
            log.error("anchor.failed", project_id=pid, error=type(e).__name__)

    async def start(self, pid: uuid.UUID, allowed: tuple[PS, ...] = STARTABLE) -> None:
        async with dbm.get_sessionmaker()() as s:
            if (await self.effective_settings(s))["kill_switch"]:
                raise ApiError(503, "KILL_SWITCH", "runs are paused platform-wide by an operator")
        h = self.handles.get(str(pid))
        if h is None:
            if allowed != STARTABLE:
                raise ApiError(409, "RUNTIME_UNAVAILABLE", RUNTIME_GONE_MSG)
            h = await self._rebuild_draft(pid)  # idle DRAFT evicted by TTL: no run state existed, rebuild from the pin
        h.touched = time.monotonic()
        if self.active(pid):
            raise ApiError(409, "INVALID_STATE", "project cannot be started/resumed from its current status")
        try:  # a tampered chain blocks the run BEFORE it is claimed, so the project never enters RUNNING
            await assert_chain_ok(dbm.get_sessionmaker(), str(pid), audit=h.audit)
        except TamperSuspected:
            raise ApiError(409, "TAMPER_SUSPECTED", "the audit chain of this project failed verification; the run is blocked") from None
        if not await self.claim(pid, allowed):
            raise ApiError(409, "INVALID_STATE", "project cannot be started/resumed from its current status")
        h.stopping = False
        h.last_state = ()
        h.task = asyncio.create_task(self._drive(h), name=f"run-{pid}")
        h.task.add_done_callback(self._done)

    @staticmethod
    def _done(t: asyncio.Task[Any]) -> None:
        if not t.cancelled() and t.exception() is not None:
            log.error("run.failed", task=t.get_name(), error=type(t.exception()).__name__)  # recorded + re-raised in _drive

    async def _drive(self, h: RunHandle) -> None:
        pid = str(h.pid)
        par.set_sequential(h.sequential)  # context of this task only (and the tasks it spawns)
        async with self._sem:
            ex = RunExecutor(h.rt)
            try:
                for _ in range(20):
                    await ex.run()
                    if h.rt.state.status is PS.ESCALATED and not h.rt.approvals.pending(pid):
                        continue  # resolved while the executor was pausing: go again
                    break
            except Exception:
                # the executor already audited `run_error` and set FAILED_RECOVERABLE; persist and re-throw
                h.last_state = ()
                await self._sync_with_retry(h)
                self.broker.notify(pid)
                raise
            finally:
                await self._release_models(h)  # FIRST and always: completed, failed, paused for a human, stopped or cancelled - the model is not working now
                if not h.stopping or h.needs_resync:
                    h.last_state = ()
                    await self._sync_with_retry(h)
                    self.broker.notify(pid)
                await self._anchor(pid)
                if not h.stopping:
                    self._evict_if_terminal(h)

    @staticmethod
    async def _release_models(h: RunHandle) -> None:
        """Unload every model this run used (direct keep_alive:0). Shielded so a cancelled run still unloads; never raises."""
        async def go() -> None:
            for fn in h.release:
                try:
                    await fn()
                except Exception as e:  # noqa: BLE001 - best effort; Ollama's own 30 s keep-alive is the backstop
                    log.warning("llm.release_failed", error=type(e).__name__)
        if h.release:
            await asyncio.shield(go())

    def _evict_if_terminal(self, h: RunHandle) -> None:
        """Drop the in-memory runtime of a finished project (everything shown to users is already in the database), so
        `handles` is bounded by the active projects, not by every project since boot."""
        st = h.rt.state.status
        if st is PS.DONE and h.rt.approvals.pending(str(h.pid)):
            return  # e.g. the finance HOLD_RELEASE checkpoint is still open after DONE: keep the runtime until it is resolved
        if st in TERMINAL and self.handles.get(str(h.pid)) is h:
            self.handles.pop(str(h.pid), None)
            self.broker.notify(str(h.pid))

    async def join(self, pid: uuid.UUID | str) -> None:
        """Wait for the current run segment (tests / graceful shutdown)."""
        h = self.handles.get(str(pid))
        if h and h.task:
            await asyncio.gather(h.task, return_exceptions=True)

    async def resume(self, pid: uuid.UUID) -> None:
        await self.start(pid, RESUMABLE)

    async def stop(self, pid: uuid.UUID, p: Principal) -> None:
        """Audit first, then mutate (rule 10): `run_stopped` is appended before the status changes anywhere."""
        h = self.handles.get(str(pid))
        if h is None:  # evicted (finished), never attached (after a restart) or lost: DB is the only truth
            async with dbm.get_sessionmaker()() as s:
                proj = await s.get(Project, pid)
            if proj is None or proj.status in TERMINAL:
                raise ApiError(409, "INVALID_STATE", "project already finished")
            await AuditLog(self.store, self.clock).append(
                str(pid), actor_type="human", actor_id=str(p.id), action="run_stopped", rule_id="stopped_by_user",
                outcome=OutcomeEnum.STOPPED_BY_USER.value, approver_id=str(p.id))
            async with dbm.session_scope() as s:
                res = await s.execute(update(Project).where(Project.id == pid, Project.status.notin_(TERMINAL)).values(
                    status=PS.STOPPED, outcome=OutcomeEnum.STOPPED_BY_USER, reason="stopped_by_user",
                    version=Project.version + 1, updated_at=self.clock.now()))
                if res.rowcount != 1:
                    raise ApiError(409, "INVALID_STATE", "project already finished")
                await self._cancel_pending_approvals(s, pid, STOPPED_WHY)
            self.broker.notify(str(pid))
            return
        h.stopping = True
        if h.task and not h.task.done():
            h.task.cancel()
            await asyncio.gather(h.task, return_exceptions=True)
        st = h.rt.state
        try:
            check_status(st.status, PS.STOPPED)
        except IllegalTransition:
            h.stopping = False
            raise ApiError(409, "INVALID_STATE", "project already finished") from None
        try:
            await h.audit.append(str(pid), actor_type="human", actor_id=str(p.id), action="run_stopped",
                                 rule_id="stopped_by_user", outcome=OutcomeEnum.STOPPED_BY_USER.value, approver_id=str(p.id))
        except Exception:
            h.stopping = False  # nothing was mutated: the project is exactly as before
            raise
        st.status, st.outcome, st.reason = PS.STOPPED, OutcomeEnum.STOPPED_BY_USER, "stopped_by_user"
        h.stopping = False
        h.last_state = ()
        await self.sync(h)
        async with dbm.session_scope() as s:  # a stopped project has no one to act on its checkpoints
            await self._cancel_pending_approvals(s, pid, STOPPED_WHY)
        self.broker.notify(str(pid))
        self._evict_if_terminal(h)

    async def _cancel_pending_approvals(self, s: AsyncSession, pid: uuid.UUID, why: str) -> int:
        """Close dead inbox items: PENDING approvals of a project that can no longer be driven become REJECTED."""
        res = await s.execute(update(Approval).where(Approval.project_id == pid, Approval.status == AS.PENDING).values(
            status=AS.REJECTED, comment=why[:1000], resolved_at=self.clock.now(), version=Approval.version + 1))
        return res.rowcount or 0

    # ------------------------------------------------------------------ approvals
    @staticmethod
    def _closed_error(row: Approval) -> ApiError:
        if row.status is AS.REJECTED and row.comment.startswith(LOST_RUNTIME):
            return ApiError(409, "RUNTIME_UNAVAILABLE", RUNTIME_GONE_MSG)
        if row.status is AS.REJECTED and row.comment.startswith(STOPPED_WHY):
            return ApiError(409, "INVALID_STATE", "the project was stopped; this approval was cancelled and can no longer be resolved")
        return ApiError(409, "VERSION_CONFLICT", "approval already resolved or version mismatch")

    async def resolve_approval(self, db: AsyncSession, p: Principal, row: Approval, status: AS, comment: str,
                               version: int, edit: dict[str, Any]) -> Approval:
        # the target role decides; the only exception is SBN's SUPER_ADMIN on an operational (ADMIN-targeted) item
        acting = row.target_role if (p.role is RoleEnum.SUPER_ADMIN and row.target_role is RoleEnum.ADMIN) else p.role
        if acting is not row.target_role:
            raise ApiError(403, "FORBIDDEN", "role not permitted to resolve this approval")
        if row.status is not AS.PENDING:
            raise self._closed_error(row)
        if version != row.version:
            raise ApiError(409, "VERSION_CONFLICT", "approval already resolved or version mismatch")
        if status is AS.PENDING:
            raise ApiError(422, "INVALID_STATUS", "status must be APPROVED, REJECTED or EDITED")
        if status is not AS.APPROVED and len(comment.strip()) < 10:
            raise ApiError(422, "COMMENT_REQUIRED", "a comment of at least 10 characters is required")
        aid, core_id, pid_, kind = row.id, row.core_id, row.project_id, row.kind
        h = self.handles.get(str(pid_))
        if h is None:
            return await self._resolve_without_runtime(db, p, row, status, comment, version)
        async with h.resolve_lock:  # one resolution at a time per project: the memory check below cannot interleave
            # 1. fresh DB state: a stale/duplicate resolver gets 409 and the in-memory orchestrator is never touched
            cur = (await db.execute(select(Approval.status, Approval.version).where(Approval.id == aid))).first()
            await db.rollback()  # end the read transaction before the audit store writes through its own connection
            if cur is None or cur[0] is not AS.PENDING or cur[1] != version:
                raise ApiError(409, "VERSION_CONFLICT", "approval already resolved or version mismatch")
            # 2. memory resolve: writes the approval_resolved audit event BEFORE any state (memory or DB) changes (rule 10).
            #    A failure here leaves the DB row untouched (PENDING), so there is nothing to compensate.
            try:
                await h.rt.approvals.resolve(core_id, status, str(p.id), acting, comment, version=version, edit=edit)
            except ApprovalError as e:
                code = {"APPROVAL_ALREADY_RESOLVED": 409, "FORBIDDEN": 403, "COMMENT_REQUIRED": 422}.get(e.code, 422)
                raise ApiError(code, "VERSION_CONFLICT" if code == 409 else e.code) from e
            except ApprovalEditError as e:
                raise ApiError(422, "INVALID_EDIT", "edit payload was rejected by the orchestrator") from e
            # 3. DB: approval CAS + ledger-hold release in ONE transaction (retried; sync converges it if it keeps failing)
            try:
                await self._persist_resolution(h, aid, core_id, pid_, kind, status, str(p.id), comment, edit, version)
            except Exception:
                h.needs_resync = True
                self.broker.notify(str(pid_))
                raise ApiError(503, "RESOLUTION_PERSIST_PENDING",
                               "the decision is recorded in the audit log but the database write failed; it will be "
                               "reconciled - retry later or resume the project", {"Retry-After": "5"}) from None
        h.synced_seq = max(h.synced_seq, h.last_seq)
        self.broker.notify(str(pid_))
        # resume after resolution if the project is parked at a checkpoint and nothing is running
        if h.rt.state.status is PS.ESCALATED and not self.active(pid_):
            try:
                await assert_chain_ok(dbm.get_sessionmaker(), str(pid_), audit=h.audit)
                ok = True
            except TamperSuspected:
                ok = False  # stays parked; the block is audited
            if ok and await self.claim(pid_, (PS.ESCALATED,)):
                h.stopping = False
                h.last_state = ()
                h.task = asyncio.create_task(self._drive(h), name=f"run-{pid_}")
                h.task.add_done_callback(self._done)
        if not self.active(pid_):
            self._evict_if_terminal(h)
        await db.refresh(row)
        return row

    async def _persist_resolution(self, h: RunHandle, aid: uuid.UUID, core_id: str, pid: uuid.UUID, kind: Any, status: AS,
                                  resolver: str, comment: str, edit: dict[str, Any], version: int) -> None:
        last: Exception | None = None
        for attempt in range(3):
            try:
                async with dbm.session_scope() as s:
                    flipped = await self._apply_resolution(s, pid, aid, kind, status, resolver, comment, edit, version + 1,
                                                           self.clock.now(), expect_version=version)
                    if not flipped:  # a concurrent sync may already have converged this exact decision
                        cur = (await s.execute(select(Approval.status, Approval.resolver_id).where(Approval.id == aid))).first()
                        if cur is None or cur[0] is not status or cur[1] != resolver:
                            raise RuntimeError("approval row diverged")
                h.resolved_rows.add(core_id)
                return
            except Exception as e:  # noqa: BLE001
                last = e
                log.error("approval.persist_failed", project_id=str(pid), approval=core_id, attempt=attempt + 1,
                          error=type(e).__name__)
                await asyncio.sleep(0.05 * (attempt + 1))
        assert last is not None
        raise last

    async def _resolve_without_runtime(self, db: AsyncSession, p: Principal, row: Approval, status: AS, comment: str,
                                       version: int) -> Approval:
        """No in-memory runtime (restart / eviction). The only DB-only checkpoint is HOLD_RELEASE on a DONE project: CAS the
        approval, release the ledger hold and append the audit event in ONE transaction (audit committed with, never
        after, the change it proves). Everything else needs the lost runtime and answers 409 with an accurate reason."""
        pid_, aid, core_id, kind = row.project_id, row.id, row.core_id, row.kind
        proj = await db.get(Project, pid_)
        pstatus, preason = (proj.status, proj.reason) if proj else (None, None)
        await db.rollback()
        if pstatus is PS.DONE and kind.value == "HOLD_RELEASE":
            if status is AS.EDITED:
                raise ApiError(422, "INVALID_EDIT", "this approval kind does not accept an edit payload")
            lock = self._offline_locks.setdefault(str(pid_), asyncio.Lock())
            async with lock:
                now = self.clock.now()
                async with dbm.session_scope() as s:
                    flipped = await self._apply_resolution(s, pid_, aid, kind, status, str(p.id), comment, {}, version + 1, now,
                                                           expect_version=version)
                    if not flipped:
                        raise ApiError(409, "VERSION_CONFLICT", "approval already resolved or version mismatch")
                    await append_in_session(
                        s, str(pid_), now, actor_type="human", actor_id=str(p.id), action="approval_resolved",
                        rule_id=kind.value, outcome=status.value, approver_id=str(p.id),
                        evidence=[{"approval": core_id, "decision": status.value, "edit_keys": [],
                                   "decision_hash": sha256_hex(dumps({"edit": {}, "comment": comment}))}])
            self._offline_locks.pop(str(pid_), None)
            self.broker.notify(str(pid_))
            await db.refresh(row)
            return row
        if pstatus in TERMINAL and preason != LOST_RUNTIME:
            raise ApiError(409, "INVALID_STATE", "the project is finished; this approval can no longer be resolved")
        raise ApiError(409, "RUNTIME_UNAVAILABLE", RUNTIME_GONE_MSG)

    # ------------------------------------------------------------------ restart recovery by deterministic replay
    async def _restore(self, pid: uuid.UUID) -> tuple[RunHandle | None, str]:
        """Rebuild the runtime of an unfinished REPLAY-mode project after a restart. Replay mode is deterministic (fixed clock, recorded
        answers), so the run is re-executed from genesis from its pinned config, feeding in the human decisions stored in the database, and
        every regenerated audit record must hash to exactly the stored one. If it does, the run is exactly where it was (or at its next
        human checkpoint when the crash was mid-run) and is resumable; otherwise nothing is changed and the caller closes it as lost.
        Returns (handle, "") or (None, reason code). Live-mode runs cannot be reproduced and are never restored."""
        sid = str(pid)
        async with dbm.get_sessionmaker()() as db:
            proj = await db.get(Project, pid)
            if proj is None:
                return None, "NO_PROJECT"
            if (proj.llm_mode or "replay") != "replay":
                return None, "LIVE_MODE_NOT_REPRODUCIBLE"
            b = proj.brief if isinstance(proj.brief, dict) else {}
            scenario, seed = b.get("scenario"), b.get("seed")
            if not isinstance(scenario, str) or not isinstance(seed, int) or proj.config_version_id is None:
                return None, "NO_RECIPE"
            pin = await pinned_config(db, self.settings, proj.config_version_id)
            mandate = await db.get(Mandate, proj.buyer_mandate_id)
            appr = list((await db.execute(select(Approval).where(Approval.project_id == pid))).scalars())
        persisted = await self.store.read(sid)
        ok, _bad = verify_chain(persisted)
        if not persisted or not ok:
            return None, "CHAIN_INVALID"
        store = ReplayStore(self.store, persisted)
        try:
            async with dbm.get_sessionmaker()() as db:
                spec, rt, audit, _release, _cap = await self._build_runtime(
                    db, pin.cfg, pin.number, mode="replay", scenario=scenario, need_index=b.get("need_index"), seed=seed,
                    need_fields=dict(b.get("need_fields") or {}), token_cap=b.get("token_cap"), pid=pid, buyer_org_id=proj.buyer_org_id,
                    org_pins=b.get("org_overrides") or NO_PINS, audit_store=store)
        except ApiError as e:
            return None, f"REBUILD_{e.code}"
        if mandate is None or json.loads(dumps(rt.buyer_mandate)) != mandate.payload:
            return None, "MANDATE_CHANGED"
        if "watchlist_sig" in b and b["watchlist_sig"] != self._wl_sig(spec.extra_watchlist):
            return None, "WATCHLIST_CHANGED"
        h = RunHandle(pid, rt, audit, scenario, config_version=pin.number, release=[], sequential=True)
        self._wire_hooks(h)

        async def track(rec: AuditRecord) -> None:  # `audit_seq` of replayed subagent calls, exactly as in the live run
            h.last_seq = max(h.last_seq, rec.seq)

        audit.subscribe(track)
        decisions = sorted((a for a in appr if a.status is not AS.PENDING),
                           key=lambda a: (a.resolved_at.timestamp() if a.resolved_at else 0.0, a.core_id))

        async def drive() -> None:  # one `_drive`: same loop as the live path, no persistence, no SSE
            tok = par.set_sequential(True)
            try:
                ex = RunExecutor(rt)
                for _ in range(20):
                    await ex.run()
                    if rt.state.status is PS.ESCALATED and not rt.approvals.pending(sid):
                        continue
                    break
            finally:
                par.reset_sequential(tok)

        async def replay() -> str:
            await drive()  # the original start()
            for d in decisions:  # each stored decision, in the order humans made them
                if d.core_id not in {a.id for a in rt.approvals.pending(sid)}:
                    return f"DECISION_NOT_REACHED:{d.core_id}"
                await rt.approvals.resolve(d.core_id, d.status, d.resolver_id or "", d.target_role, d.comment or "",
                                           version=d.version - 1, edit=dict(d.edit or {}))
                if rt.state.status is PS.ESCALATED:  # the live resolve path starts a drive when the run is parked
                    await drive()
            return ""

        try:
            bad = await asyncio.wait_for(replay(), self.settings.baseline_timeout_s)
        except ReplayMismatch as e:
            return None, f"REPLAY_DIVERGED_AT_{e.seq}"
        except Exception as e:  # noqa: BLE001 - reason code only
            return None, f"REPLAY_ERROR_{type(e).__name__}"
        if bad:
            return None, bad
        if store.produced < len(persisted):
            return None, "REPLAY_SHORTER_THAN_RECORD"
        # ---- the replay IS the recorded run. Bookkeeping so that only what the database lacks gets written.
        async with dbm.get_sessionmaker()() as db:
            msg_ids = list((await db.execute(select(Message.id).where(Message.project_id == pid).order_by(Message.seq))).scalars())
            if [str(m.id) for m in rt.bus.log[:len(msg_ids)]] != [str(i) for i in msg_ids]:
                return None, "MESSAGES_DIVERGED"
            h.n_msgs = len(msg_ids)
            h.supplier_rows = set((await db.execute(select(ProjectSupplier.supplier_id).where(ProjectSupplier.project_id == pid))).scalars())
            h.approval_ids = {a.core_id: a.id for a in appr}
            h.resolved_rows = {a.core_id for a in appr if a.status is not AS.PENDING}
            calls = (await db.execute(select(func.coalesce(func.max(SubagentCall.ord), 0)).where(SubagentCall.project_id == pid))).scalar_one()
            h.call_ord = int(calls)
            del h.pending_calls[:h.call_ord]  # already persisted (same order as in the live run)
            for sup_id, rnd in (await db.execute(select(Offer.supplier_id, func.max(Offer.round)).where(Offer.project_id == pid)
                                                 .group_by(Offer.supplier_id))).all():
                h.rounds[sup_id] = int(rnd)
            h.contract_done = (await db.execute(select(func.count()).select_from(Contract).where(Contract.project_id == pid))).scalar_one() > 0
            h.hold_done = (await db.execute(select(func.count()).select_from(LedgerHold).where(LedgerHold.project_id == pid))).scalar_one() > 0
        wrote = await store.commit()  # only now do the replay's new records (the run's next steps) become durable
        audit.clear_listeners()
        h.last_seq = max(h.last_seq, len(persisted) + wrote)
        self._subscribe(h)
        await self._sync_with_retry(h)  # derived rows the crash lost (messages, approvals, state) are written; nothing is duplicated
        self.broker.notify(sid)
        self._evict_if_terminal(h)
        return h, ""

    # ------------------------------------------------------------------ lifecycle
    async def recover(self) -> int:
        """Restart recovery. Projects that were mid-run or parked at a human checkpoint are REBUILT by deterministic replay when
        possible (`_restore`): they come back at the checkpoint they were at and are resumable. Never-started DRAFTs are left alone (their
        runtime is rebuilt on start). Anything that cannot be reproduced (live-mode run, changed inputs, a record that does not replay)
        is closed as terminal: status STOPPED, outcome FAILED, reason RESTART_LOST_RUNTIME, after a `run_lost_runtime` audit event that
        carries the reason code, and its PENDING approvals are closed so no inbox shows a dead item. Returns how many were closed."""
        unfinished = (PS.RUNNING, PS.ESCALATED, PS.FAILED_RECOVERABLE, PS.STALLED, PS.BUDGET_EXCEEDED)
        n = restored = 0
        async with dbm.get_sessionmaker()() as s:
            rows = (await s.execute(select(Project.id).where(Project.status.in_(unfinished)).order_by(Project.created_at))).scalars().all()
        for pid in rows:
            if str(pid) in self.handles:
                continue
            try:
                self._check_capacity()
                h, why = await self._restore(pid)
            except Exception as e:  # noqa: BLE001 - a restore bug must never block startup or lose the project's record
                h, why = None, f"RESTORE_ERROR_{type(e).__name__}"
            if h is not None:
                restored += 1
                log.info("run.restored", project=str(pid))
                continue
            self.handles.pop(str(pid), None)
            audit = AuditLog(self.store, self.clock)
            await audit.append(str(pid), actor_type="orchestrator", actor_id="orchestrator", action="run_lost_runtime",
                               rule_id=LOST_RUNTIME, outcome=OutcomeEnum.FAILED.value, evidence=[{"restore_failed": why}])
            async with dbm.session_scope() as s:
                await s.execute(update(Project).where(Project.id == pid, Project.status.in_(unfinished)).values(
                    status=PS.STOPPED, outcome=OutcomeEnum.FAILED, reason=LOST_RUNTIME, version=Project.version + 1,
                    updated_at=self.clock.now()))
                await self._cancel_pending_approvals(s, pid, LOST_RUNTIME + ": server restarted")
            log.warning("run.lost_runtime", project=str(pid), why=why)
            n += 1
        self.last_recovery = {"restored": restored, "lost": n}
        return n

    async def prune_revoked(self) -> None:
        async with dbm.session_scope() as s:
            await s.execute(delete(RevokedToken).where(RevokedToken.expires_at < self.clock.now()))

    async def shutdown(self) -> None:
        for h in self.handles.values():
            if h.task and not h.task.done():
                h.stopping = True
                h.task.cancel()
        await asyncio.gather(*[h.task for h in self.handles.values() if h.task], return_exceptions=True)
        for h in self.handles.values():
            await self._release_models(h)   # a cancelled run may not have reached its own finally: unload anyway
        async with dbm.session_scope() as s:
            for pid, h in self.handles.items():
                if h.rt.state.status is PS.RUNNING:
                    await s.execute(update(Project).where(Project.id == uuid.UUID(pid), Project.status == PS.RUNNING)
                                    .values(status=PS.FAILED_RECOVERABLE, reason="SHUTDOWN", version=Project.version + 1))
