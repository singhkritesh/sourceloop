"""Demo installs find, quote and award only suppliers someone can sign in as: an empty pool finds nobody, never everybody."""
from sourceloop.data.enums import OutcomeEnum
from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion


async def _run(**kw):  # noqa: ANN003
    rt = build_project(ScenarioSpec(seed=42, need_index=0, **kw))
    return rt, await run_to_completion(rt)


async def test_only_pool_suppliers_can_win_and_an_empty_pool_finds_nobody():
    _, st = await _run()
    assert st.outcome is OutcomeEnum.DEAL and st.awarded
    winner = st.awarded
    rt, st = await _run(supplier_pool=frozenset({winner}))
    assert st.outcome is OutcomeEnum.DEAL and st.awarded == winner
    assert {s for s in st.deals} <= {winner}
    rt, st = await _run(supplier_pool=frozenset({"S-NOT-IN-THE-REGISTRY"}))
    assert st.outcome is not OutcomeEnum.DEAL and st.awarded is None
    rt, st = await _run(supplier_pool=frozenset())
    assert st.outcome is not OutcomeEnum.DEAL and st.awarded is None
