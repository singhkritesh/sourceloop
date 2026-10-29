"""Run executor: deterministic orchestration of one sourcing project.

* Resumable: every step is guarded by `state.done` markers; escalation pauses (raises Paused) and `run()`
  can simply be called again after a human resolves the approval.
* Parallel fan-out to suppliers via asyncio.gather (bounded by the tool runners' semaphores).
* The executor never reasons about price; it moves messages, applies the policy engine results and
  asks humans at the checkpoints defined in the (pinned) config.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sourceloop.agents.buyer.agent import BuyerAgent, Evaluated
from sourceloop.agents.common import contracts as K
from sourceloop.agents.common import par
from sourceloop.agents.supplier.agent import SupplierAgent
from sourceloop.audit.canonical import dumps, sha256_hex
from sourceloop.audit.chain import AuditLog
from sourceloop.bus.bus import ORCHESTRATOR, Delivered, MessageBus, Rejected, SendResult
from sourceloop.bus.messages import Envelope
from sourceloop.compliance.scoring import Scored
from sourceloop.compliance.scoring import shortlist as pick_shortlist
from sourceloop.data.enums import ApprovalKindEnum as AK
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.enums import OutcomeEnum
from sourceloop.data.enums import ProjectStatusEnum as PS
from sourceloop.data.enums import StageEnum as S
from sourceloop.llm.client import BudgetExceeded, LLMClient
from sourceloop.llm.prompt_builder import build_choice
from sourceloop.llm.prompts_default import JUDGE_OPTIONS, JUDGE_RUBRIC, JUDGE_STATE, JUDGE_VARS
from sourceloop.orchestrator.approvals import Approval, ApprovalService
from sourceloop.orchestrator.transitions import check_status, enabled_stages, next_stage
from sourceloop.policy.engine import Action
from sourceloop.policy.judge import Level, Turn, Verdict, combine, judge, turn_lines
from sourceloop.policy.mandates import BuyerMandate, SupplierMandate, overlap

CLAUSE_RULES = {"LOCKED_CLAUSE", "CLAUSE_NOT_IN_LIBRARY", "CLAUSE_NOT_NEGOTIABLE"}


class Paused(Exception):
    def __init__(self, approval: Approval):
        super().__init__(approval.id)
        self.approval = approval


class RunInProgress(Exception):
    """A second run() on a project whose executor is already running (single-flight guard, BUG-11)."""

    def __init__(self, project_id: str):
        super().__init__(f"run already in progress: {project_id}")
        self.project_id = project_id


class Stopped(Exception):
    def __init__(self, outcome: OutcomeEnum, reason: str):
        super().__init__(reason)
        self.outcome, self.reason = outcome, reason


@dataclass
class RunConfig:
    longlist_size: int = 10
    shortlist_size: int = 5
    negotiate_top: int = 2
    min_countries: int = 2
    disabled_stages: frozenset[S] = frozenset()
    standard_clauses: tuple[str, ...] = ("payment_terms", "delivery", "warranty", "liability", "termination", "ip")
    news_escalate_severity: float = 0.7
    news_min_confidence: float = 0.6
    config_version: int = 1
    confirm_requirement: bool = True  # Studio workflow.checkpoints.requirement_confirmation (the only skippable checkpoint)
    need_autonomy: int = 1  # Studio workflow.autonomy[NEED]; >= 3 ("act then report") auto-confirms the requirement (audited)
    judge_llm: bool = False  # Studio workflow.negotiation.judge_llm: ask the reviewer model too (it can only add a concern)
    batched_async: bool = False  # Studio workflow.checkpoints.batched_async: requirement confirmation does not block discovery


@dataclass
class SupplierRuntime:
    sid: str
    name: str
    country: str
    agent: SupplierAgent
    mandate: SupplierMandate


@dataclass
class ProjectState:
    stage: S = S.NEED
    status: PS = PS.DRAFT
    outcome: OutcomeEnum | None = None
    reason: str | None = None
    done: set[str] = field(default_factory=set)
    longlist: list[K.Cand] = field(default_factory=list)
    interested: list[str] = field(default_factory=list)
    qual: dict[str, dict[str, Any]] = field(default_factory=dict)
    included: list[str] = field(default_factory=list)
    shortlist: list[str] = field(default_factory=list)
    offers: dict[str, Envelope] = field(default_factory=dict)
    deals: dict[str, dict[str, Any]] = field(default_factory=dict)
    dropped: dict[str, str] = field(default_factory=dict)
    awarded: str | None = None
    contract: dict[str, Any] | None = None
    hold: str | None = None
    ranking: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ProjectRuntime:
    project_id: str
    need: K.NeedSpec
    brief: dict[str, Any]
    buyer: BuyerAgent
    buyer_mandate: BuyerMandate
    bus: MessageBus
    audit: AuditLog
    approvals: ApprovalService
    llm: LLMClient
    cfg: RunConfig
    make_supplier: Any  # Callable[[str], SupplierRuntime]  (lazy, registers keys/mandates on the bus)
    suppliers: dict[str, SupplierRuntime] = field(default_factory=dict)
    state: ProjectState = field(default_factory=ProjectState)
    running: bool = False  # claim token: set by RunExecutor.run() for the duration of one run segment

    def supplier(self, sid: str) -> SupplierRuntime:
        if sid not in self.suppliers:
            self.suppliers[sid] = self.make_supplier(sid)
        return self.suppliers[sid]


class RunExecutor:
    def __init__(self, rt: ProjectRuntime) -> None:
        self.rt = rt
        self.st = rt.state
        self.stages = enabled_stages(rt.cfg.disabled_stages)

    # ------------------------------------------------------------------ driver
    async def _audit(self, action: str, **kw: Any) -> None:
        await self.rt.audit.append(self.rt.project_id, actor_type=kw.pop("actor_type", "orchestrator"),
                                   actor_id=kw.pop("actor_id", ORCHESTRATOR), action=action,
                                   config_version=self.rt.cfg.config_version, **kw)

    async def _set_status(self, to: PS) -> None:
        if self.st.status is not to:
            check_status(self.st.status, to)
            self.st.status = to

    async def run(self) -> ProjectState:
        """Single-flight per project: the claim is taken without an await in between (atomic on the event loop), so two
        concurrent callers cannot interleave. The loser gets RunInProgress and produces no traffic and no audit."""
        if self.rt.running:
            raise RunInProgress(self.rt.project_id)
        self.rt.running = True
        try:
            return await self._run()
        finally:
            self.rt.running = False

    async def _run(self) -> ProjectState:
        st = self.st
        if st.status is PS.DRAFT:
            await self._set_status(PS.RUNNING)
            await self._audit("run_started", outcome="RUNNING", evidence=[{"need": self.rt.need.id}])
        else:
            await self._set_status(PS.RUNNING)
            await self._audit("run_resumed", outcome="RUNNING")
        try:
            while st.stage is not S.DONE:
                self.rt.bus.set_stage(st.stage)
                if f"enter:{st.stage}" not in st.done:
                    st.done.add(f"enter:{st.stage}")
                    await self._audit("stage_changed", outcome=st.stage.value)
                await getattr(self, f"_stage_{st.stage.value.lower()}")()
                st.stage = next_stage(st.stage, self.stages)
            st.outcome = st.outcome or OutcomeEnum.DEAL
            await self._set_status(PS.DONE)
            await self._audit("run_finished", outcome=st.outcome.value)
        except Paused as p:
            await self._set_status(PS.ESCALATED)
            await self._audit("run_paused", rule_id=p.approval.kind.value, outcome="ESCALATED",
                              evidence=[{"approval": p.approval.id, "role": p.approval.target_role.value}])
        except Stopped as s:
            st.outcome, st.reason = s.outcome, s.reason
            await self._set_status(PS.STOPPED)
            await self._audit("run_finished", outcome=s.outcome.value, rule_id=s.reason)
        except BudgetExceeded:
            await self._set_status(PS.BUDGET_EXCEEDED)
            await self.rt.approvals.ensure(self.rt.project_id, AK.BUDGET, "tokens", ("TOKEN_BUDGET",))
            await self._audit("run_paused", rule_id="BUDGET", outcome="BUDGET_EXCEEDED")
        except Exception as e:
            await self._set_status(PS.FAILED_RECOVERABLE)
            await self._audit("run_error", outcome="FAILED_RECOVERABLE", rule_id=type(e).__name__)
            raise  # supervisors/tests must see the failure
        return st

    # ------------------------------------------------------------------ helpers
    async def _gate(self, kind: AK, key: str, reasons: tuple[str, ...], evidence: dict[str, Any] | None = None,
                    recommendation: dict[str, Any] | None = None) -> Approval:
        if kind is not AK.REQUIREMENT:
            await self._settle_deferred()
        a = await self.rt.approvals.ensure(self.rt.project_id, kind, key, reasons, evidence, recommendation, self.st.stage.value)
        if a.status is AS.PENDING:
            raise Paused(a)
        return a

    async def _once(self, key: str, coro_fn) -> Any:
        if key in self.st.done:
            return None
        out = await coro_fn()
        self.st.done.add(key)
        return out

    def _buyer_inbox(self, types: set[T], sender: str | None = None) -> list[Envelope]:
        return self.rt.bus.inbox("buyer", types=types, sender=sender)

    async def _failures(self, failures: list[tuple[Rejected, str, str]]) -> None:
        """Record ALL failed sends first, then pause on the first unresolved approval (so none is lost on resume)."""
        pending: list[Approval] = []
        for res, sid, what in failures:
            self.st.dropped[sid] = res.rule_id or res.code
            if res.action is not None:
                a = await self.rt.approvals.ensure(self.rt.project_id, AK.MANDATE_BREACH, f"{what}:{sid}:{res.rule_id}",
                                                   (res.rule_id or res.code,), {"supplier": sid, "step": what},
                                                   {"suggested": "exclude supplier"}, self.st.stage.value)
                if a.status is AS.PENDING:
                    pending.append(a)
        if pending:
            raise Paused(pending[0])

    async def _supplier_round(self, sids: list[str]) -> dict[str, list[SendResult]]:
        res = await par.gather(*[self.rt.supplier(s).agent.step() for s in sids])
        return dict(zip(sids, res, strict=True))

    # ------------------------------------------------------------------ stages
    async def _stage_need(self) -> None:
        rt = self.rt
        res = await self._once("brief", lambda: rt.buyer.ch.send(T.BRIEF, ORCHESTRATOR, rt.brief))
        if isinstance(res, Rejected):
            raise Stopped(OutcomeEnum.FAILED, f"brief:{res.code}")
        if not rt.cfg.confirm_requirement or rt.cfg.need_autonomy >= 3:  # non-invariant checkpoint switched off: audited, never silent
            if "requirement_auto" not in self.st.done:
                self.st.done.add("requirement_auto")
                await self._audit("checkpoint_skipped", rule_id=("requirement_confirmation_disabled" if not rt.cfg.confirm_requirement
                                                                 else "autonomy_act_then_report"), outcome="AUTO_CONFIRMED")
            return
        if rt.cfg.batched_async:
            # async mode: the confirmation is raised but discovery proceeds; the first MANDATORY checkpoint settles it
            # (see _settle_deferred), so no invariant checkpoint can be reached while it is still open.
            a = await rt.approvals.ensure(rt.project_id, AK.REQUIREMENT, "need", ("CONFIRM_REQUIREMENT",), {"brief": rt.brief},
                                          {"suggested": "confirm"}, self.st.stage.value)
            if a.status is AS.PENDING:
                if "requirement_deferred" not in self.st.done:
                    self.st.done.add("requirement_deferred")
                    await self._audit("checkpoint_deferred", rule_id="requirement_confirmation_async", outcome="PENDING")
                return
            if a.status is AS.REJECTED:
                raise Stopped(OutcomeEnum.STOPPED_BY_USER, "requirement_rejected")
            return
        a = await self._gate(AK.REQUIREMENT, "need", ("CONFIRM_REQUIREMENT",), {"brief": rt.brief}, {"suggested": "confirm"})
        if a.status is AS.REJECTED:
            raise Stopped(OutcomeEnum.STOPPED_BY_USER, "requirement_rejected")

    async def _settle_deferred(self) -> None:
        """A batched/async requirement confirmation must be resolved before ANY mandatory checkpoint can be raised."""
        if "requirement_deferred" not in self.st.done:
            return
        a = await self.rt.approvals.ensure(self.rt.project_id, AK.REQUIREMENT, "need", ("CONFIRM_REQUIREMENT",))
        if a.status is AS.PENDING:
            raise Paused(a)
        if a.status is AS.REJECTED:
            raise Stopped(OutcomeEnum.STOPPED_BY_USER, "requirement_rejected")

    async def _stage_discovery(self) -> None:
        rt, st = self.rt, self.st
        if "research" not in st.done:
            r = await rt.buyer.discover(rt.need, rt.cfg.longlist_size, rt.cfg.min_countries)
            if not (r.ok and r.value):
                raise Stopped(OutcomeEnum.FAILED, f"research:{r.error}")
            st.longlist = list(r.value.candidates)
            st.done.add("research")
            await self._audit("discovery_done", outcome="OK", evidence=[{
                "considered": r.value.considered, "merged_duplicates": r.value.merged_duplicates,
                "filtered": r.value.filtered, "longlist": len(st.longlist)}])
        if not st.longlist:
            raise Stopped(OutcomeEnum.NO_VIABLE_SUPPLIER, "empty_longlist")
        sids = [c.supplier_id for c in st.longlist]
        for s in sids:
            rt.supplier(s)  # registers participant keys + mandates on the bus before any send
        todo = [s for s in sids if f"invite:{s}" not in st.done and s not in st.dropped]
        results = await rt.buyer.send_all(T.INVITE, [f"supplier:{s}" for s in todo], lambda _t: {
            "category": rt.need.category, "volume_units": rt.need.volume_units,
            "required_certificates": list(rt.need.required_certs), "max_lead_time_weeks": rt.need.max_lead_time_weeks})
        for s in todo:
            r = results[f"supplier:{s}"]
            st.done.add(f"invite:{s}")
            if isinstance(r, Rejected):
                st.dropped[s] = r.rule_id or r.code
                await self._audit("candidate_dropped", rule_id=st.dropped[s], evidence=[{"supplier": s}], outcome="DROPPED")
        live = [s for s in sids if s not in st.dropped]
        await self._supplier_round(live)
        replies = {e.from_agent.split(":", 1)[1]: e for e in self._buyer_inbox({T.INTEREST_REPLY})}
        st.interested = [s for s in live if (e := replies.get(s)) and e.payload["interested"]] or st.interested
        if not st.interested:
            raise Stopped(OutcomeEnum.NO_VIABLE_SUPPLIER, "no_interest")

    async def _stage_qualification(self) -> None:
        rt, st = self.rt, self.st
        sids = [s for s in st.interested if s not in st.dropped]
        todo = [s for s in sids if f"docreq:{s}" not in st.done]
        if todo:
            results = await rt.buyer.send_all(T.DOC_REQUEST, [f"supplier:{s}" for s in todo],
                                              lambda _t: {"required_certificates": list(rt.need.required_certs) or ["ISO9001"]})
            for s in todo:
                st.done.add(f"docreq:{s}")
                if isinstance(results[f"supplier:{s}"], Rejected):
                    st.dropped[s] = results[f"supplier:{s}"].code
            sub = await self._supplier_round([s for s in todo if s not in st.dropped])
            self._buyer_inbox({T.DOC_SUBMISSION})
            # e.g. CLAIM_NOT_PROVABLE: the inflated claim never reached the buyer; supplier is dropped, humans told
            await self._failures([(r, s, "doc_submission") for s, rs in sorted(sub.items()) for r in rs
                                  if isinstance(r, Rejected) and r.needs_escalation])

        if "screen" not in st.done:
            live = [s for s in sids if s not in st.dropped]

            async def one(s: str):
                comp = await rt.buyer.screen(rt.need, s)
                news, wx = await rt.buyer.region_risk(rt.supplier(s).country, rt.need.max_lead_time_weeks)
                return s, comp, news, wx

            for s, comp, news, wx in await par.gather(*[one(s) for s in live]):
                status = comp.value.status if comp.ok and comp.value else "REVIEW"
                reasons = list(comp.value.reasons) if comp.ok and comp.value else ["COMPLIANCE_TOOL_FAILED"]
                nsev = news.value.max_severity if news.ok and news.value else 0.0
                nconf = news.confidence if news.ok else 0.0
                if status == "QUALIFIED":
                    if not news.ok or nconf < rt.cfg.news_min_confidence:
                        status, reasons = "REVIEW", ["NEWS_LOW_CONFIDENCE"]
                    elif nsev >= rt.cfg.news_escalate_severity:
                        status, reasons = "REVIEW", [f"NEWS_RISK_HIGH:{','.join(news.value.kinds)}"]
                st.qual[s] = {"status": status, "reasons": reasons, "news_sev": nsev,
                              "weather_p": wx.value.delay_probability if wx.ok and wx.value else 0.2,
                              "country": rt.supplier(s).country}
                await rt.buyer.report_qualification(s, status, tuple(reasons))
            st.done.add("screen")

        flagged = {s: q for s, q in st.qual.items() if q["status"] in ("REVIEW", "BLOCKED")}
        include_override: set[str] = set()
        if flagged:
            a = await self._gate(AK.COMPLIANCE_FLAG, "qual_flags", tuple(sorted({r for q in flagged.values() for r in q["reasons"]}))[:20],
                                 {"flagged": {s: {"status": q["status"], "reasons": q["reasons"]} for s, q in flagged.items()}},
                                 {"suggested": "exclude all flagged"})
            if a.status is AS.EDITED:  # human cleared specific suppliers (comment required) — the only way a flag is overridden
                include_override = set(await self._edit_ids(a, "include", fail_closed=False)) & set(flagged)
        passed = [s for s in sids if s not in st.dropped and (st.qual.get(s, {}).get("status") == "QUALIFIED" or s in include_override)]
        if not passed:
            raise Stopped(OutcomeEnum.NO_VIABLE_SUPPLIER, "none_qualified")

        a = await self._gate(AK.TECHNICAL_QUAL, "tech", ("TECHNICAL_PASS_FAIL",), {"candidates": passed}, {"suggested": "pass all"})
        failed = set(await self._edit_ids(a, "fail", fail_closed=True)) if a.status is AS.EDITED else set()
        if a.status is AS.REJECTED:
            raise Stopped(OutcomeEnum.NO_VIABLE_SUPPLIER, "technical_qualification_failed")
        st.included = [s for s in passed if s not in failed]
        scored = {c.supplier_id: c for c in st.longlist}
        pick = pick_shortlist([Scored(s, scored[s].country, scored[s].score, ()) for s in st.included if s in scored],
                              k=rt.cfg.shortlist_size, min_countries=rt.cfg.min_countries)
        st.shortlist = [p.supplier_id for p in pick]
        if not st.shortlist:
            raise Stopped(OutcomeEnum.NO_VIABLE_SUPPLIER, "empty_shortlist")

    async def _stage_sourcing(self) -> None:
        rt, st = self.rt, self.st
        todo = [s for s in st.shortlist if f"rfq:{s}" not in st.done]
        if todo:
            results = await rt.buyer.send_all(T.RFQ, [f"supplier:{s}" for s in todo], lambda _t: {
                "category": rt.need.category, "volume_units": rt.need.volume_units,
                "target_lead_time_weeks": rt.need.max_lead_time_weeks, "spec_summary": rt.brief.get("spec_summary", "")})
            for s in todo:
                st.done.add(f"rfq:{s}")
                if isinstance(results[f"supplier:{s}"], Rejected):
                    st.dropped[s] = results[f"supplier:{s}"].rule_id or results[f"supplier:{s}"].code
            sub = await self._supplier_round([s for s in todo if s not in st.dropped])
            for e in self._buyer_inbox({T.OFFER, T.REJECT}):
                sid = e.from_agent.split(":", 1)[1]
                if e.type == T.OFFER:
                    st.offers[sid] = e
                else:
                    st.dropped[sid] = f"declined:{e.payload['reason_code']}"
            # e.g. PRICE_BELOW_FLOOR: the offer was never delivered; supplier dropped, human informed
            await self._failures([(r, s, "offer") for s, rs in sorted(sub.items()) for r in rs if isinstance(r, Rejected)])
        if not st.offers:
            raise Stopped(OutcomeEnum.NO_DEAL, "no_offers")

        if "negotiate" not in st.done:
            evals = await par.gather(*[rt.buyer.evaluate(s, e, rt.supplier(s).country, st.qual[s]["weather_p"], st.qual[s]["news_sev"])
                                           for s, e in sorted(st.offers.items())])
            ranked = sorted(evals, key=lambda x: (x.landed_total, x.supplier_id))
            st.ranking = [{"supplier": x.supplier_id, "landed": str(x.landed_total), "stage": "initial"} for x in ranked]
            finalists = []
            for x in ranked:
                if len(finalists) >= rt.cfg.negotiate_top:
                    break
                if not overlap(rt.buyer_mandate, rt.supplier(x.supplier_id).mandate):  # predicate probe: bool only
                    await self._audit("no_overlap_detected", evidence=[{"supplier": x.supplier_id}], outcome="NO_DEAL")
                    st.dropped[x.supplier_id] = "NO_OVERLAP"
                    continue
                finalists.append(x)
            outs = await par.gather(*[self._negotiate(x) for x in finalists])
            for x, deal in zip(finalists, outs, strict=True):
                if deal:
                    st.deals[x.supplier_id] = deal
            st.done.add("negotiate")
        if not st.deals:
            raise Stopped(OutcomeEnum.NO_DEAL, "no_agreement")

        final = await par.gather(*[rt.buyer.evaluate(s, d["msg"], rt.supplier(s).country, st.qual[s]["weather_p"], st.qual[s]["news_sev"])
                                       for s, d in sorted(st.deals.items())])
        ranked = sorted(final, key=lambda x: (x.landed_total, x.supplier_id))
        st.ranking = [{"supplier": x.supplier_id, "landed": str(x.landed_total), "price": str(x.offer.payload["unit_price"]),
                       "delay_p": x.delay_probability} for x in ranked]
        best = ranked[0].supplier_id
        a = await self._gate(AK.AWARD, "award", ("AWARD_RECOMMENDATION",), {"ranking": st.ranking},
                             {"suggested": best, "why_others_lost": [r["supplier"] for r in st.ranking[1:]]})
        if a.status is AS.REJECTED:
            raise Stopped(OutcomeEnum.STOPPED_BY_USER, "award_rejected")
        pick_s = a.edit.get("supplier") if a.status is AS.EDITED else None
        if a.status is AS.EDITED and pick_s is not None and not (isinstance(pick_s, str) and pick_s in st.deals):
            await self._audit("approval_edit_malformed", rule_id=a.kind.value, outcome="IGNORED_USED_RECOMMENDATION")
        st.awarded = pick_s if isinstance(pick_s, str) and pick_s in st.deals else best

    async def _edit_ids(self, a: Approval, key: str, *, fail_closed: bool) -> list[str]:
        """Defensive read of a human edit list. A malformed value never crashes the run: it is audited and either
        ignored (conservative: no override) or, when `fail_closed`, stops the project cleanly (REJECTED-equivalent)."""
        v = a.edit.get(key, [])
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return v
        await self._audit("approval_edit_malformed", rule_id=a.kind.value, outcome="REJECTED_EQUIVALENT" if fail_closed else "IGNORED")
        if fail_closed:
            raise Stopped(OutcomeEnum.NO_VIABLE_SUPPLIER, "invalid_approval_edit")
        return []

    async def _judge_llm(self, t: Turn) -> tuple[Level | None, float | None]:
        """Optional second opinion from the 'fast' model as a typed decision: a probability for each verdict (first-token log-probs).
        Only relative figures are sent. It flags a turn when it puts at least half its probability on CONCERN or INAPPROPRIATE and was
        actually answering the question. Any failure is audited and ignored: the code verdict already stands, so this can add a concern
        but never remove one."""
        rt = self.rt

        args = {"actor": t.actor, "action": t.action, "round": str(t.round), "max_rounds": str(t.max_rounds),
                **turn_lines(t, rt.bus.cfg.price_band_pct)}
        labels, letters, system, user, tpl = build_choice(JUDGE_RUBRIC, JUDGE_STATE, JUDGE_VARS, args, JUDGE_OPTIONS)
        try:
            d = await rt.llm.decide(project_id=rt.project_id, agent_key="negotiation_judge", role="fast", template_hash=tpl,
                                    system=system, user=user, labels=labels, letters=letters)
        except Exception as e:  # noqa: BLE001 - advisory path: record and carry on with the code verdict
            await self._audit("negotiation_judge_llm_unavailable", outcome=type(e).__name__[:40])
            return None, None
        p_flag = round(d.probs["CONCERN"] + d.probs["INAPPROPRIATE"], 2)
        return (Level.CONCERN if (p_flag >= 0.5 and d.mass >= 0.5) else Level.APPROPRIATE), p_flag

    async def _judged(self, sid: str, t: Turn) -> Verdict:
        """Judge one turn, then audit it (before anything is delivered). Codes only: no prices or limits in the evidence."""
        v = judge(t)
        ev: dict[str, Any] = {"supplier": sid, "actor": t.actor, "action": t.action, "round": t.round}
        if self.rt.cfg.judge_llm:
            lvl, p_flag = await self._judge_llm(t)
            v = combine(v, lvl)
            if p_flag is not None:
                ev["llm_p_flag"] = p_flag   # the reviewer's own probability that the turn is questionable; audited, never acted on alone
        ev["codes"] = list(v.codes)
        await self._audit("negotiation_judged", rule_id=v.codes[0] if v.codes else None, outcome=v.level.value, evidence=[ev])
        return v

    async def _judge_stop(self, sid: str) -> None:
        self.st.dropped[sid] = "JUDGE_INAPPROPRIATE"
        await self._audit("candidate_dropped", rule_id="JUDGE_INAPPROPRIATE", evidence=[{"supplier": sid}], outcome="DROPPED")
        await self.rt.buyer.walk(sid, "JUDGED_STOP")

    async def _negotiate(self, x: Evaluated) -> dict[str, Any] | None:
        rt, sid = self.rt, x.supplier_id
        last: Envelope = x.offer
        my_last: Decimal | None = None
        sup_prev: Decimal = Decimal(str(last.payload["unit_price"]))
        idx, step, cap = rt.bus.cfg.index_price, rt.buyer_mandate.min_step_pct, rt.buyer_mandate.max_rounds

        def turn(actor: str, action: str, rnd: int, price: Decimal | None, own_prev: Decimal | None, other: Decimal | None) -> Turn:
            return Turn(actor, action, rnd, cap, price, own_prev, other, idx, step)

        for rnd in range(1, cap + 1):
            sup_price = Decimal(str(last.payload["unit_price"]))
            action, price = rt.buyer.decide(sup_price, my_last, rnd)
            if action == "accept":
                if (await self._judged(sid, turn("buyer", "accept", rnd, sup_price, my_last, sup_price))).level is Level.INAPPROPRIATE:
                    await self._judge_stop(sid)
                    return None
                r = await rt.buyer.accept(sid, last)
                if isinstance(r, Delivered):
                    await rt.suppliers[sid].agent.step()
                    return self._deal(last)
                return None
            if action == "walk" or price is None:
                await self._judged(sid, turn("buyer", "walk", rnd, None, my_last, sup_price))
                await rt.buyer.walk(sid)
                return None
            if (await self._judged(sid, turn("buyer", "counter", rnd, price, my_last, sup_price))).level is Level.INAPPROPRIATE:
                await self._judge_stop(sid)
                return None
            r = await rt.buyer.counter(sid, last, price, rnd)
            if isinstance(r, Rejected):
                await self._audit("candidate_dropped", rule_id=r.rule_id or r.code, evidence=[{"supplier": sid}], outcome="DROPPED")
                return None
            my_last = price
            await rt.suppliers[sid].agent.step()
            replies = self._buyer_inbox({T.ACCEPT, T.COUNTER, T.REJECT}, sender=f"supplier:{sid}")
            if not replies:
                return None
            reply = replies[-1]
            if reply.type == T.ACCEPT:
                await self._judged(sid, turn("supplier", "accept", rnd, price, sup_prev, price))
                return self._deal(r.envelope)
            if reply.type == T.REJECT:
                await self._judged(sid, turn("supplier", "reject", rnd, None, sup_prev, price))
                return None
            rp = Decimal(str(reply.payload["unit_price"]))
            v = await self._judged(sid, turn("supplier", "counter", int(reply.payload["round"]) - 1, rp, sup_prev, price))
            sup_prev = rp
            if v.level is Level.INAPPROPRIATE:
                await self._judge_stop(sid)
                return None
            last = reply
        return None

    @staticmethod
    def _deal(msg: Envelope) -> dict[str, Any]:
        return {"msg": msg, "price": msg.payload["unit_price"], "lead": msg.payload["lead_time_weeks"], "capacity": msg.payload["capacity_units"]}

    async def _stage_contracting(self) -> None:
        rt, st = self.rt, self.st
        sid = st.awarded
        assert sid is not None
        sup = rt.supplier(sid)
        if "draft" not in st.done:
            r = await rt.buyer.draft_contract(sid, rt.cfg.standard_clauses)
            if isinstance(r, Rejected):
                raise Stopped(OutcomeEnum.FAILED, f"contract_draft:{r.rule_id or r.code}")
            results = (await sup.agent.step())
            st.done.add("draft")
            for res in results:
                if isinstance(res, Rejected) and res.action is Action.ESCALATE:
                    st.done.add(f"redline_pending:{res.rule_id}")
            self._buyer_inbox({T.REDLINE})
        deviations: list[str] = []
        clause_keys = [k for k in st.done if k.startswith("redline_pending:")]
        for k in clause_keys:
            rule = k.split(":", 1)[1]
            clause = sup.agent.profile.redline_locked_clause or ""
            a = await self._gate(AK.CLAUSE, f"redline:{clause}", (rule,), {"clause": clause, "supplier": sid},
                                 {"suggested": "keep standard clause"})
            if a.status is AS.EDITED and a.edit.get("accept_change") is True:
                wkey = rt.bus.grant_waiver(rule, sup.agent.channel.agent_id, clause)
                try:
                    r = await sup.agent.redline(clause, "propose_alternative", clause)
                finally:
                    rt.bus.revoke_waiver(wkey)  # never leave a standing waiver, even if the redline raised
                if isinstance(r, Delivered):
                    deviations.append(clause)
        st.contract = {"supplier": sid, "clauses": list(rt.cfg.standard_clauses), "deviations": deviations,
                       "price": st.deals[sid]["price"], "lead": st.deals[sid]["lead"]}
        cid = sha256_hex(dumps(st.contract))[:32]
        cid = f"{cid[:8]}-{cid[8:12]}-{cid[12:16]}-{cid[16:20]}-{cid[20:32]}"
        r = await self._once("sign_request", lambda: rt.buyer.request_signature(sid, cid))
        a = await self._gate(AK.SIGNATURE, "sign", ("SIGNATURE_REQUIRED",), {"contract": st.contract, "deviations": deviations},
                             {"suggested": "sign"})
        if a.status is AS.REJECTED:
            raise Stopped(OutcomeEnum.STOPPED_BY_USER, "signature_rejected")
        st.contract["signature_hash"] = sha256_hex(dumps(st.contract) + "|simulated-signature")

    async def _stage_onboarding(self) -> None:
        rt, st = self.rt, self.st
        sid = st.awarded
        assert sid is not None
        await self._once("onboard", lambda: rt.suppliers[sid].agent.send_onboarding())
        self._buyer_inbox({T.ONBOARD_DATA})
        a = await self._gate(AK.BANK_DETAILS, "bank", ("CONFIRM_BANK_DETAILS",), {"supplier": sid}, {"suggested": "confirm"})
        if a.status is AS.REJECTED:
            raise Stopped(OutcomeEnum.STOPPED_BY_USER, "bank_details_rejected")
        st.hold = "HELD"  # agents can never release this
        await rt.approvals.ensure(rt.project_id, AK.HOLD_RELEASE, "hold", ("RELEASE_PAYMENT_HOLD",), {"supplier": sid}, {}, S.ONBOARDING.value)
        await self._audit("ledger_hold_created", outcome="HELD", evidence=[{"supplier": sid}])
