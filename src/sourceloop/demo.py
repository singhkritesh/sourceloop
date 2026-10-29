"""CLI demo: run a scenario end to end and print the orchestrator timeline.

    python -m sourceloop.demo [happy|no_deal|below_floor|locked_clause|poisoned_news|inflated_claims] [--need N]
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
from urllib.parse import urlparse

from sourceloop.agents.supplier.agent import SupplierProfile
from sourceloop.data.enums import ApprovalKindEnum as AK
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.enums import ProviderKindEnum
from sourceloop.data.synthetic import generate
from sourceloop.llm.config import LlmConfig, ModelRef, ProviderCfg
from sourceloop.llm.providers import OllamaProvider
from sourceloop.net.safe_http import SafeHttp
from sourceloop.orchestrator.scenario import Resolver, ScenarioSpec, build_project, run_to_completion

SCENARIOS = ["happy", "no_deal", "below_floor", "locked_clause", "poisoned_news", "inflated_claims"]
_SHOW = {"stage_changed", "run_started", "run_paused", "run_resumed", "run_finished", "discovery_done", "policy_block",
         "policy_waived", "escalation_created", "approval_resolved", "no_overlap_detected", "candidate_dropped", "ledger_hold_created"}


def live_ollama(url: str, fast: str, chat: str) -> tuple[LlmConfig, dict, OllamaProvider]:
    host = urlparse(url).hostname or "localhost"
    prov = OllamaProvider(url, SafeHttp(set(), {host}, 180))
    cfg = LlmConfig(mode="live", providers={"ollama": ProviderCfg(name="ollama", kind=ProviderKindEnum.OLLAMA, base_url=url, is_local=True)},
                    roles={"fast": ModelRef(provider="ollama", model=fast, timeout_s=120, max_tokens=512),
                           "chat": ModelRef(provider="ollama", model=chat, timeout_s=120, max_tokens=1024)})
    return cfg, {"ollama": prov}, prov


async def run(name: str, need: int, live: dict | None = None) -> None:
    vis, full, _ = generate(42)
    base = build_project(ScenarioSpec(need_index=need), vis, full)
    b = await run_to_completion(base)
    spec = ScenarioSpec(need_index=need)
    resolver = Resolver()
    if name == "no_deal":
        spec.buyer_max_factor, spec.buyer_target_factor = Decimal("0.5"), Decimal("0.4")
    elif name == "below_floor" and b.shortlist:
        spec.profiles = {b.shortlist[0]: SupplierProfile(attempt_below_floor=True)}
    elif name == "inflated_claims" and len(b.shortlist) > 1:
        spec.profiles = {b.shortlist[1]: SupplierProfile(inflate_claims=True)}
    elif name == "locked_clause" and b.awarded:
        spec.profiles = {b.awarded: SupplierProfile(redline_locked_clause="liability")}
        resolver.decisions[AK.CLAUSE] = (AS.APPROVED, {}, "")
    elif name == "poisoned_news":
        spec.poisoned_news = frozenset(full.get(s).country for s in b.shortlist)
    prov = None
    if live:
        spec.llm_cfg, spec.llm_providers, prov = live_ollama(live["url"], live["fast"], live["chat"])
        spec.news_timeout_s, spec.news_concurrency = 150.0, 2
    rt = build_project(spec, vis, full)

    async def show(rec) -> None:
        if rec.action in _SHOW:
            print(f"  #{rec.seq:03d} {rec.action:<20} {rec.rule_id or '':<22} {rec.outcome or ''}")

    rt.audit.subscribe(show)
    print(f"== scenario: {name}  need: {rt.need.id} {rt.need.category} x{rt.need.volume_units}  certs={rt.need.required_certs}")
    try:
        st = await run_to_completion(rt, resolver)
    finally:
        if prov is not None:   # the models are needed only while the run is working: unload on success AND on failure
            for m in {live["fast"], live["chat"]}:
                await prov.release(m)
    print(f"\nstatus={st.status.value} outcome={st.outcome.value if st.outcome else None} reason={st.reason}")
    print(f"longlist={len(st.longlist)} shortlist={len(st.shortlist)} offers={len(st.offers)} deals={len(st.deals)} awarded={st.awarded}")
    for r in st.ranking:
        print("  ranking", r)
    if st.dropped:
        print("  dropped:", st.dropped)
    print("humans:", *[f"{a.target_role.value}:{a.kind.value}={a.status.value}" for a in rt.approvals.all(rt.project_id)], sep="\n  ")
    ok, bad = await rt.audit.verify(rt.project_id)
    print(f"audit events={len(await rt.audit.store.read(rt.project_id))} chain_valid={ok}")
    print(f"buyer tool stats={dict(rt.buyer.tools.stats)} llm={dict(rt.llm.stats)} messages={len(rt.bus.log)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", default="happy", choices=SCENARIOS)
    ap.add_argument("--need", type=int, default=0)
    ap.add_argument("--live", action="store_true", help="use a local Ollama model for news-risk extraction")
    ap.add_argument("--ollama-url", default="http://localhost:11434")
    ap.add_argument("--fast", default="gemma4:e2b-mlx")
    ap.add_argument("--chat", default="qwen3.8:27b-mlx")
    a = ap.parse_args()
    live = {"url": a.ollama_url, "fast": a.fast, "chat": a.chat} if a.live else None
    asyncio.run(run(a.scenario, a.need, live))


if __name__ == "__main__":
    main()
