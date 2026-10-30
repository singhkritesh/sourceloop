"""End-to-end through the API: full run to DONE, persistence, locking, SSE, determinism, failure/resume, secrecy."""
import asyncio
import json
import uuid
from decimal import Decimal

from sqlalchemy import func, select

from api_support import (
    PASSWORD,
    Api,
    World,
    build_world,
    create_and_start,
    pending,
    resolve_all,
    run_to_done,
    w,  # noqa: F401
)
from sourceloop.api import sse
from sourceloop.api.broker import Broker
from sourceloop.api.deps import Principal
from sourceloop.data import db as dbm
from sourceloop.data.enums import ApprovalStatusEnum, HoldStatusEnum
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import (
    Approval,
    Contract,
    LedgerHold,
    Mandate,
    Message,
    Offer,
    Project,
    ProjectSupplier,
    SubagentCall,
)


def frames(text: str) -> list[dict]:
    out = []
    for block in text.split("\n\n"):
        lines = [x for x in block.split("\n") if x and not x.startswith(":")]
        if not lines:
            continue
        d = dict(line.split(": ", 1) for line in lines)
        out.append({"id": int(d["id"]), "event": d["event"], "data": json.loads(d["data"])})
    return out


async def test_full_run_through_api_persists_everything(w):  # noqa: F811
    pid = await create_and_start(w)
    b = await w.client("buyer")
    assert (await b.get(f"/projects/{pid}")).json()["status"] == "ESCALATED"
    proj = await run_to_done(w, pid)
    assert proj["status"] == "DONE" and proj["outcome"] == "DEAL" and proj["stage"] == "DONE"
    ev = await w.client("evaluator")
    v = (await ev.get(f"/projects/{pid}/audit/verify")).json()
    assert v["valid"] is True and v["first_bad_seq"] is None and v["events"] > 100
    # the audit rows read back through the API re-hash correctly and end with run_finished
    audit = (await (await w.client("org_admin")).get(f"/projects/{pid}/audit?limit=500")).json()["items"]
    assert audit[0]["action"] == "run_started" and audit[-1]["action"] == "run_finished"
    assert any(a["action"] == "ledger_hold_created" for a in audit)
    # derived rows
    async with dbm.get_sessionmaker()() as s:
        u = uuid.UUID(pid)
        h = w.service.handles[pid]
        assert (await s.execute(select(func.count()).select_from(Message).where(Message.project_id == u))).scalar_one() == len(h.rt.bus.log)
        offers = (await s.execute(select(Offer).where(Offer.project_id == u))).scalars().all()
        assert offers and all(isinstance(o.unit_price, Decimal) for o in offers)
        assert {o.side for o in offers} == {"supplier", "buyer"}
        c = (await s.execute(select(Contract).where(Contract.project_id == u))).scalar_one()
        assert c.status == "SIGNED" and c.signature_hash and "liability" in c.clauses
        assert (await s.execute(select(func.count()).select_from(SubagentCall).where(SubagentCall.project_id == u))).scalar_one() > 0
        llm_calls = [c for c in (await s.execute(select(SubagentCall).where(SubagentCall.project_id == u))).scalars() if c.model_id]
        assert llm_calls and all(c.output["llm"][0]["digest"] for c in llm_calls)  # provenance persisted (stub/replay digest)
        ps = (await s.execute(select(ProjectSupplier).where(ProjectSupplier.project_id == u))).scalars().all()
        assert ps and all(p.supplier_mandate_id for p in ps) and any(p.qual_status for p in ps)
        mandates = (await s.execute(select(Mandate))).scalars().all()
        assert {m.side for m in mandates} == {"buyer", "supplier"} and all(len(m.signature) == 64 for m in mandates)
        hold = (await s.execute(select(LedgerHold).where(LedgerHold.project_id == u))).scalar_one()
        assert hold.status is HoldStatusEnum.HELD
    # only the buyer user can release the hold (bank details are confirmed in the same decision); admins and other roles cannot
    left = await pending(w, pid)
    assert [a["kind"] for a in left] == ["HOLD_RELEASE"]
    url = f"/approvals/{left[0]['id']}/resolve"
    for who in ("finance", "org_admin", "admin", "evaluator"):
        assert (await (await w.client(who)).post(url, {"status": "APPROVED", "version": 1})).status_code in (403, 404), who
    assert (await (await w.client("buyer")).post(url, {"status": "APPROVED", "version": 1})).status_code == 200
    async with dbm.get_sessionmaker()() as s:
        hold = (await s.execute(select(LedgerHold).where(LedgerHold.project_id == uuid.UUID(pid)))).scalar_one()
        assert hold.status is HoldStatusEnum.RELEASED and hold.released_by == str(w.ids["buyer"])
    assert (await ev.get(f"/projects/{pid}/audit/verify")).json()["valid"] is True
    # a human decision is on the record with its approver
    approvals = (await ev.get(f"/projects/{pid}/escalations")).json()["items"]
    assert all(a["status"] != "PENDING" for a in approvals)
    resolved = [a for a in audit if a["action"] == "approval_resolved"]
    assert resolved and all(a["approver_id"] for a in resolved)


async def test_optimistic_lock_and_resolve_rules(w):  # noqa: F811
    pid = await create_and_start(w)
    b = await w.client("buyer")
    a = (await pending(w, pid))[0]
    url = f"/approvals/{a['id']}/resolve"
    r = await b.post(url, {"status": "APPROVED", "version": a["version"] + 5})
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_CONFLICT"
    r = await b.post(url, {"status": "REJECTED", "version": a["version"], "comment": "too short"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "COMMENT_REQUIRED"
    assert (await b.post(url, {"status": "EDITED", "version": a["version"]})).status_code == 422
    assert (await b.post(url, {"status": "PENDING", "version": a["version"]})).status_code == 422
    assert (await b.post(url, {"status": "APPROVED", "version": a["version"], "bogus": 1})).status_code == 422
    # two concurrent resolves of the same approval: exactly one wins
    rs = await asyncio.gather(b.post(url, {"status": "APPROVED", "version": a["version"]}),
                              b.post(url, {"status": "APPROVED", "version": a["version"]}))
    assert sorted(r.status_code for r in rs) == [200, 409]
    again = await b.post(url, {"status": "APPROVED", "version": a["version"] + 1})
    assert again.status_code == 409  # already resolved
    async with dbm.get_sessionmaker()() as s:
        row = await s.get(Approval, uuid.UUID(a["id"]))
        assert row.status is ApprovalStatusEnum.APPROVED and row.version == a["version"] + 1
    await w.service.join(pid)


async def test_reject_with_comment_stops_run_and_start_is_not_repeatable(w):  # noqa: F811
    pid = await create_and_start(w)
    b = await w.client("buyer")
    for _ in range(10):  # approve anything that is not the award; the award is the decision the buyer refuses
        a = (await pending(w, pid))[0]
        if a["kind"] == "AWARD":
            break
        assert (await b.post(f"/approvals/{a['id']}/resolve", {"status": "APPROVED", "version": a["version"]})).status_code == 200
        await w.service.join(pid)
    assert a["kind"] == "AWARD"
    r = await b.post(f"/approvals/{a['id']}/resolve", {"status": "REJECTED", "version": a["version"], "comment": "award is wrong"})
    assert r.status_code == 200
    await w.service.join(pid)
    proj = (await b.get(f"/projects/{pid}")).json()
    assert proj["status"] == "STOPPED" and proj["outcome"] == "STOPPED_BY_USER"
    assert (await b.post(f"/projects/{pid}/start")).status_code == 409


async def test_stop_resume_and_double_start(w):  # noqa: F811
    pid = await create_and_start(w)
    b = await w.client("buyer")
    assert (await b.post(f"/projects/{pid}/start")).status_code == 409  # not DRAFT any more
    assert (await b.post(f"/projects/{pid}/stop")).status_code == 200
    assert (await b.get(f"/projects/{pid}")).json()["status"] == "STOPPED"
    assert (await b.post(f"/projects/{pid}/stop")).status_code == 409
    assert (await b.post(f"/projects/{pid}/resume")).status_code == 409
    ev = await w.client("evaluator")
    assert (await ev.get(f"/projects/{pid}/audit/verify")).json()["valid"] is True
    audit = (await (await w.client("org_admin")).get(f"/projects/{pid}/audit?limit=500")).json()["items"]
    assert audit[-1]["action"] == "run_stopped" and audit[-1]["approver_id"] == str(w.ids["buyer"])


async def test_failure_is_recorded_rethrown_and_resumable(w):  # noqa: F811
    b = await w.client("buyer")
    pid = (await b.post("/projects", {"title": "t", "need_text": "n"})).json()["id"]
    h = w.service.handles[pid]
    original = h.rt.buyer.discover
    calls = {"n": 0}

    async def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return await original(*a, **kw)

    h.rt.buyer.discover = flaky
    assert (await b.post(f"/projects/{pid}/start")).status_code == 202
    await w.service.join(pid)
    await resolve_all(w, pid)  # REQUIREMENT approved -> run continues -> discovery blows up
    await w.service.join(pid)
    assert h.task.exception() is not None  # re-thrown, not swallowed
    proj = (await b.get(f"/projects/{pid}")).json()
    assert proj["status"] == "FAILED_RECOVERABLE"
    audit = (await (await w.client("org_admin")).get(f"/projects/{pid}/audit?limit=500")).json()["items"]
    assert any(a["action"] == "run_error" and a["rule_id"] == "RuntimeError" for a in audit)
    assert (await (await w.client("compliance")).post(f"/projects/{pid}/resume")).status_code == 403
    assert (await b.post(f"/projects/{pid}/resume")).status_code == 202
    await w.service.join(pid)
    assert (await b.get(f"/projects/{pid}")).json()["status"] == "ESCALATED"
    assert (await (await w.client("evaluator")).get(f"/projects/{pid}/audit/verify")).json()["valid"] is True


async def _make_unreproducible(pid: str) -> None:
    from sqlalchemy import update
    async with dbm.session_scope() as s:
        await s.execute(update(Project).where(Project.id == uuid.UUID(pid)).values(llm_mode="live"))


async def test_restart_closes_inflight_projects_as_lost_runtime(w):  # noqa: F811
    pid = await create_and_start(w)  # parked at ESCALATED
    await _make_unreproducible(pid)  # a live-mode run cannot be replayed (replay-mode runs are restored: tests/test_restart_recovery.py)
    fresh = type(w.service)(w.settings, Broker())
    assert await fresh.recover() >= 1
    b = await w.client("buyer")
    got = (await b.get(f"/projects/{pid}")).json()
    assert got["status"] == "STOPPED" and got["outcome"] == "FAILED" and got["reason"] == "RESTART_LOST_RUNTIME"
    async with dbm.get_sessionmaker()() as s:
        p = await s.get(Project, uuid.UUID(pid))
        assert p.reason == "RESTART_LOST_RUNTIME"


# ---------------------------------------------------------------- SSE
async def test_sse_finished_project_ordering_and_resume(w):  # noqa: F811
    pid = await create_and_start(w)
    await run_to_done(w, pid)
    r = await (await w.client("org_admin")).get(f"/projects/{pid}/stream")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
    fs = frames(r.text)
    audit_ids = [f["id"] for f in fs if f["event"] == "audit_event"]
    assert audit_ids == list(range(1, len(audit_ids) + 1))  # id = audit seq, strictly ordered, gap-free
    types = {f["event"] for f in fs}
    assert {"stage_changed", "message", "subagent_call", "subagent_result", "escalation_created", "approval_resolved",
            "audit_event", "run_finished"} <= types
    assert fs[-1]["event"] == "run_finished" or fs[-2]["event"] == "run_finished"
    assert all(f["data"]["seq"] == f["id"] for f in fs)
    assert [f["id"] for f in fs] == sorted(f["id"] for f in fs)
    stage_events = [f for f in fs if f["event"] == "stage_changed"]
    assert stage_events[0]["data"]["from"] == "NEED" and stage_events[0]["data"]["to"] == "NEED"
    esc = next(f for f in fs if f["event"] == "escalation_created")
    assert uuid.UUID(esc["data"]["approval_id"]) and esc["data"]["target_role"] == "BUYER"
    # resume: Last-Event-ID => only newer events, no duplicates, no gaps
    cut = audit_ids[len(audit_ids) // 2]
    r2 = await (await w.client("org_admin")).get(f"/projects/{pid}/stream", headers={"Last-Event-ID": str(cut)})
    fs2 = frames(r2.text)
    assert [f["id"] for f in fs2 if f["event"] == "audit_event"] == [i for i in audit_ids if i > cut]
    assert min(f["id"] for f in fs2) >= cut
    # caught up: nothing left, the stream closes immediately instead of hanging
    r3 = await (await w.client("org_admin")).get(f"/projects/{pid}/stream", headers={"Last-Event-ID": str(audit_ids[-1])})
    assert frames(r3.text) == []


async def _collect_until(gen, event: str, timeout: float = 15.0) -> list[str]:
    out: list[str] = []

    async def go() -> None:
        hit = False
        async for chunk in gen:
            out.append(chunk)
            hit = hit or f"event: {event}" in chunk
            if hit and "event: audit_event" in chunk:  # finish the seq: typed event then its audit_event frame
                return

    await asyncio.wait_for(go(), timeout)
    return out


async def test_sse_live_stream_reconnect_with_last_event_id(w):  # noqa: F811
    b = await w.client("buyer")
    pid = (await b.post("/projects", {"title": "t", "need_text": "n"})).json()["id"]

    def open_stream(after: int):
        async def never() -> bool:
            return False

        return sse.stream(service=w.service, broker=w.app.state.broker, pid=uuid.UUID(pid), user_key="t",
                          after_seq=after, supplier_view=None, is_disconnected=never)

    g1 = open_stream(0)
    t1 = asyncio.create_task(_collect_until(g1, "escalation_created"))
    await asyncio.sleep(0.05)
    await b.post(f"/projects/{pid}/start")  # live: events arrive while the run executes
    first = frames("".join(await t1))
    await g1.aclose()
    last = first[-1]["id"]
    assert [f["id"] for f in first if f["event"] == "audit_event"] == list(range(1, last + 1))
    # resolve the first checkpoint, reconnect with the last id we saw
    await resolve_all(w, pid)
    g2 = open_stream(last)
    second = frames("".join(await asyncio.wait_for(_collect_until(g2, "escalation_created"), 15)))
    await g2.aclose()
    seen = [f["id"] for f in first + second if f["event"] == "audit_event"]
    assert seen == list(range(1, len(seen) + 1)), "no duplicates and no gaps across the reconnect"
    assert all(f["id"] >= last for f in second)
    assert w.app.state.broker.streams["t"] == 0  # stream slots are released
    await w.service.join(pid)


async def test_supplier_sse_sees_only_own_traffic(w):  # noqa: F811
    pid = await create_and_start(w)
    await run_to_done(w, pid)
    sup = await w.client("supplier1")
    fs = frames((await sup.get(f"/projects/{pid}/stream")).text)
    mine = f"supplier:{w.demo_suppliers[0]}"
    assert {f["event"] for f in fs} <= {"stage_changed", "message", "run_finished", "audit_event"}
    assert all(mine in (f["data"]["message"]["from_agent"], f["data"]["message"]["to_agent"]) for f in fs if f["event"] == "message")
    msgs = (await sup.get(f"/projects/{pid}/messages?limit=500")).json()["items"]
    assert msgs and all(mine in (m["from_agent"], m["to_agent"]) for m in msgs)
    # AI disclosure is a view-layer flag derived from the sender (not part of the signed envelope / audit hash)
    assert all(m["ai_generated"] is (m["from_agent"] != "orchestrator") for m in msgs)
    assert any(m["ai_generated"] for m in msgs)
    assert all(f["data"]["message"]["ai_generated"] is (f["data"]["message"]["from_agent"] != "orchestrator")
               for f in fs if f["event"] == "message")
    out = await w.client("outsider")
    assert (await out.get(f"/projects/{pid}/stream")).status_code == 404


async def test_stream_limit_per_user(w):  # noqa: F811
    pid = await create_and_start(w)
    w.app.state.broker.streams[str(w.ids["org_admin"])] = 3
    r = await (await w.client("org_admin")).get(f"/projects/{pid}/stream")
    assert r.status_code == 429 and r.json()["error"]["code"] == "TOO_MANY_STREAMS"
    w.app.state.broker.streams[str(w.ids["org_admin"])] = 0


# ---------------------------------------------------------------- determinism
FIXED_PID = uuid.UUID("11111111-2222-4333-8444-555555555555")


async def _fixed_run(tmp_path, i: int) -> tuple[str, int]:
    world: World = await build_world(tmp_path / f"run{i}", fixed_ids=True)
    async with world.app.router.lifespan_context(world.app):
        async with dbm.get_sessionmaker()() as db:
            org = world.orgs["acme"]
            await world.service.create_project(db, Principal(world.ids["buyer"], "b", R.BUYER, org, "BUYER"), title="t",
                                               need_text="n", scenario="happy", need_index=0, seed=42, project_id=FIXED_PID)
        b = await world.client("buyer")
        assert (await b.post(f"/projects/{FIXED_PID}/start")).status_code == 202
        await world.service.join(FIXED_PID)
        proj = await run_to_done(world, str(FIXED_PID))
        assert proj["status"] == "DONE"
        v = (await (await world.client("evaluator")).get(f"/projects/{FIXED_PID}/audit/verify")).json()
        await world.close()
        return v["head_hash"], v["events"]


async def test_same_scenario_three_times_identical_audit_hash(tmp_path):
    results = [await _fixed_run(tmp_path, i) for i in range(3)]
    assert len({r[0] for r in results}) == 1 and results[0][1] > 100, results


# ---------------------------------------------------------------- secrets and secrecy
async def test_settings_masked_and_db_first(w, monkeypatch):  # noqa: F811
    a = await w.client("admin")
    r = await a.get("/settings")
    body = r.json()
    assert body["jwt_secret"] == "****" and body["org_signing_secret"] == "****" and body["database_url"] == "****"
    assert w.settings.jwt_secret not in r.text and w.settings.org_signing_secret not in r.text
    assert body["llm_mode"] == "replay" and body["token_budget_per_project"] == 400000  # env fallback
    r = await a.put("/settings", {"token_budget_per_project": 123456})
    assert r.status_code == 200 and r.json()["token_budget_per_project"] == 123456  # DB value wins
    assert (await a.put("/settings", {"llm_mode": "nonsense"})).status_code == 422
    assert (await a.put("/settings", {"jwt_secret": "x"})).status_code == 422  # secrets are not settable here
    assert (await a.get("/health")).json()["llm_mode"] == "replay"
    await a.put("/settings", {"llm_mode": "live"})
    assert (await a.get("/health")).json()["llm_mode"] == "live"

    async def unreachable(name, d, vault):  # noqa: ANN001 - the suite never talks to a real Ollama
        return {"ok": False, "models": [], "error": "UNREACHABLE"}
    monkeypatch.setattr("sourceloop.api.service.probe", unreachable)
    r = await (await w.client("buyer")).post("/projects", {"title": "t"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "LLM_PROVIDER_UNREACHABLE"  # live needs a reachable provider
    await a.put("/settings", {"llm_mode": "record"})
    r = await (await w.client("buyer")).post("/projects", {"title": "t"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "LLM_MODE_UNSUPPORTED"
    audit = (await (await w.client("evaluator")).get("/health")).status_code
    assert audit == 200


async def test_no_secrets_in_responses_or_logs(tmp_path, capsys):
    world = await build_world(tmp_path, log_level="INFO")
    async with world.app.router.lifespan_context(world.app):
        c = Api(world.app)
        await c.login(world.emails["buyer"], "wrong-password-value")
        r = await c.login(world.emails["buyer"])
        access, refresh, csrf = (c.c.cookies.get(k) for k in ("access_token", "refresh_token", "csrf"))
        pid = await create_and_start(world)
        await run_to_done(world, pid)
        texts = [r.text]
        for path in ("/auth/me", "/projects", f"/projects/{pid}", f"/projects/{pid}/messages?limit=500",
                     f"/projects/{pid}/audit?limit=500", f"/projects/{pid}/escalations", "/approvals"):
            texts.append((await (await world.client("buyer")).get(path)).text)
        blob = "\n".join(texts)
        for needle in ("password_hash", "argon2", PASSWORD, world.settings.jwt_secret, world.settings.org_signing_secret, access, refresh):
            assert needle not in blob, needle
        out = capsys.readouterr()
        logs = out.out + out.err
        assert '"event": "http"' in logs and '"event": "auth.fail"' in logs  # logging is on...
        for needle in (PASSWORD, "wrong-password-value", access, refresh, csrf, world.settings.jwt_secret,
                       world.settings.org_signing_secret, world.emails["buyer"]):
            assert needle not in logs, "secret leaked into logs"
        await c.close()
        await world.close()


async def test_mandates_never_cross_the_side_boundary(w):  # noqa: F811
    pid = await create_and_start(w)
    await run_to_done(w, pid)
    async with dbm.get_sessionmaker()() as s:
        mandates = (await s.execute(select(Mandate))).scalars().all()
    buyer_cap = next(m for m in mandates if m.side == "buyer").payload["max_unit_price"]
    costs = {m.payload["unit_cost"] for m in mandates if m.side == "supplier"}
    sup = await w.client("supplier1")
    sup_text = ""
    for path in ("", "/messages?limit=500", "/audit?limit=500"):
        sup_text += (await sup.get(f"/projects/{pid}{path}")).text
    sup_text += (await sup.get(f"/projects/{pid}/stream")).text
    assert buyer_cap not in sup_text and "max_unit_price" not in sup_text and "target_unit_price" not in sup_text
    b = await w.client("buyer")
    buyer_text = ""
    for path in ("", "/messages?limit=500", "/audit?limit=500", "/escalations"):
        buyer_text += (await b.get(f"/projects/{pid}{path}")).text
    buyer_text += (await (await w.client("org_admin")).get(f"/projects/{pid}/stream")).text
    assert "unit_cost" not in buyer_text and "min_unit_price" not in buyer_text
    assert not any(f'"{c}"' in buyer_text for c in costs)
    # registry search exposes public fields only
    found = (await b.get("/suppliers/search?limit=5")).json()["items"]
    assert set(found[0]) == {"id", "name", "country", "categories", "network_status", "years_in_business"} if found else True
