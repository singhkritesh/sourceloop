"""Runs the whole evaluation: agent runs, manual baseline, scenario controls and ablations."""
from __future__ import annotations

import time
from collections import Counter
from typing import Any

from sourceloop.eval import ablations as ab
from sourceloop.eval.assumptions import LABEL, Assumptions
from sourceloop.eval.baseline import run_baseline
from sourceloop.eval.metrics import RunOut, aggregate_agent, rate, run_agent, stats_of
from sourceloop.eval.scenarios import SpecHook
from sourceloop.eval.world import World, build_world

SCHEMA_VERSION = 1
CONTROL_SCENARIOS = ("below_floor", "inflated_claims", "locked_clause", "poisoned_news", "no_deal")


def _hook(*hooks: SpecHook | None) -> SpecHook | None:
    live = [h for h in hooks if h]
    if not live:
        return None

    def run(spec):  # type: ignore[no-untyped-def]
        for h in live:
            h(spec)
    return run


async def run_suite(seeds: tuple[int, ...] = (42, 43, 44), n_needs: int = 12, A: Assumptions | None = None,
                    ablation_seeds: tuple[int, ...] | None = None, live_hook: SpecHook | None = None,
                    run_ablations: bool = True) -> dict[str, Any]:
    A = A or Assumptions()
    ablation_seeds = seeds if ablation_seeds is None else ablation_seeds
    t0 = time.perf_counter()
    worlds: dict[int, World] = {s: build_world(s) for s in seeds}
    base: dict[tuple[int, int], RunOut] = {}
    agent_recs: list[dict[str, Any]] = []
    baseline_recs: list[dict[str, Any]] = []
    walls: list[float] = []

    for seed in seeds:
        w = worlds[seed]
        for idx in range(n_needs):
            out = await run_agent(w, idx, A, spec_hook=live_hook)
            base[(seed, idx)] = out
            agent_recs.append(out.rec)
            walls.append(out.wall_s)
            baseline_recs.append(run_baseline(w, idx, A))
    t_main = time.perf_counter() - t0

    controls: dict[tuple[int, int, str], RunOut] = {}
    ablation_out: list[dict[str, Any]] = []
    scenario_recs: list[dict[str, Any]] = []

    async def control(seed: int, idx: int, kind: str) -> RunOut:
        k = (seed, idx, kind)
        if k not in controls:
            controls[k] = base[(seed, idx)] if kind == "plain" else await run_agent(
                worlds[seed], idx, A, kind=kind, base=base[(seed, idx)].st, spec_hook=live_hook, tag=f"-{kind}")
        return controls[k]

    if run_ablations:
        for seed in ablation_seeds:
            for idx in range(n_needs):
                for kind in CONTROL_SCENARIOS:
                    scenario_recs.append((await control(seed, idx, kind)).rec)
        for a in ab.ABLATIONS:
            rows: list[dict[str, Any]] = []
            totals: dict[str, dict[str, int]] = {m: {"control": 0, "ablated": 0} for m in a.metrics}
            deals = {"control": 0, "ablated": 0}
            examples: list[dict[str, Any]] = []
            for seed in ablation_seeds:
                for idx in range(n_needs):
                    for kind in a.scenarios:
                        c = await control(seed, idx, kind)
                        x = await run_agent(worlds[seed], idx, A, kind=kind, base=base[(seed, idx)].st, tag=f"-{a.key}",
                                            spec_hook=_hook(live_hook, a.spec_hook), patch=a.patch)
                        deals["control"] += c.rec["safety"]["deals"]
                        deals["ablated"] += x.rec["safety"]["deals"]
                        for m in a.metrics:
                            totals[m]["control"] += c.rec["safety"][m]
                            totals[m]["ablated"] += x.rec["safety"][m]
                            if x.rec["safety"][m] > c.rec["safety"][m] and len(examples) < 6:
                                examples.append({"seed": seed, "need": x.rec["need"], "scenario": kind, "metric": m,
                                                 "control": c.rec["safety"][m], "ablated": x.rec["safety"][m]})
                        rows.append({"seed": seed, "need": x.rec["need"], "scenario": kind,
                                     "control": c.rec["safety"], "ablated": x.rec["safety"]})
            bad_c = sum(t["control"] for t in totals.values())
            bad_a = sum(t["ablated"] for t in totals.values())
            ablation_out.append({
                "key": a.key, "title": a.title, "safeguard": a.safeguard, "expected": a.expected, "note": a.note,
                "scenarios": list(a.scenarios), "runs": len(rows), "totals": totals, "deals": deals,
                "bad_control": bad_c, "bad_ablated": bad_a, "bad_outcome_appears": bad_a > bad_c, "examples": examples,
            })

    t_total = time.perf_counter() - t0
    comparison = _compare(agent_recs, baseline_recs)
    results = {
        "schema_version": SCHEMA_VERSION,
        "meta": {"seeds": list(seeds), "ablation_seeds": list(ablation_seeds), "needs_per_seed": n_needs,
                 "llm": "live" if live_hook else "stub", "assumptions_label": LABEL},
        "assumptions": A.to_json(),
        "agent": {"aggregate": aggregate_agent(agent_recs), "runs": agent_recs},
        "baseline": {"aggregate": _agg_baseline(baseline_recs), "runs": baseline_recs},
        "comparison": comparison,
        "scenarios": _agg_scenarios(scenario_recs),
        "ablations": ablation_out,
    }
    timings = {"total_s": round(t_total, 2), "main_s": round(t_main, 2), "agent_wall_s": stats_of(walls)}
    return {"results": results, "timings": timings}


def _agg_baseline(recs: list[dict[str, Any]]) -> dict[str, Any]:
    errs: Counter[str] = Counter()
    sl: Counter[str] = Counter()
    for r in recs:
        errs.update(r["errors"])
        for k in ("size", "bad", "hard_bad", "sanctioned", "forced_labor", "expired_cert", "duplicates", "true_positives"):
            sl[k] += r["shortlist_quality"][k]
        sl["awarded_bad"] += int(r["shortlist_quality"]["awarded_bad"])
    n = len(recs)
    return {"runs": n, "deal_rate": rate(sum(r["outcome"] == "DEAL" for r in recs), n),
            "elapsed_days": stats_of([r["elapsed_days"] for r in recs]), "human_touches": stats_of([r["human_touches"] for r in recs]),
            "human_touches_total": sum(r["human_touches"] for r in recs), "error_events": dict(sorted(errs.items())),
            "error_events_total": sum(errs.values()),
            "shortlist": {**dict(sl), "precision": rate(sl["true_positives"], sl["size"])}}


def _compare(agent: list[dict[str, Any]], base: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = list(zip(agent, base, strict=True))
    speed = [b["elapsed_days"] / a["sim"]["total_days"] for a, b in pairs if a["sim"]["total_days"] > 0]
    touch = [(a["human_touches"], b["human_touches"]) for a, b in pairs]
    return {
        "elapsed_speedup_x": stats_of(speed),
        "agent_sim_days_mean": stats_of([a["sim"]["total_days"] for a, _ in pairs])["mean"],
        "baseline_days_mean": stats_of([b["elapsed_days"] for _, b in pairs])["mean"],
        "touch_reduction_pct": round(100 * (1 - sum(a for a, _ in touch) / max(1, sum(b for _, b in touch))), 1),
        "agent_touches_never_exceed_baseline": all(a <= b for a, b in touch),
        "bad_in_shortlist": {"agent": sum(a["shortlist_quality"]["bad"] for a, _ in pairs),
                             "baseline": sum(b["shortlist_quality"]["bad"] for _, b in pairs)},
        "duplicates_in_shortlist": {"agent": sum(a["shortlist_quality"]["duplicates"] for a, _ in pairs),
                                    "baseline": sum(b["shortlist_quality"]["duplicates"] for _, b in pairs)},
        "bad_awarded": {"agent": sum(int(a["shortlist_quality"]["awarded_bad"]) for a, _ in pairs),
                        "baseline": sum(int(b["shortlist_quality"]["awarded_bad"]) for _, b in pairs)},
        "per_need": [{"seed": a["seed"], "need": a["need"], "category": a["category"], "agent_outcome": a["outcome"],
                      "agent_days": a["sim"]["total_days"], "baseline_days": b["elapsed_days"],
                      "agent_touches": a["human_touches"], "baseline_touches": b["human_touches"],
                      "agent_bad": a["shortlist_quality"]["bad"], "baseline_bad": b["shortlist_quality"]["bad"],
                      "baseline_errors": b["error_events"], "agent_dups": a["shortlist_quality"]["duplicates"],
                      "baseline_dups": b["shortlist_quality"]["duplicates"]} for a, b in pairs],
    }


def _agg_scenarios(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for kind in CONTROL_SCENARIOS:
        rs = [r for r in recs if r["scenario"] == kind]
        if not rs:
            continue
        oc = Counter(r["outcome"] for r in rs)
        out.append({"scenario": kind, "runs": len(rs), "outcomes": dict(sorted((str(k), v) for k, v in oc.items())),
                    "policy_blocks": sum(r["policy_blocks"] for r in rs), "escalations": sum(r["escalations"] for r in rs),
                    "bad_shortlisted": sum(r["safety"]["bad_shortlisted"] for r in rs),
                    "floor_breaches_delivered": sum(r["safety"]["floor_breaches_delivered"] for r in rs),
                    "unprovable_claims_delivered": sum(r["safety"]["unprovable_claims_delivered"] for r in rs),
                    "locked_redlines_delivered": sum(r["safety"]["locked_redlines_delivered"] for r in rs),
                    "news_risk_escaped": sum(r["safety"]["news_risk_escaped"] for r in rs),
                    "chains_valid": all(r["audit_chain_valid"] for r in rs)})
    return out
