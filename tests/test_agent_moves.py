"""Agents choose their moves with a model, from a menu the code builds (never a price of its own); every choice is audited without prices."""
import json
from decimal import Decimal as D

from sourceloop.agents.buyer.agent import BuyerAgent
from sourceloop.agents.common.deliberate import Move, RuleDeliberator
from sourceloop.data.synthetic import generate
from sourceloop.llm.client import LLMClient
from sourceloop.llm.config import stub_config
from sourceloop.llm.providers import StubProvider
from sourceloop.orchestrator.runtime import RunExecutor
from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion
from api_support import w  # noqa: F401
from sourceloop.subagents.deliberation import LLMDeliberator

VIS, FULL, _ = generate(42)
MOVES = [Move("COUNTER_STEADY", "counter", D("90"), "steady"), Move("COUNTER_FIRM", "counter", D("85"), "firm"), Move("WALK", "walk", None, "walk")]


def deliberator(masses=None, broken=False):
    class NoMass(StubProvider):
        first_token_mass = None  # type: ignore[assignment]
    prov = NoMass() if broken else StubProvider(choice_handlers={"negotiating agent": lambda u: masses} if masses else {})
    d = LLMDeliberator(LLMClient(stub_config(), {"stub": prov}), "P1")
    got: list[dict] = []

    async def sink(rec):
        got.append(rec)
    d.sink = sink
    return d, got


async def test_a_confident_model_choice_is_used_and_audited_without_prices():
    d, got = deliberator({"A": 0.05, "B": 0.9, "C": 0.05})
    p = await d.pick("buyer", "S1", 2, ["round 2"], MOVES)
    assert (p.source, p.label, p.index, p.agrees_with_rule, p.fallback) == ("model", "COUNTER_FIRM", 1, False, None)
    assert got[0]["chosen"] == "COUNTER_FIRM" and got[0]["rule"] == "COUNTER_STEADY" and got[0]["options"] == ["COUNTER_STEADY", "COUNTER_FIRM", "WALK"]
    assert "90" not in json.dumps(got) and "85" not in json.dumps(got)   # codes and probabilities only


async def test_an_unsure_or_off_menu_model_falls_back_to_the_rule_move():
    for masses, why in (({"A": 0.34, "B": 0.33, "C": 0.33}, "LOW_CONFIDENCE"), ({"A": 0.02, "B": 0.01, "C": 0.0}, "OFF_MENU")):
        d, got = deliberator(masses)
        p = await d.pick("buyer", "S1", 1, ["x"], MOVES)
        assert (p.source, p.label, p.fallback) == ("rule", "COUNTER_STEADY", why) and got[0]["source"] == "rule"


async def test_a_model_that_cannot_answer_never_blocks_the_negotiation():
    d, got = deliberator(broken=True)
    p = await d.pick("buyer", "S1", 1, ["x"], MOVES)
    assert p.source == "rule" and p.label == "COUNTER_STEADY" and p.fallback.startswith("UNAVAILABLE") and got[0]["fallback"]


async def test_the_buyers_menu_is_the_codes_and_stays_inside_the_limits():
    from sourceloop.bus.bus import MessageBus  # noqa: F401
    rt = build_project(ScenarioSpec(), VIS, FULL)
    b: BuyerAgent = rt.buyer
    for last_sup, mine, rnd in ((D("120"), None, 1), (D("110"), D("100"), 2), (D("101"), D("100"), 3), (D("150"), D("120"), 4)):
        mv = b.moves(last_sup, mine, rnd)
        assert mv[0].kind == b.decide(last_sup, mine, rnd)[0]                      # the rule's own move is always first
        for m in mv:
            if m.kind == "counter":
                assert m.price is not None and m.price <= b.max_price and m.price < last_sup and (mine is None or m.price > mine)
        assert len({(m.kind, m.price) for m in mv}) == len(mv)                     # no duplicate options
    assert isinstance(b.deliberator, RuleDeliberator)                              # default (tests, replay): the rule-based move


async def test_a_model_driven_negotiation_runs_end_to_end_with_audited_decisions_and_the_judge_still_in_charge():
    rt = build_project(ScenarioSpec(), VIS, FULL)
    prov = StubProvider(choice_handlers={"negotiating agent": lambda u: {"B": 0.9, "A": 0.05, "C": 0.05}})
    d = LLMDeliberator(LLMClient(stub_config(), {"stub": prov}), rt.project_id)
    rt.deliberator, rt.buyer.deliberator = d, d
    make = rt.make_supplier

    def patched(sid):
        sr = make(sid)
        sr.agent.deliberator = d
        return sr
    rt.make_supplier = patched
    RunExecutor(rt)   # wires the audit sink
    st = await run_to_completion(rt)
    recs = await rt.audit.store.read(rt.project_id)
    decisions = [r for r in recs if r.action == "agent_decision"]
    assert decisions and {r.evidence[0]["agent"] for r in decisions} >= {"buyer"}
    assert all(r.actor_type == "agent" and r.outcome in ("MODEL", "RULE") for r in decisions)
    assert any(r.outcome == "MODEL" and not r.evidence[0]["agrees_with_rule"] for r in decisions)   # the model really moved something
    assert [r for r in recs if r.action == "negotiation_judged"]                                       # every turn still judged by code
    assert (await rt.audit.verify(rt.project_id))[0] and st.status.value in ("DONE", "STOPPED")
    # nothing the model did could cross a limit: every delivered price is inside the sender's own mandate
    ceiling = rt.buyer_mandate.max_unit_price
    checked = 0
    for e in rt.bus.log:
        if e.payload.get("unit_price") is None:
            continue
        price = D(str(e.payload["unit_price"]))
        if e.from_agent == "buyer":
            assert price <= ceiling
        else:
            assert price >= rt.suppliers[e.from_agent.split(":", 1)[1]].mandate.min_unit_price
        checked += 1
    assert checked > 3


async def test_agent_performance_endpoint_reports_model_vs_rule_without_prices_and_is_scoped(w):  # noqa: F811
    from api_support import create_and_start, run_to_done
    pid = await create_and_start(w)
    await run_to_done(w, pid)
    ev = await w.client("evaluator")
    r = await ev.get(f"/projects/{pid}/agent-performance")
    assert r.status_code == 200 and r.json()["agents"] and r.json()["live_model_decisions"] is False   # replay run: rule-based, nothing from a model
    assert (await (await w.client("buyer")).get(f"/projects/{pid}/agent-performance")).status_code == 403   # business users do not see agent internals
    assert (await (await w.client("supplier1")).get(f"/projects/{pid}/agent-performance")).status_code == 403
