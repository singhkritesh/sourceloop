"""Regression tests for the second review round: F4 F5 F6 F7 F11/F12 F14 F16 F17 + the missing tests (b)-(f)."""
import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text, update

from api_support import (  # noqa: F401
    API,
    PASSWORD,
    Api,
    build_world,
    create_and_start,
    pending,
    resolve_all,
    run_to_done,
    w,
)
from sourceloop.api import sse
from sourceloop.api.broker import Broker
from sourceloop.api.config import load_settings
from sourceloop.api.deps import client_ip
from sourceloop.data import db as dbm
from sourceloop.data.models import Approval, AuditEvent, Project, RevokedToken


async def _status(w, pid):  # noqa: F811
    return (await (await w.client("buyer")).get(f"/projects/{pid}")).json()


async def _drain(resp_text: str) -> list[str]:
    return [ln for ln in resp_text.splitlines() if ln.startswith("event:")]


# ---------------------------------------------------------------- F4
async def test_registration_default_is_off_in_prod_on_in_dev(monkeypatch):
    for k in ("APP_ENV", "REGISTRATION_ENABLED", "JWT_SECRET", "ORG_SIGNING_SECRET", "SOURCELOOP_VAULT_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert load_settings().registration_enabled is True
    from cryptography.fernet import Fernet
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.test")
    monkeypatch.setenv("JWT_SECRET", "j" * 40)
    monkeypatch.setenv("ORG_SIGNING_SECRET", "s" * 40)
    monkeypatch.setenv("SOURCELOOP_VAULT_KEY", Fernet.generate_key().decode())
    assert load_settings().registration_enabled is False
    monkeypatch.setenv("REGISTRATION_ENABLED", "true")
    assert load_settings().registration_enabled is True


async def test_finished_project_handle_is_evicted(w):  # noqa: F811
    pid = await create_and_start(w)
    assert pid in w.service.handles
    b = await w.client("buyer")
    assert (await b.post(f"/projects/{pid}/stop")).status_code == 200
    assert pid not in w.service.handles  # terminal -> runtime dropped, DB is the record
    assert (await _status(w, pid))["status"] == "STOPPED"
    assert (await b.post(f"/projects/{pid}/stop")).status_code == 409


async def test_done_project_is_evicted_after_last_checkpoint(w):  # noqa: F811
    pid = await create_and_start(w)
    assert (await run_to_done(w, pid))["status"] == "DONE"
    assert pid in w.service.handles  # finance HOLD_RELEASE still open
    left = await pending(w, pid)
    fin = await w.client("buyer")
    assert (await fin.post(f"/approvals/{left[0]['id']}/resolve", {"status": "APPROVED", "version": left[0]["version"]})).status_code == 200
    assert pid not in w.service.handles


async def test_quota_exceeded_and_concurrent_creation_memory_bound(tmp_path):
    w2 = await build_world(tmp_path, max_active_projects_per_org=3)
    async with w2.app.router.lifespan_context(w2.app):
        b = await w2.client("buyer")
        rs = await asyncio.gather(*[b.post("/projects", {"title": f"p{i}", "need_text": "x", "scenario": "happy"}) for i in range(8)])
        codes = sorted(r.status_code for r in rs)
        assert codes.count(201) == 3 and codes.count(429) == 5, codes
        assert {r.json()["error"]["code"] for r in rs if r.status_code == 429} == {"QUOTA_EXCEEDED"}
        assert len(w2.service.handles) == 3  # memory bound: failed creations leave no handle behind
        other = await w2.client("buyer_b")  # per-org: another tenant is unaffected
        assert (await other.post("/projects", {"title": "q", "need_text": "x", "scenario": "happy"})).status_code == 201
        # finishing one frees quota
        pid = next(r.json()["id"] for r in rs if r.status_code == 201)
        assert (await b.post(f"/projects/{pid}/stop")).status_code == 200
        assert (await b.post("/projects", {"title": "again", "need_text": "x", "scenario": "happy"})).status_code == 201
        await w2.close()


async def test_baseline_is_cached_and_bounded(w):  # noqa: F811
    b = await w.client("buyer")
    calls = {"n": 0}
    import sourceloop.api.service as svc
    real = svc.run_to_completion

    async def counting(rt):
        calls["n"] += 1
        return await real(rt)
    svc.run_to_completion = counting
    try:
        for _ in range(2):
            r = await b.post("/projects", {"title": "t", "need_text": "x", "scenario": "below_floor"})
            assert r.status_code == 201, r.text
    finally:
        svc.run_to_completion = real
    assert calls["n"] <= 1  # second creation reuses the cached baseline


# ---------------------------------------------------------------- F5
async def test_resume_and_resolve_after_restart_are_runtime_unavailable(w):  # noqa: F811
    pid = await create_and_start(w)
    open_items = await pending(w, pid)
    assert open_items
    async with dbm.session_scope() as s:  # live-mode runs cannot be replayed; replay-mode runs are restored (tests/test_restart_recovery.py)
        await s.execute(update(Project).where(Project.id == uuid.UUID(pid)).values(llm_mode="live"))
    fresh = type(w.service)(w.settings, Broker())
    w.service.handles.clear()  # the process "restarted": no runtime in memory
    assert await fresh.recover() >= 1
    b = await w.client("buyer")
    got = await _status(w, pid)
    assert (got["status"], got["outcome"], got["reason"]) == ("STOPPED", "FAILED", "RESTART_LOST_RUNTIME")
    r = await b.post(f"/projects/{pid}/resume")
    assert r.status_code == 409 and r.json()["error"]["code"] == "RUNTIME_UNAVAILABLE"
    assert await pending(w, pid) == []  # dead inbox items are closed
    ev = await (await w.client("evaluator")).get(f"/projects/{pid}/audit/verify")
    assert ev.json()["valid"] is True
    audit = (await (await w.client("org_admin")).get(f"/projects/{pid}/audit?limit=500")).json()["items"]
    assert audit[-1]["action"] == "run_lost_runtime" and audit[-1]["rule_id"] == "RESTART_LOST_RUNTIME"
    async with dbm.get_sessionmaker()() as s:
        a = (await s.execute(select(Approval).where(Approval.project_id == uuid.UUID(pid)))).scalars().first()
    rr = await (await w.client("buyer")).post(f"/approvals/{a.id}/resolve", {"status": "APPROVED", "version": a.version})
    assert rr.status_code == 409 and rr.json()["error"]["code"] == "RUNTIME_UNAVAILABLE"


async def test_resolve_without_handle_is_runtime_unavailable(w):  # noqa: F811
    pid = await create_and_start(w)
    a = (await pending(w, pid))[0]
    w.service.handles.clear()
    r = await (await w.client("buyer")).post(f"/approvals/{a['id']}/resolve", {"status": "APPROVED", "version": a["version"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "RUNTIME_UNAVAILABLE"
    assert (await pending(w, pid))  # untouched


# ---------------------------------------------------------------- F7
async def test_logout_revocation_survives_restart(w):  # noqa: F811
    c = Api(w.app)
    assert (await c.login(w.emails["buyer"])).status_code == 200
    tok = c.c.cookies.get("access_token")
    assert (await c.get("/auth/me")).status_code == 200
    assert (await c.post("/auth/logout")).status_code == 204
    async with dbm.get_sessionmaker()() as s:
        assert (await s.execute(select(func.count()).select_from(RevokedToken))).scalar_one() == 1
    w.app.state.revoked_jti.clear()  # a restart forgets memory, not the table
    c2 = Api(w.app)
    c2.c.cookies.set("access_token", tok, path="/api/v1")
    r = await c2.get("/auth/me")
    assert r.status_code == 401
    await c.close()
    await c2.close()


async def test_expired_revocations_are_pruned(w):  # noqa: F811
    from sourceloop.clock import SystemClock
    now = SystemClock().now()
    async with dbm.session_scope() as s:
        s.add(RevokedToken(jti="old", expires_at=now - timedelta(hours=1), revoked_at=now - timedelta(hours=2)))
        s.add(RevokedToken(jti="live", expires_at=now + timedelta(hours=1), revoked_at=now))
    await w.service.prune_revoked()
    async with dbm.get_sessionmaker()() as s:
        left = set((await s.execute(select(RevokedToken.jti))).scalars())
    assert left == {"live"}


# ---------------------------------------------------------------- F6 + test (b)
async def _open_stream(w, who, pid):  # noqa: F811
    """Consume the stream in a task; returns (task, list of text chunks)."""
    c = await w.client(who)
    chunks: list[str] = []
    async def run():
        async with c.c.stream("GET", f"{API}/projects/{pid}/stream") as r:
            async for ch in r.aiter_text():
                chunks.append(ch)
    return asyncio.create_task(run()), chunks


async def test_sse_closes_after_logout(w, monkeypatch):  # noqa: F811
    monkeypatch.setattr(sse, "REVALIDATE_S", 0.2)
    pid = await create_and_start(w)
    c = Api(w.app)
    assert (await c.login(w.emails["org_admin"])).status_code == 200
    chunks: list[str] = []

    async def run():
        async with c.c.stream("GET", f"{API}/projects/{pid}/stream") as r:
            async for ch in r.aiter_text():
                chunks.append(ch)
    task = asyncio.create_task(run())
    await asyncio.sleep(0.6)
    assert not task.done()
    other = Api(w.app)  # logout with the same cookies (the revoke is by token jti)
    other.c.cookies.update(c.c.cookies)
    assert (await other.post("/auth/logout")).status_code == 204
    await asyncio.wait_for(task, 5)
    assert "UNAUTHENTICATED" in "".join(chunks)
    await c.close()
    await other.close()


async def test_sse_closes_after_role_change_or_supplier_unlink(w, monkeypatch):  # noqa: F811
    monkeypatch.setattr(sse, "REVALIDATE_S", 0.2)
    pid = await create_and_start(w)
    task, chunks = await _open_stream(w, "org_admin", pid)
    await asyncio.sleep(0.6)
    assert not task.done()
    async with dbm.session_scope() as s:
        await s.execute(text("update app_user set role='EVALUATOR' where email=:e"), {"e": w.emails["org_admin"]})
    await asyncio.wait_for(task, 5)
    assert "UNAUTHENTICATED" in "".join(chunks)


async def test_sse_closes_when_user_disabled(w, monkeypatch):  # noqa: F811
    monkeypatch.setattr(sse, "REVALIDATE_S", 0.2)
    pid = await create_and_start(w)
    task, chunks = await _open_stream(w, "org_admin", pid)
    await asyncio.sleep(0.6)
    async with dbm.session_scope() as s:
        await s.execute(text("delete from refresh_token where user_id=(select id from app_user where email=:e)"), {"e": w.emails["org_admin"]})
        await s.execute(text("delete from app_user where email=:e"), {"e": w.emails["org_admin"]})
    await asyncio.wait_for(task, 5)
    assert "UNAUTHENTICATED" in "".join(chunks)


# ---------------------------------------------------------------- F11 / F12
async def test_stop_writes_audit_before_mutating_even_without_handle(w):  # noqa: F811
    pid = await create_and_start(w)
    w.service.handles.clear()
    b = await w.client("buyer")
    assert (await b.post(f"/projects/{pid}/stop")).status_code == 200
    audit = (await (await w.client("org_admin")).get(f"/projects/{pid}/audit?limit=500")).json()["items"]
    assert audit[-1]["action"] == "run_stopped"
    assert (await _status(w, pid))["status"] == "STOPPED"
    assert (await (await w.client("evaluator")).get(f"/projects/{pid}/audit/verify")).json()["valid"] is True
    assert await pending(w, pid) == []


async def test_stop_audit_failure_leaves_project_unchanged(w, monkeypatch):  # noqa: F811
    pid = await create_and_start(w)
    h = w.service.handles[pid]

    async def boom(*a, **kw):
        raise RuntimeError("audit down")
    monkeypatch.setattr(h.audit, "append", boom)
    b = await w.client("buyer")
    with pytest.raises(RuntimeError):
        await b.post(f"/projects/{pid}/stop")
    assert h.rt.state.status.value == "ESCALATED" and (await _status(w, pid))["status"] == "ESCALATED"


async def test_settings_put_audits_first_and_compensates_on_failure(w, monkeypatch):  # noqa: F811
    a = await w.client("admin")
    assert (await a.put("/settings", {"token_budget_per_project": 5000})).status_code == 200
    async with dbm.get_sessionmaker()() as s:
        acts = list((await s.execute(select(AuditEvent.action).where(AuditEvent.project_id == "system").order_by(AuditEvent.seq))).scalars())
    assert acts[-1] == "settings.updated"

    async def bad(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(dbm, "upsert_setting", bad)
    with pytest.raises(RuntimeError):
        await a.put("/settings", {"token_budget_per_project": 7000})
    async with dbm.get_sessionmaker()() as s:
        acts = list((await s.execute(select(AuditEvent.action).where(AuditEvent.project_id == "system").order_by(AuditEvent.seq))).scalars())
    assert acts[-2:] == ["settings.updated", "settings.update_failed"]


async def test_resolve_db_cas_first_and_compensation(w, monkeypatch):  # noqa: F811
    pid = await create_and_start(w)
    a = (await pending(w, pid))[0]
    h = w.service.handles[pid]
    b = await w.client(_ROLE := {"REQUIREMENT": "buyer"}.get(a["kind"], "buyer"))
    # a stale DB version (someone else already resolved the row) -> 409 and the in-memory approval is untouched
    async with dbm.session_scope() as s:
        await s.execute(text("update approval set version = version + 1 where id=:i"), {"i": a["id"].replace("-", "")})
    called = {"n": 0}
    real = h.rt.approvals.resolve

    async def spy(*x, **kw):
        called["n"] += 1
        return await real(*x, **kw)
    monkeypatch.setattr(h.rt.approvals, "resolve", spy)
    r = await b.post(f"/approvals/{a['id']}/resolve", {"status": "APPROVED", "version": a["version"]})
    assert r.status_code == 409 and called["n"] == 0
    assert h.rt.approvals.pending(pid)
    # memory failure after the DB CAS -> row compensated back to PENDING
    async with dbm.session_scope() as s:
        await s.execute(text("update approval set version = version - 1 where id=:i"), {"i": a["id"].replace("-", "")})

    async def fail(*x, **kw):
        raise RuntimeError("mem")
    monkeypatch.setattr(h.rt.approvals, "resolve", fail)
    with pytest.raises(RuntimeError):
        await b.post(f"/approvals/{a['id']}/resolve", {"status": "APPROVED", "version": a["version"]})
    async with dbm.get_sessionmaker()() as s:
        row = await s.get(Approval, uuid.UUID(a["id"]))
    assert row.status.value == "PENDING" and row.version == a["version"]


# ---------------------------------------------------------------- F14
def _req(headers, peer="10.0.0.9"):
    class R:
        client = type("C", (), {"host": peer})()
        app = type("A", (), {"state": type("S", (), {"settings": None})()})()
    r = R()
    r.headers = headers
    return r


def test_client_ip_trusts_forwarded_for_only_with_hops():
    h = {"x-forwarded-for": "6.6.6.6, 1.2.3.4, 10.1.1.1"}
    assert client_ip(_req(h), 0) == "10.0.0.9"  # default: header ignored (spoofable)
    assert client_ip(_req(h), 1) == "10.1.1.1"
    assert client_ip(_req(h), 2) == "1.2.3.4"  # never the attacker-controlled leftmost entry
    assert client_ip(_req(h), 9) == "10.0.0.9"  # fewer entries than hops -> fall back to the peer
    assert load_settings().trusted_proxy_hops == 0


# ---------------------------------------------------------------- F16
async def test_studio_audit_is_paginated_and_verified_per_page(w):  # noqa: F811
    from test_studio_api import publish_change
    for i in range(3):
        await publish_change(w, lambda c, i=i: c["workflow"].update(shortlist_size=3 + i % 2))
    a = await w.client("admin")
    first = (await a.get("/studio/audit?limit=2")).json()
    assert len(first["items"]) == 2 and first["valid"] and first["page_valid"] and first["head_anchored"]
    assert first["events"] > 2 and first["next_cursor"] is not None
    seen = [i["seq"] for i in first["items"]]
    cur = first["next_cursor"]
    while cur:
        pg = (await a.get(f"/studio/audit?limit=2&before_seq={cur}")).json()
        assert pg["valid"] and all(i["seq"] < cur for i in pg["items"])
        seen += [i["seq"] for i in pg["items"]]
        cur = pg["next_cursor"]
    assert seen == sorted(seen, reverse=True) and len(seen) == first["events"] and len(set(seen)) == len(seen)
    # tamper with an old event: only the page containing it (or the next one chaining to it) fails; head anchor still fine
    async with dbm.session_scope() as s:
        await s.execute(text("drop trigger audit_event_no_update"))
        await s.execute(text("update audit_event set outcome='x' where project_id='studio' and seq=2"))
    bad = (await a.get("/studio/audit?limit=2&before_seq=4")).json()
    assert bad["valid"] is False and bad["first_bad_seq"] == 2 and bad["head_anchored"] is True


# ---------------------------------------------------------------- F17
async def test_supplier_audit_view_has_no_chain_hashes(w):  # noqa: F811
    pid = await create_and_start(w)
    await run_to_done(w, pid)
    s1 = await w.client("supplier1")
    items = (await s1.get(f"/projects/{pid}/audit?limit=500")).json()["items"]
    assert items
    for it in items:
        assert "hash" not in it and "prev_hash" not in it and len(it["digest"]) == 64
    full = (await (await w.client("org_admin")).get(f"/projects/{pid}/audit?limit=500")).json()["items"]
    chain_hashes = {x["hash"] for x in full} | {x["prev_hash"] for x in full}
    assert not ({it["digest"] for it in items} & chain_hashes)
    again = (await s1.get(f"/projects/{pid}/audit?limit=500")).json()["items"]
    assert [i["digest"] for i in again] == [i["digest"] for i in items]  # stable
    s2 = await w.client("supplier2")
    d2 = {i["seq"]: i["digest"] for i in (await s2.get(f"/projects/{pid}/audit?limit=500")).json()["items"]}
    common = [i for i in items if i["seq"] in d2 and i["action"] == "stage_changed"]
    assert common and all(i["digest"] != d2[i["seq"]] for i in common)  # per-supplier-org keyed
    assert (await s1.get(f"/projects/{pid}/audit/verify")).status_code == 403
    r = await s1.get(f"/projects/{pid}/stream")
    assert r.status_code == 200
    assert not ({m for m in __import__("re").findall(r'"hash":"([0-9a-f]{64})"', r.text)} & chain_hashes)


# ---------------------------------------------------------------- (d) waiver cleanup when redline raises
async def test_waiver_revoked_when_redline_raises(w):  # noqa: F811
    pid = await create_and_start(w, "locked_clause")
    h = w.service.handles[pid]
    sup = None
    orig = h.rt.make_supplier

    def patched(sid):
        nonlocal sup
        sr = orig(sid)

        async def boom(*a, **kw):
            raise RuntimeError("redline blew up")
        sr.agent.redline = boom
        sup = sr
        return sr
    # supplier runtimes are created lazily and cached: patch every existing one + future ones
    for sr in list(h.rt.suppliers.values()):
        async def boom(*a, **kw):
            raise RuntimeError("redline blew up")
        sr.agent.redline = boom
    h.rt.make_supplier = patched
    for _ in range(12):
        items = await pending(w, pid)
        if not items:
            break
        for a in items:
            c = await w.client({"REQUIREMENT": "buyer", "COMPLIANCE_FLAG": "buyer", "TECHNICAL_QUAL": "buyer", "AWARD": "buyer",
                                "CLAUSE": "buyer", "SIGNATURE": "buyer", "BANK_DETAILS": "buyer", "HOLD_RELEASE": "buyer",
                                "MANDATE_BREACH": "buyer", "LOW_CONFIDENCE": "buyer"}[a["kind"]])
            body = {"status": "APPROVED", "version": a["version"]}
            if a["kind"] == "CLAUSE":
                body = {"status": "EDITED", "version": a["version"], "comment": "accept the supplier change",
                        "edit": {"accept_change": True}}
            r = await c.post(f"/approvals/{a['id']}/resolve", body)
            assert r.status_code == 200, r.text
        await w.service.join(pid)
        if h.task and h.task.done() and h.task.exception() is not None:
            break
    assert h.task.exception() is not None  # the failure was re-thrown, not swallowed
    assert h.rt.bus.waivers == set()  # no standing waiver


# ---------------------------------------------------------------- (e) F19: bus delivered <=> audit when a listener raises
async def test_bus_delivery_matches_audit_when_listener_raises(sup_a, buyer, bus, audit):
    from sourceloop.bus.bus import Delivered
    from sourceloop.data.enums import MessageTypeEnum as T

    async def bad_listener(rec):
        raise RuntimeError("sse broker down")
    audit.subscribe(bad_listener)
    r = await sup_a.send(T.OFFER, "buyer", dict(round=1, unit_price="110", lead_time_weeks=8, capacity_units=1000))
    assert isinstance(r, Delivered)
    assert len(buyer.inbox()) == 1
    recs = await audit.store.read("p1")
    assert [x.action for x in recs].count("message_delivered") == 1 == len(bus.log)
    assert await audit.verify("p1") == (True, None)
