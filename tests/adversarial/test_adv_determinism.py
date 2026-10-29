"""ADV-17: determinism. Same seed => identical final audit hash, independent of run count, hash seed or scheduling."""
import ast
import json
import os
import subprocess
import sys
import textwrap
from decimal import Decimal
from pathlib import Path

import pytest

from sourceloop.agents.supplier.agent import SupplierProfile
from sourceloop.data.enums import ApprovalKindEnum as AK
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.synthetic import generate
from sourceloop.orchestrator.scenario import Resolver, ScenarioSpec, build_project, run_to_completion

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "sourceloop"
VIS, FULL, _KEY = generate(42)


async def final_hash(spec: ScenarioSpec, resolver=None, pid="P1") -> str:
    rt = build_project(spec, VIS, FULL, project_id=pid)
    await run_to_completion(rt, resolver)
    recs = await rt.audit.store.read(pid)
    assert await rt.audit.verify(pid) == (True, None)
    return recs[-1].hash


async def test_adv17_five_identical_runs_have_identical_final_hash():
    hashes = {await final_hash(ScenarioSpec()) for _ in range(5)}
    assert len(hashes) == 1


async def test_adv17_adversarial_scenarios_are_deterministic_too():
    base = build_project(ScenarioSpec(), VIS, FULL)
    st = await run_to_completion(base)
    victim, winner = st.shortlist[0], st.awarded

    def specs():
        return [
            (ScenarioSpec(profiles={victim: SupplierProfile(attempt_below_floor=True)}), None),
            (ScenarioSpec(profiles={st.shortlist[1]: SupplierProfile(inflate_claims=True)}), None),
            (ScenarioSpec(profiles={winner: SupplierProfile(redline_locked_clause="liability")}),
             Resolver(decisions={AK.CLAUSE: (AS.EDITED, {"accept_change": True}, "Legal accepts the capped variant")})),
            (ScenarioSpec(buyer_max_factor=Decimal("0.5"), buyer_target_factor=Decimal("0.4")), None),
            (ScenarioSpec(poisoned_news=frozenset({"VN", "CN", "DE", "MX"})), None),
        ]

    first = [await final_hash(s, r) for s, r in specs()]
    for _ in range(4):
        assert [await final_hash(s, r) for s, r in specs()] == first
    assert len(set(first)) == len(first)  # and the scenarios really differ from each other


async def test_adv17_different_inputs_give_different_hashes():
    assert await final_hash(ScenarioSpec()) != await final_hash(ScenarioSpec(need_index=1))
    assert await final_hash(ScenarioSpec()) != await final_hash(ScenarioSpec(), pid="P2")
    assert await final_hash(ScenarioSpec()) != await final_hash(ScenarioSpec(margin_pct=Decimal("20")))


_PROG = textwrap.dedent("""
    import asyncio, json
    from decimal import Decimal
    from sourceloop.data.synthetic import generate
    from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion
    vis, full, _ = generate(42)
    async def h(spec):
        rt = build_project(spec, vis, full)
        await run_to_completion(rt)
        return (await rt.audit.store.read("P1"))[-1].hash
    async def main():
        return [await h(ScenarioSpec()), await h(ScenarioSpec(need_index=3)),
                await h(ScenarioSpec(poisoned_news=frozenset({"VN", "CN"}))),
                await h(ScenarioSpec(buyer_max_factor=Decimal("0.5")))]
    print(json.dumps(asyncio.run(main())))
""")


def _run_with_hashseed(seed: str) -> list[str]:
    env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT / "src")}
    out = subprocess.run([sys.executable, "-c", _PROG], env=env, cwd=str(ROOT), capture_output=True, text=True, timeout=60, check=True)  # noqa: S603
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_adv17_final_hash_is_independent_of_python_hash_randomisation():
    """set/dict ordering differences between interpreter runs must never reach the audit chain."""
    results = [_run_with_hashseed(s) for s in ("0", "1", "4242")]
    assert results[0] == results[1] == results[2]


# ---- static: decision code must not read wall-clock time, global RNG or fresh UUIDs
DECISION_PKGS = ["orchestrator", "bus", "policy", "compliance", "agents", "subagents", "audit"]
NONDET_CALLS = {("datetime", "now"), ("datetime", "utcnow"), ("date", "today"), ("time", "time"), ("time", "monotonic"),
                ("uuid", "uuid4"), ("uuid", "uuid1"), ("random", "random"), ("random", "randint"), ("random", "choice"),
                ("random", "shuffle"), ("random", "sample"), ("random", "uniform"), ("os", "urandom"), ("secrets", "token_hex")}


def test_adv17_no_wall_clock_or_unseeded_randomness_in_decision_code():
    offenders = []
    for pkg in DECISION_PKGS:
        for f in sorted((SRC / pkg).rglob("*.py")):
            for n in ast.walk(ast.parse(f.read_text())):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name):
                    if (n.func.value.id, n.func.attr) in NONDET_CALLS:
                        offenders.append(f"{f.relative_to(SRC)}:{n.lineno} {n.func.value.id}.{n.func.attr}")
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in {"uuid4", "uuid1"}:
                    offenders.append(f"{f.relative_to(SRC)}:{n.lineno} {n.func.id}()")
    assert not offenders, offenders


async def test_adv17_audit_timestamps_come_from_the_injected_clock():
    rt = build_project(ScenarioSpec(), VIS, FULL)
    await run_to_completion(rt)
    recs = await rt.audit.store.read("P1")
    assert recs[0].ts.year == 2026 and recs[0].ts.month == 10 and recs[0].ts.day == 20
    assert all(a.ts < b.ts for a, b in zip(recs, recs[1:], strict=False))


async def test_adv17_message_ids_are_deterministic_functions_of_project_and_counter():
    rt1 = build_project(ScenarioSpec(), VIS, FULL)
    rt2 = build_project(ScenarioSpec(), VIS, FULL)
    await run_to_completion(rt1)
    await run_to_completion(rt2)
    assert [e.id for e in rt1.bus.log] == [e.id for e in rt2.bus.log]
    assert [e.signature for e in rt1.bus.log] == [e.signature for e in rt2.bus.log]


@pytest.mark.parametrize("i", range(0, 12, 4))
async def test_adv17_every_need_is_reproducible(i):
    assert await final_hash(ScenarioSpec(need_index=i)) == await final_hash(ScenarioSpec(need_index=i))
