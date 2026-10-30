"""Negotiation judge: pure verdicts, audited on every turn, can drop a candidate, optional reviewer model can only add a concern."""
from decimal import Decimal as D

from api_support import w  # noqa: F401
from sourceloop.agents.buyer.agent import BuyerAgent
from sourceloop.data.synthetic import generate
from sourceloop.llm.providers import StubProvider
from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion
from sourceloop.orchestrator.scenario import stub_extract as _se  # noqa: F401
from sourceloop.policy.judge import Level, Turn, Verdict, combine, judge


def t(actor="buyer", action="counter", rnd=2, price=None, own=None, other=None, idx=D("100"), step=D("0.5")):
    return Turn(actor, action, rnd, 4, price and D(price), own and D(own), other and D(other), idx, step)


def codes(x: Turn) -> tuple[Level, tuple[str, ...]]:
    v = judge(x)
    return v.level, v.codes


def test_normal_bargaining_is_appropriate():
    assert codes(t(price="92", own="90", other="100")) == (Level.APPROPRIATE, ())
    assert codes(t("supplier", price="96", own="100", other="92")) == (Level.APPROPRIATE, ())
    assert codes(t(rnd=1, price="90", other="100")) == (Level.APPROPRIATE, ())


def test_moving_backwards_is_inappropriate_for_both_sides():
    assert codes(t(price="88", own="90", other="100"))[1] == ("J_REGRESSION",)
    assert codes(t("supplier", price="101", own="100", other="92"))[0] is Level.INAPPROPRIATE


def test_crossing_and_extreme_lowball_are_inappropriate_a_mild_lowball_is_a_concern():
    assert "J_CROSSED" in codes(t(price="100", own="90", other="100"))[1]
    assert "J_CROSSED" in codes(t("supplier", price="90", own="100", other="92"))[1]
    assert codes(t(rnd=1, price="40", other="100")) == (Level.INAPPROPRIATE, ("J_LOWBALL_EXTREME",))
    assert codes(t(rnd=1, price="60", other="100")) == (Level.CONCERN, ("J_LOWBALL",))


def test_stalling_token_moves_overpaying_and_early_exit_are_concerns():
    assert codes(t(price="90", own="90", other="100")) == (Level.CONCERN, ("J_STALL",))
    assert codes(t(price="90.1", own="90", other="100")) == (Level.CONCERN, ("J_TOKEN_MOVE",))
    assert codes(t(action="accept", price="130", other="130")) == (Level.CONCERN, ("J_OVERPAY",))
    assert codes(t(action="accept", price="110", other="110")) == (Level.APPROPRIATE, ())
    assert codes(t(action="walk", rnd=2, own="90", other="100")) == (Level.CONCERN, ("J_EARLY_EXIT",))
    assert codes(t(action="walk", rnd=4, own="90", other="100")) == (Level.APPROPRIATE, ())
    assert codes(t("supplier", action="reject", rnd=1, other="92"))[0] is Level.APPROPRIATE   # a supplier may stop at its floor
    assert codes(Turn("buyer", "counter", 5, 4, D("91"), D("90"), D("100"), D("100"), D("0.5")))[1][0] == "J_ROUND_EXCEEDED"


def test_a_reviewer_model_can_only_tighten():
    ok, bad = Verdict(Level.APPROPRIATE), Verdict(Level.INAPPROPRIATE, ("J_CROSSED",))
    assert combine(ok, Level.INAPPROPRIATE) == Verdict(Level.CONCERN, ("J_LLM_REVIEW",))   # never ends a negotiation alone
    assert combine(ok, Level.APPROPRIATE) == ok
    assert combine(bad, Level.APPROPRIATE) == bad                                          # never softens the code verdict
    assert combine(bad, Level.CONCERN).level is Level.INAPPROPRIATE


async def _run(spec=None, patch=None):
    vis, full, _ = generate(42)
    rt = build_project(spec or ScenarioSpec(need_index=0), vis, full)
    await run_to_completion(rt)
    return rt, [r for r in await rt.audit.store.read("P1") if r.action.startswith("negotiation_judge")]


async def test_every_negotiation_turn_is_judged_and_audited_without_prices():
    rt, ev = await _run()
    judged = [r for r in ev if r.action == "negotiation_judged"]
    assert judged and {r.evidence[0]["actor"] for r in judged} >= {"buyer", "supplier"}
    assert all(set(r.evidence[0]) == {"supplier", "actor", "action", "round", "codes"} for r in judged)   # codes only: nothing to leak
    assert await rt.audit.verify("P1") == (True, None)


async def test_an_inappropriate_turn_drops_the_candidate_and_is_audited(monkeypatch):
    real = BuyerAgent.decide

    def regress(self, last_sup, my_last, rnd):
        a, p = real(self, last_sup, my_last, rnd)
        return (a, p - D("5")) if (a == "counter" and my_last is not None) else (a, p)   # a buyer that takes back a concession

    monkeypatch.setattr(BuyerAgent, "decide", regress)
    rt, ev = await _run()
    st = rt.state
    bad = [r for r in ev if r.outcome == "INAPPROPRIATE"]
    assert bad and bad[0].evidence[0]["codes"] == ["J_REGRESSION"]
    assert "JUDGE_INAPPROPRIATE" in st.dropped.values()


async def test_reviewer_model_adds_a_concern_but_never_ends_the_run():
    from sourceloop.orchestrator.runtime import RunConfig
    prov = {"stub": StubProvider({"supply-continuity risk": lambda u: _se(u)},
                                 choice_handlers={"negotiation": lambda u: {"A": 0.05, "B": 0.15, "C": 0.8}})}   # says: clearly inappropriate
    rt, ev = await _run(ScenarioSpec(need_index=0, run=RunConfig(judge_llm=True), llm_providers=prov))
    judged = [r for r in ev if r.action == "negotiation_judged"]
    assert judged and all(r.outcome == "CONCERN" and "J_LLM_REVIEW" in r.evidence[0]["codes"] and r.evidence[0]["llm_p_flag"] == 0.95 for r in judged)
    assert not any(v == "JUDGE_INAPPROPRIATE" for v in rt.state.dropped.values())   # concern only: the model alone never ends a negotiation
    assert rt.state.deals                                                            # and the negotiation still reached a deal


async def test_a_confident_appropriate_reviewer_changes_nothing():
    from sourceloop.orchestrator.runtime import RunConfig
    prov = {"stub": StubProvider({"supply-continuity risk": lambda u: _se(u)}, choice_handlers={"negotiation": lambda u: {"A": 0.9, "B": 0.1}})}
    _, ev = await _run(ScenarioSpec(need_index=0, run=RunConfig(judge_llm=True), llm_providers=prov))
    judged = [r for r in ev if r.action == "negotiation_judged"]
    assert judged and all("J_LLM_REVIEW" not in r.evidence[0]["codes"] and r.evidence[0]["llm_p_flag"] == 0.1 for r in judged)   # it added nothing


async def test_a_reviewer_that_does_not_answer_the_question_is_not_trusted():
    from sourceloop.orchestrator.runtime import RunConfig
    prov = {"stub": StubProvider({"supply-continuity risk": lambda u: _se(u)}, choice_handlers={"negotiation": lambda u: {"C": 0.2}})}   # 20% mass on options
    _, ev = await _run(ScenarioSpec(need_index=0, run=RunConfig(judge_llm=True), llm_providers=prov))
    judged = [r for r in ev if r.action == "negotiation_judged"]
    assert judged and all("J_LLM_REVIEW" not in r.evidence[0]["codes"] for r in judged)   # 20% of its mass on the options: ignored


async def test_a_failing_reviewer_model_is_recorded_and_the_code_verdict_stands():
    from sourceloop.orchestrator.runtime import RunConfig

    class NoLogprobs(StubProvider):   # a provider that cannot return option probabilities
        first_token_mass = None
    prov = {"stub": NoLogprobs({"supply-continuity risk": lambda u: _se(u)})}
    rt, ev = await _run(ScenarioSpec(need_index=0, run=RunConfig(judge_llm=True), llm_providers=prov))
    assert any(r.action == "negotiation_judge_llm_unavailable" for r in ev)
    assert any(r.action == "negotiation_judged" for r in ev)


async def test_models_are_unloaded_when_a_run_ends_whatever_the_way(w):  # noqa: F811
    """Completed, failed or paused for a human: the release hooks run every time (the model is not working)."""
    from api_support import create_and_start, pending, run_to_done
    calls: list[str] = []

    async def spy(model_name: str) -> None:
        calls.append(model_name)

    orig = w.service._build_runtime

    async def build(*a, **k):
        spec, rt, audit, release, cap = await orig(*a, **k)
        release.append(lambda: spy("m"))
        return spec, rt, audit, release, cap

    w.service._build_runtime = build
    pid = await create_and_start(w)               # runs until the first human checkpoint, i.e. PAUSED (ESCALATED), not finished
    assert calls, "released while the run waits for a person"
    n = len(calls)
    await run_to_done(w, pid)                     # finishes
    assert len(calls) > n
    assert pending is not None


async def test_release_runs_even_when_the_run_is_cancelled_or_crashes(w):  # noqa: F811
    from sourceloop.api.service import ProjectService
    from sourceloop.api.service import RunHandle as _H  # noqa: F401
    done: list[int] = []

    async def rel() -> None:
        done.append(1)

    class H:
        release = [rel]
    await ProjectService._release_models(H())
    assert done == [1]

    async def boom() -> None:
        raise OSError("ollama down")

    class H2:
        release = [boom, rel]
    await ProjectService._release_models(H2())    # a failing unload never raises and does not stop the others
    assert done == [1, 1]


# ---- need intake: in a live run the typed text, not a sample need, drives the run
async def test_need_intake_reads_the_typed_need_and_refuses_what_it_cannot_place():
    import pytest as _pt

    from sourceloop.llm.client import LLMClient
    from sourceloop.llm.config import stub_config
    from sourceloop.subagents.need_intake import NeedUnclear, read_need

    def llm(out):
        return LLMClient(stub_config(), {"stub": StubProvider(handlers={"purchase request": lambda u: out})})
    got = await read_need(llm({"category": "connectors", "volume_units": 2000, "required_certs": ["ISO9001", "ISO9001", "UL"],
                               "excluded_countries": ["RU"], "max_lead_time_weeks": 6, "target_unit_price": "12.5"}), "p1", "2,000 connectors, UL")
    assert got == {"category": "connectors", "industry": "high_tech", "required_certs": ["ISO9001", "UL"], "excluded_countries": ["RU"],
                   "volume_units": 2000, "max_lead_time_weeks": 6, "target_price": "12.5"}
    for bad in ({"category": "none"}, {"category": "laptops"}, {"category": "connectors", "required_certs": ["MADE_UP"]},
                {"category": "connectors", "volume_units": -5}, {"category": "connectors", "price": "cheap"}):
        with _pt.raises(NeedUnclear):
            await read_need(llm(bad), "p1", "whatever")
