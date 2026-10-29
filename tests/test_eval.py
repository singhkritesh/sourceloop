import asyncio
import json
from pathlib import Path

import pytest

import sourceloop.bus.bus as bus_mod
import sourceloop.subagents.buyer_tools as bt
from sourceloop.eval import ablations as ab
from sourceloop.eval.assumptions import LABEL, Assumptions
from sourceloop.eval.baseline import SimClock, run_baseline
from sourceloop.eval.report import render_markdown, results_hash
from sourceloop.eval.suite import run_suite
from sourceloop.eval.world import build_world
from sourceloop.orchestrator.runtime import RunExecutor

EVAL_SRC = Path(__file__).resolve().parents[1] / "src" / "sourceloop" / "eval"


@pytest.fixture(scope="module")
def suite():
    return asyncio.run(run_suite(seeds=(42,)))


def test_same_seed_identical_report_json():
    a = asyncio.run(run_suite(seeds=(42,), n_needs=4))["results"]
    b = asyncio.run(run_suite(seeds=(42,), n_needs=4))["results"]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert results_hash(a) == results_hash(b)


def test_different_seed_changes_report():
    a = asyncio.run(run_suite(seeds=(42,), n_needs=3, run_ablations=False))["results"]
    b = asyncio.run(run_suite(seeds=(43,), n_needs=3, run_ablations=False))["results"]
    assert results_hash(a) != results_hash(b)


def test_baseline_deterministic_and_uses_injected_clock():
    w = build_world(42)
    A = Assumptions()
    r1, r2 = run_baseline(w, 0, A, SimClock()), run_baseline(w, 0, A, SimClock())
    assert r1 == r2 and r1["elapsed_days"] > 0 and r1["human_touches"] == sum(s["touches"] for s in r1["steps"])
    assert r1["elapsed_days"] == pytest.approx(sum(s["days"] for s in r1["steps"]), abs=0.01)


def test_agent_vs_baseline_invariants(suite):
    res = suite["results"]
    for a in res["agent"]["runs"]:
        assert a["audit_chain_valid"]
        assert a["human_touches_routine"] <= 5  # requirement, technical, award, signature, bank details
        sq = a["shortlist_quality"]
        assert sq["hard_bad"] == 0 and sq["sanctioned"] == 0 and sq["forced_labor"] == 0  # blocked classes never escape
        assert sq["bad"] == 0 and sq["duplicates"] == 0
        if sq["size"]:
            assert sq["precision"] == 1.0
    assert res["agent"]["aggregate"]["qualification"]["precision"] == 1.0
    cmp_ = res["comparison"]
    assert cmp_["agent_touches_never_exceed_baseline"]
    assert cmp_["agent_sim_days_mean"] < cmp_["baseline_days_mean"]
    assert res["baseline"]["aggregate"]["error_events_total"] > 0  # the modeled skimming human does err
    assert cmp_["bad_in_shortlist"]["baseline"] >= cmp_["bad_in_shortlist"]["agent"] == 0


@pytest.mark.parametrize("key", [a.key for a in ab.ABLATIONS])
def test_each_ablation_produces_its_bad_outcome_and_control_does_not(suite, key):
    a = next(x for x in suite["results"]["ablations"] if x["key"] == key)
    assert a["bad_control"] == 0, "normal run must not show the bad outcome"
    assert a["bad_ablated"] > 0 and a["bad_outcome_appears"], a["totals"]
    assert a["examples"]


def test_ablations_restore_core_and_leave_normal_runs_clean(suite):
    orig = (bt.qualify, bt.resolve, bt.build, bus_mod.check, RunExecutor._gate)
    for x in ab.ABLATIONS:
        with x.patch():
            pass
    assert (bt.qualify, bt.resolve, bt.build, bus_mod.check, RunExecutor._gate) == orig
    agg = suite["results"]["agent"]["aggregate"]
    assert agg["shortlist"]["bad"] == 0 and agg["audit_chain_valid_all"]


def test_report_flags_modeled_numbers_as_assumptions(suite):
    data = {**suite, "results_hash": results_hash(suite["results"])}
    md = render_markdown(data)
    assert LABEL in md and "modeled" in md and "Ablations" in md and "Per-need outcomes" in md
    assert "(modeled)" in md


def test_eval_source_rules():
    for f in EVAL_SRC.glob("*.py"):
        src = f.read_text()
        assert "datetime.now(" not in src and "time.time(" not in src
        assert "float(" not in src or f.name == "assumptions.py"
