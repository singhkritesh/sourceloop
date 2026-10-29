"""ADV-15: concurrency. Parallel projects must not share state; one project must not run twice."""
import asyncio
from collections import Counter

import pytest

from adversarial.helpers import offer
from sourceloop.audit.chain import AuditLog, MemoryAuditStore
from sourceloop.bus.bus import AgentChannel, Delivered, MessageBus
from sourceloop.clock import FixedClock
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.enums import ProjectStatusEnum as PS
from sourceloop.data.enums import StageEnum as S
from sourceloop.data.synthetic import generate
from sourceloop.orchestrator.runtime import RunExecutor
from sourceloop.orchestrator.scenario import Resolver, ScenarioSpec, build_project, run_to_completion
from sourceloop.orchestrator.transitions import IllegalTransition

VIS, FULL, _KEY = generate(42)
N = 6


async def _solo(i):
    rt = build_project(ScenarioSpec(need_index=i), VIS, FULL, project_id=f"P{i}")
    st = await run_to_completion(rt)
    recs = await rt.audit.store.read(f"P{i}")
    return st.outcome, st.awarded, recs[-1].hash, len(recs)


async def test_adv15_parallel_projects_match_their_isolated_runs():
    solo = [await _solo(i) for i in range(N)]
    shared_store = MemoryAuditStore()  # even a SHARED store/log must keep per-project chains apart
    audit = AuditLog(shared_store, FixedClock())
    rts = [build_project(ScenarioSpec(need_index=i), VIS, FULL, project_id=f"P{i}", audit=audit) for i in range(N)]
    states = await asyncio.gather(*[run_to_completion(rt) for rt in rts])
    for i, (rt, st) in enumerate(zip(rts, states, strict=True)):
        recs = await shared_store.read(f"P{i}")
        assert (st.outcome, st.awarded, len(recs)) == (solo[i][0], solo[i][1], solo[i][3])
        assert await audit.verify(f"P{i}") == (True, None)
        assert all(r.project_id == f"P{i}" for r in recs)
        assert all(e.project_id == f"P{i}" for e in rt.bus.log)


async def test_adv15_no_message_bleed_between_project_buses():
    ids = {}
    rts = [build_project(ScenarioSpec(need_index=i), VIS, FULL, project_id=f"P{i}") for i in range(3)]
    await asyncio.gather(*[RunExecutor(rt).run() for rt in rts])
    for i, rt in enumerate(rts):
        ids[i] = {e.id for e in rt.bus.log}
        assert ids[i]
    assert not (ids[0] & ids[1]) and not (ids[1] & ids[2])
    # a signed envelope from project 0 is useless against project 1
    foreign = rts[0].bus.log[0]
    res = await rts[1].bus.submit(foreign)
    assert getattr(res, "code", None) == "WRONG_PROJECT"
    assert foreign.id not in {e.id for e in rts[1].bus.log}


async def test_adv15_many_parallel_senders_get_contiguous_unique_sequence_numbers(bus, buyer, sup_a):
    async def send(i):
        # alternate sides; strictly increasing prices so step rules never interfere
        if i % 2:
            return await sup_a.send(T.REJECT, "buyer", {"reason_code": f"R{i}"})
        return await buyer.send(T.REJECT, "supplier:a", {"reason_code": f"R{i}"})

    res = await asyncio.gather(*[send(i) for i in range(200)])
    assert all(isinstance(r, Delivered) for r in res)
    seqs = sorted(r.envelope.seq for r in res)
    assert seqs == list(range(1, 201))
    assert await bus.audit.verify("p1") == (True, None)
    recs = await bus.audit.store.read("p1")
    assert [r.seq for r in recs] == list(range(1, len(recs) + 1))


async def test_adv15_concurrent_audit_appends_keep_one_valid_chain():
    log = AuditLog(MemoryAuditStore(), FixedClock())
    await asyncio.gather(*[log.append("p", actor_type="o", actor_id="o", action=f"a{i}") for i in range(300)])
    assert await log.verify("p") == (True, None)
    assert [r.seq for r in await log.store.read("p")] == list(range(1, 301))


async def test_adv15_interleaved_projects_on_one_log_do_not_cross_chains():
    log = AuditLog(MemoryAuditStore(), FixedClock())
    await asyncio.gather(*[log.append(f"p{i % 5}", actor_type="o", actor_id="o", action="x") for i in range(250)])
    for k in range(5):
        assert await log.verify(f"p{k}") == (True, None)
        assert len(await log.store.read(f"p{k}")) == 50


async def test_adv15_same_participants_in_two_projects_keep_separate_state(ring, buyer_mandate, supplier_mandate):
    """Same agent ids, same keys, two projects: replay ids, last-price memory, offers and waivers must not be shared."""
    def mk(pid):
        b = MessageBus(pid, AuditLog(MemoryAuditStore(), FixedClock()), ring, {"buyer": "buyer-org", "supplier:a": "sup-a"},
                       {"buyer": buyer_mandate, "supplier:a": supplier_mandate}, clock=FixedClock())
        b.set_stage(S.SOURCING)
        return b

    b1, b2 = mk("p1"), mk("p2")
    b1.waivers.add("PRICE_BAND")
    assert not b2.waivers
    r1 = await AgentChannel("supplier:a", b1).send(T.OFFER, "buyer", offer("100"))
    assert isinstance(r1, Delivered)
    # step memory: 100.1 right after 100 is blocked in p1 but the very first offer in p2
    assert getattr(await AgentChannel("supplier:a", b1).send(T.COUNTER, "buyer", offer("100.1", 2)), "rule_id", None) == "STEP_TOO_SMALL"
    assert isinstance(await AgentChannel("supplier:a", b2).send(T.OFFER, "buyer", offer("100.1")), Delivered)
    assert b1.inbox("buyer", drain=False) and len(b2.inbox("buyer", drain=False)) == 1
    assert not set(e.id for e in b1.log) & set(e.id for e in b2.log)


class _IOStore(MemoryAuditStore):
    """Yields to the event loop on every write, like a real database does."""

    async def write(self, records):
        await asyncio.sleep(0)
        await super().write(records)


async def test_adv15_double_start_of_the_same_project_is_idempotent_or_refused():
    rt = build_project(ScenarioSpec(), VIS, FULL, audit=AuditLog(_IOStore(), FixedClock()))
    res = Resolver()
    for _ in range(30):
        await asyncio.gather(RunExecutor(rt).run(), RunExecutor(rt).run(), return_exceptions=True)
        if rt.state.status is not PS.ESCALATED:
            break
        await res.resolve_pending(rt)
    kinds = Counter(e.type for e in rt.bus.log)
    assert kinds[T.BRIEF] == 1
    per_target = Counter(e.to_agent for e in rt.bus.log if e.type is T.INVITE)
    assert per_target and max(per_target.values()) == 1
    assert rt.state.status is PS.DONE


async def test_adv15_sequential_restart_of_a_finished_project_is_refused_and_leaves_no_trace():
    rt = build_project(ScenarioSpec(), VIS, FULL)
    await run_to_completion(rt)
    before = len(await rt.audit.store.read("P1"))
    with pytest.raises(IllegalTransition):
        await RunExecutor(rt).run()
    assert len(await rt.audit.store.read("P1")) == before and rt.state.status is PS.DONE


async def test_adv15_resume_after_pause_does_not_resend_completed_steps():
    rt = build_project(ScenarioSpec(), VIS, FULL)
    ex = RunExecutor(rt)
    await ex.run()
    await ex.run()  # unresolved approval: nothing may be re-sent
    await ex.run()
    assert Counter(e.type for e in rt.bus.log)[T.BRIEF] == 1
    assert await rt.audit.verify("P1") == (True, None)



async def test_adv15_second_concurrent_run_gets_a_clear_error_and_the_claim_is_released():
    from sourceloop.orchestrator.runtime import RunInProgress

    rt = build_project(ScenarioSpec(), VIS, FULL, audit=AuditLog(_IOStore(), FixedClock()))
    first, second = await asyncio.gather(RunExecutor(rt).run(), RunExecutor(rt).run(), return_exceptions=True)
    assert isinstance(second, RunInProgress) and not isinstance(first, Exception)
    assert rt.running is False  # claim released: a resume after the pause works
    await RunExecutor(rt).run()
