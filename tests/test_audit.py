from sourceloop.audit.chain import AuditLog, MemoryAuditStore, verify_chain
from sourceloop.clock import FixedClock


async def test_chain_verifies_and_detects_tamper():
    store = MemoryAuditStore()
    log = AuditLog(store, FixedClock())
    await log.append_many("p", [dict(actor_type="orchestrator", actor_id="o", action=f"a{i}") for i in range(5)])
    await log.append("p", actor_type="human", actor_id="u1", action="approve", approver_id="u1")
    assert await log.verify("p") == (True, None)
    store.tamper("p", 3, outcome="EDITED")
    ok, bad = await log.verify("p")
    assert ok is False and bad == 3


async def test_projects_have_independent_chains_and_resume_head():
    store = MemoryAuditStore()
    log = AuditLog(store, FixedClock())
    await log.append("a", actor_type="x", actor_id="x", action="1")
    await log.append("b", actor_type="x", actor_id="x", action="1")
    log2 = AuditLog(store, FixedClock())  # fresh process: must continue the chain from the store
    r = await log2.append("a", actor_type="x", actor_id="x", action="2")
    assert r.seq == 2
    assert verify_chain(await store.read("a")) == (True, None)


async def test_listener_runs_after_durable_write():
    store = MemoryAuditStore()
    log = AuditLog(store, FixedClock())
    seen = []

    async def fn(rec):
        seen.append((rec.seq, len(await store.read(rec.project_id))))

    log.subscribe(fn)
    await log.append("p", actor_type="x", actor_id="x", action="1")
    assert seen == [(1, 1)]
