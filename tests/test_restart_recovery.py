"""Restart recovery by deterministic replay: an unfinished replay-mode project comes back at its checkpoint and finishes exactly like a run
that was never interrupted; anything that cannot be reproduced is closed as lost, never guessed."""
import asyncio
import uuid

from sqlalchemy import func, select, update

from api_support import ROLE_OF_KIND, create_and_start, pending, resolve_all, run_to_done, w  # noqa: F401
from sourceloop.data import db as dbm
from sourceloop.data.models import Approval, Message, Project, WatchlistEntry

NO_HOLD = {k for k in ROLE_OF_KIND if k != "HOLD_RELEASE"}


async def actions(w, pid):  # noqa: F811
    return [(e["action"], e["rule_id"]) for e in (await (await w.client("org_admin")).get(f"/projects/{pid}/audit?limit=500")).json()["items"]]


async def restart(w):  # noqa: F811
    """The process died: every in-memory runtime is gone; the next boot runs recover()."""
    w.service.handles.clear()
    return await w.service.recover()


async def status(w, pid):  # noqa: F811
    return (await (await w.client("buyer")).get(f"/projects/{pid}")).json()


async def valid(w, pid):  # noqa: F811
    return (await (await w.client("evaluator")).get(f"/projects/{pid}/audit/verify")).json()


async def finish(w, pid):  # noqa: F811
    for _ in range(40):
        proj = await run_to_done(w, pid)
        if proj["status"] in ("DONE", "STOPPED"):
            return proj
    return proj


async def test_parked_project_survives_restart_and_finishes_like_an_uninterrupted_run(w):  # noqa: F811
    ref = await create_and_start(w)
    assert (await finish(w, ref))["outcome"] == "DEAL"
    want = await actions(w, ref)

    pid = await create_and_start(w)
    assert (await status(w, pid))["status"] == "ESCALATED"
    assert await restart(w) == 0 and w.service.last_recovery == {"restored": 1, "lost": 0}
    got = await status(w, pid)
    assert got["status"] == "ESCALATED" and got["reason"] != "RESTART_LOST_RUNTIME"
    assert [a["kind"] for a in await pending(w, pid)] == ["REQUIREMENT"]          # the same checkpoint, still decidable
    assert (await valid(w, pid))["valid"] is True
    proj = await finish(w, pid)
    assert proj["status"] == "DONE" and proj["outcome"] == "DEAL"
    assert await actions(w, pid) == want                                           # same decisions, same events, same order
    assert (await valid(w, pid))["valid"] is True


async def test_restart_after_every_single_approval_changes_nothing(w):  # noqa: F811
    ref = await create_and_start(w)
    await finish(w, ref)
    want = await actions(w, ref)
    pid = await create_and_start(w)
    restarts = 0
    for _ in range(30):
        proj = await status(w, pid)
        if proj["status"] in ("DONE", "STOPPED"):
            break
        n = await resolve_all(w, pid, only=NO_HOLD)
        await w.service.join(pid)
        if n:
            await restart(w)
            restarts += 1
            assert w.service.last_recovery["lost"] == 0
    proj = await status(w, pid)
    assert restarts >= 4 and proj["status"] == "DONE" and proj["outcome"] == "DEAL"
    assert await actions(w, pid) == want and (await valid(w, pid))["valid"] is True


async def test_every_row_is_written_exactly_once_after_restore(w):  # noqa: F811
    pid = await create_and_start(w)
    await resolve_all(w, pid, only=NO_HOLD)
    await w.service.join(pid)
    async with dbm.get_sessionmaker()() as s:
        before = (await s.execute(select(func.count()).select_from(Message).where(Message.project_id == uuid.UUID(pid)))).scalar_one()
    await restart(w)
    async with dbm.get_sessionmaker()() as s:
        after = (await s.execute(select(func.count()).select_from(Message).where(Message.project_id == uuid.UUID(pid)))).scalar_one()
        seqs = list((await s.execute(select(Message.seq).where(Message.project_id == uuid.UUID(pid)).order_by(Message.seq))).scalars())
        n_appr = (await s.execute(select(func.count()).select_from(Approval).where(Approval.project_id == uuid.UUID(pid)))).scalar_one()
    assert after == before and seqs == list(range(1, after + 1))                    # no duplicate or missing message rows
    cores = [a["id"] for a in await pending(w, pid)]
    assert n_appr >= 2 and len(cores) == len(set(cores))


async def test_crash_in_the_middle_of_a_run_is_completed_to_the_next_checkpoint(w):  # noqa: F811
    ref = await create_and_start(w)
    await finish(w, ref)
    want = await actions(w, ref)
    b = await w.client("buyer")
    r = await b.post("/projects", {"title": "crash", "need_text": "x", "scenario": "happy"})
    pid = r.json()["id"]
    h = w.service.handles[pid]

    async def die(rec):  # the process is killed while the run is executing
        if rec.seq >= 9:
            raise asyncio.CancelledError

    h.audit.subscribe(die)
    await b.post(f"/projects/{pid}/start")
    await asyncio.gather(*[x for x in [h.task] if x], return_exceptions=True)
    n_audit = len(await w.service.store.read(pid))
    assert n_audit < len(want)                                                      # the durable chain stops mid-run
    await restart(w)
    assert w.service.last_recovery["lost"] == 0
    assert (await status(w, pid))["status"] == "ESCALATED" and (await valid(w, pid))["valid"] is True
    proj = await finish(w, pid)
    assert proj["status"] == "DONE" and await actions(w, pid) == want


async def test_never_started_draft_is_not_closed_by_a_restart(w):  # noqa: F811
    b = await w.client("buyer")
    pid = (await b.post("/projects", {"title": "d", "need_text": "x", "scenario": "happy"})).json()["id"]
    await restart(w)
    assert (await status(w, pid))["status"] == "DRAFT"
    assert (await b.post(f"/projects/{pid}/start")).status_code == 202               # runtime rebuilt lazily
    await w.service.join(pid)
    assert (await status(w, pid))["status"] == "ESCALATED"


# ------------------------------------------------------------------ what must NOT be restored
async def _lost_reason(w, pid):  # noqa: F811
    audit = (await (await w.client("org_admin")).get(f"/projects/{pid}/audit?limit=500")).json()["items"]
    assert audit[-1]["action"] == "run_lost_runtime"
    return audit[-1]["evidence"][0]["restore_failed"]


async def test_live_mode_run_is_closed_as_lost_not_guessed(w):  # noqa: F811
    pid = await create_and_start(w)
    async with dbm.session_scope() as s:
        await s.execute(update(Project).where(Project.id == uuid.UUID(pid)).values(llm_mode="live"))
    assert await restart(w) == 1
    got = await status(w, pid)
    assert (got["status"], got["outcome"], got["reason"]) == ("STOPPED", "FAILED", "RESTART_LOST_RUNTIME")
    assert await _lost_reason(w, pid) == "LIVE_MODE_NOT_REPRODUCIBLE" and await pending(w, pid) == []


async def test_a_decision_that_no_longer_replays_is_closed_as_lost(w):  # noqa: F811
    pid = await create_and_start(w)
    await resolve_all(w, pid, only=NO_HOLD)
    await w.service.join(pid)
    async with dbm.session_scope() as s:   # someone altered who made the first decision after the fact
        await s.execute(update(Approval).where(Approval.project_id == uuid.UUID(pid), Approval.core_id == "A0001").values(resolver_id="intruder"))
    assert await restart(w) == 1
    assert (await _lost_reason(w, pid)).startswith("REPLAY_DIVERGED_AT_")
    assert (await valid(w, pid))["valid"] is True                                    # the durable chain was never touched


async def test_changed_screening_input_is_closed_as_lost(w):  # noqa: F811
    pid = await create_and_start(w)
    async with dbm.session_scope() as s:   # a sanctions list was synced after the run started
        s.add(WatchlistEntry(name="Newly Listed Co", aliases=[], country="XX", list_name="OFAC_SDN"))
    assert await restart(w) == 1
    assert await _lost_reason(w, pid) == "WATCHLIST_CHANGED"


async def test_restoring_a_parked_run_writes_nothing_new_to_the_chain(w):  # noqa: F811
    pid = await create_and_start(w)
    n0 = len(await w.service.store.read(pid))
    w.service.handles.clear()
    h, why = await w.service._restore(uuid.UUID(pid))
    assert h is not None and why == ""
    assert len(await w.service.store.read(pid)) == n0                                # parked at the checkpoint: nothing to add, nothing lost
    assert (await valid(w, pid))["valid"] is True


# ------------------------------------------------------------------ the case that only real I/O timing exposes
def _simulated_profiles(monkeypatch):
    from sourceloop.api import service as svc
    real = svc.build_spec_from_config

    async def with_profiles(*a, **k):
        spec = await real(*a, **k)
        spec.profile_source = "simulated"
        return spec
    monkeypatch.setattr(svc, "build_spec_from_config", with_profiles)


async def _jittery_db(w, seed=7):  # noqa: F811
    """Random few-ms latency on the audit write AND on the per-event derived-row sync, like a busy database. The sync runs outside the audit
    lock, so with concurrent agent steps it changes which step resumes first: the real-server cause of replay divergence."""
    import random
    rng = random.Random(seed)  # noqa: S311 - latency jitter, not security
    real_write, real_sync = w.service.store.write, w.service.sync

    async def slow_write(records):
        await asyncio.sleep(rng.random() * 0.02)
        await real_write(records)

    async def slow_sync(h):
        await asyncio.sleep(rng.random() * 0.03)
        await real_sync(h)
    w.service.store.write, w.service.sync = slow_write, slow_sync


async def test_restore_matches_even_when_the_original_run_had_jittery_database_latency(w, monkeypatch):  # noqa: F811
    _simulated_profiles(monkeypatch)  # the shape of run that exposed the problem: no flagged suppliers, so every finalist negotiates
    ref = await create_and_start(w)
    await finish(w, ref)
    want = await actions(w, ref)
    await _jittery_db(w, seed=11)
    pid = await create_and_start(w)
    for _ in range(3):                     # past discovery and qualification, into sourcing / negotiation
        await resolve_all(w, pid, only=NO_HOLD)
        await w.service.join(pid)
    assert (await status(w, pid))["stage"] in ("SOURCING", "CONTRACTING", "ONBOARDING")
    await restart(w)
    assert w.service.last_recovery["lost"] == 0 and (await status(w, pid))["status"] != "STOPPED"
    proj = await finish(w, pid)
    assert proj["status"] == "DONE" and await actions(w, pid) == want and (await valid(w, pid))["valid"] is True


async def test_par_gather_is_concurrent_by_default_and_ordered_when_sequential():
    from sourceloop.agents.common import par

    order: list[str] = []

    async def job(name: str, delay: float):
        order.append(f"{name}-start")
        await asyncio.sleep(delay)
        order.append(f"{name}-end")
        return name

    assert await par.gather(job("a", 0.02), job("b", 0.0)) == ["a", "b"] and order == ["a-start", "b-start", "b-end", "a-end"]   # concurrent
    order.clear()
    tok = par.set_sequential(True)
    try:
        assert await par.gather(job("a", 0.02), job("b", 0.0)) == ["a", "b"] and order == ["a-start", "a-end", "b-start", "b-end"]  # one after another
        ran: list[str] = []

        async def boom():
            raise ValueError("x")

        async def never():
            ran.append("never")

        c = never()
        try:
            await par.gather(boom(), c)
        except ValueError:
            pass
        assert ran == [] and c.cr_frame is None                                    # the un-awaited coroutine was closed, not left dangling
    finally:
        par.reset_sequential(tok)
    assert not par.is_sequential()
