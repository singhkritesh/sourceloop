"""Regression tests for the second review round (service/auth/API part): R2-1 .. R2-15 + coordinator wiring."""
import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select

from api_support import (  # noqa: F401
    Api,
    build_world,
    create_and_start,
    pending,
    resolve_all,
    run_to_done,
    w,
)
from sourceloop.agents.common.tools import Tool, ToolRunner
from sourceloop.api import sse
from sourceloop.api.broker import Broker
from sourceloop.api.config import load_settings
from sourceloop.api.deps import client_ip
from sourceloop.data import db as dbm
from sourceloop.data.models import Approval, AuditEvent, LedgerHold, Message, Project, RefreshToken
from sourceloop.llm.client import BudgetExceeded
from sourceloop.orchestrator.approvals import sla_fields


def _frames(text: str) -> list[dict]:
    out = []
    for blk in text.split("\n\n"):
        d = {}
        for ln in blk.splitlines():
            if ln.startswith("id: "):
                d["id"] = int(ln[4:])
            elif ln.startswith("event: "):
                d["event"] = ln[7:]
            elif ln.startswith("data: "):
                d["data"] = json.loads(ln[6:])
        if "event" in d:
            out.append(d)
    return out


# ---------------------------------------------------------------- R2-1 sync failure converges
def _fail_once(monkeypatch, cls_name: str):
    """Make the first session_scope whose transaction INSERTS a `cls_name` row fail at commit time."""
    orig = dbm.session_scope
    state = {"armed": True, "hit": 0}

    @asynccontextmanager
    async def scope():
        async with orig() as s:
            seen: set[str] = set()
            event.listen(s.sync_session, "after_flush", lambda sess, _c: seen.update(type(o).__name__ for o in sess.new))
            yield s
            if state["armed"] and cls_name in seen:
                state["armed"] = False
                state["hit"] += 1
                raise RuntimeError("injected commit failure")
    monkeypatch.setattr(dbm, "session_scope", scope)
    return state


async def _counts(pid: str):
    async with dbm.get_sessionmaker()() as s:
        m = (await s.execute(select(func.count()).select_from(Message).where(Message.project_id == uuid.UUID(pid)))).scalar_one()
        a = (await s.execute(select(func.count()).select_from(Approval).where(Approval.project_id == uuid.UUID(pid)))).scalar_one()
    return m, a


@pytest.mark.parametrize("cls", ["Message", "Approval"])
async def test_failed_sync_commit_converges_and_project_stays_resolvable(w, monkeypatch, cls):  # noqa: F811
    state = _fail_once(monkeypatch, cls)
    pid = await create_and_start(w)
    assert state["hit"] == 1
    h = w.service.handles[pid]
    m, a = await _counts(pid)
    assert m == len(h.rt.bus.log) and a == len(h.rt.approvals.all(pid))  # DB and memory converged
    assert h.n_msgs == m and not h.needs_resync
    assert (await (await w.client("buyer")).get(f"/projects/{pid}")).json()["status"] == "ESCALATED"
    assert await resolve_all(w, pid) >= 1  # still resolvable
    await w.service.join(pid)


# ---------------------------------------------------------------- R2-2 concurrent refresh
async def test_concurrent_refresh_exactly_one_succeeds(w):  # noqa: F811
    c = await w.client("buyer")
    tok = c.c.cookies.get("refresh_token")
    csrf = c.c.cookies.get("csrf")

    async def one():
        cl = Api(w.app)
        cl.c.cookies.set("refresh_token", tok, path="/api/v1")
        cl.c.cookies.set("csrf", csrf)
        try:
            return (await cl.post("/auth/refresh", headers={"X-CSRF-Token": csrf})).status_code
        finally:
            await cl.close()
    codes = await asyncio.gather(*[one() for _ in range(6)])
    assert codes.count(200) == 1 and all(x in (200, 401) for x in codes)


# ---------------------------------------------------------------- R2-3 DONE HOLD_RELEASE without a runtime
async def test_done_hold_release_resolvable_after_restart(w):  # noqa: F811
    pid = await create_and_start(w)
    proj = await run_to_done(w, pid)
    assert proj["status"] == "DONE"
    hold_items = [a for a in await pending(w, pid) if a["kind"] == "HOLD_RELEASE"]
    assert len(hold_items) == 1
    w.service.handles.pop(pid, None)  # simulated restart: the runtime is gone, DONE projects are not closed by recover()
    fresh = type(w.service)(w.settings, Broker())
    await fresh.recover()
    fin = await w.client("buyer")
    a = hold_items[0]
    r = await fin.post(f"/approvals/{a['id']}/resolve", {"status": "APPROVED", "version": a["version"]})
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    async with dbm.get_sessionmaker()() as s:
        hold = (await s.execute(select(LedgerHold).where(LedgerHold.project_id == uuid.UUID(pid)))).scalar_one()
        evs = list((await s.execute(select(AuditEvent).where(AuditEvent.project_id == pid).order_by(AuditEvent.seq))).scalars())
    assert hold.status.value == "RELEASED" and hold.released_by == str(w.ids["buyer"])
    assert evs[-1].action == "approval_resolved" and evs[-1].approver_id == str(w.ids["buyer"])
    ev = await w.client("evaluator")
    assert (await ev.get(f"/projects/{pid}/audit/verify")).json()["valid"] is True
    again = await fin.post(f"/approvals/{a['id']}/resolve", {"status": "APPROVED", "version": a["version"]})
    assert again.status_code == 409


# ---------------------------------------------------------------- R2-4 stop cancels approvals
async def test_stop_cancels_pending_approvals_and_resolve_says_stopped(w):  # noqa: F811
    pid = await create_and_start(w)
    b = await w.client("buyer")
    a = (await pending(w, pid))[0]
    assert (await b.post(f"/projects/{pid}/stop")).status_code == 200
    async with dbm.get_sessionmaker()() as s:
        row = await s.get(Approval, uuid.UUID(a["id"]))
    assert row.status.value == "REJECTED" and row.comment == "project stopped"
    r = await b.post(f"/approvals/{a['id']}/resolve", {"status": "APPROVED", "version": a["version"]})
    body = r.json()["error"]
    assert r.status_code == 409 and body["code"] == "INVALID_STATE" and "restart" not in body["message"].lower()


# ---------------------------------------------------------------- R2-5 structured need
async def test_structured_need_fields_feed_the_brief_and_mandate(w):  # noqa: F811
    b = await w.client("buyer")
    r = await b.post("/projects", {"title": "t", "need_text": "free text", "category": "connectors", "volume_units": 777,
                                   "required_certs": ["ISO9001"], "excluded_countries": ["RU"], "max_lead_time_weeks": 9})
    assert r.status_code == 201, r.text
    brief = r.json()["brief"]
    assert brief["category"] == "connectors" and brief["industry"] == "high_tech" and brief["volume_units"] == 777
    assert brief["required_certificates"] == ["ISO9001"] and brief["excluded_countries"] == ["RU"]
    assert brief["max_lead_time_weeks"] == 9
    h = w.service.handles[r.json()["id"]]
    assert h.rt.need.category == "connectors" and h.rt.buyer_mandate.max_lead_time_weeks == 9
    # target_price becomes the mandate target (Decimal); above the ceiling -> 422, never silently ignored
    ceiling = h.rt.buyer_mandate.max_unit_price
    ok = await b.post("/projects", {"title": "t", "category": "connectors", "target_price": str(ceiling - Decimal("0.5"))})
    assert ok.status_code == 201 and Decimal(ok.json()["brief"]["target_unit_price"]) == ceiling - Decimal("0.5")
    bad = await b.post("/projects", {"title": "t", "category": "connectors", "target_price": str(ceiling + 1)})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_NEED"
    for body in ({"category": "nope"}, {"required_certs": ["X"]}, {"excluded_countries": ["ZZ"]}, {"industry": "x"},
                 {"volume_units": 0}, {"target_price": -1}):
        assert (await b.post("/projects", {"title": "t", **body})).status_code == 422


# ---------------------------------------------------------------- R2-6 rate limits / login throttle
async def test_per_route_limits_are_per_user_with_retry_after(tmp_path):
    world = await build_world(tmp_path, create_limit="2/hour", start_limit="1/hour", resolve_limit="1/minute")
    async with world.app.router.lifespan_context(world.app):
        b = await world.client("buyer")
        assert (await b.post("/projects", {"title": "a"})).status_code == 201
        pid = (await b.post("/projects", {"title": "b"})).json()["id"]
        r = await b.post("/projects", {"title": "c"})
        assert r.status_code == 429 and "retry-after" in r.headers
        assert (await (await world.client("buyer_b")).post("/projects", {"title": "x"})).status_code == 201  # other user
        assert (await b.post(f"/projects/{pid}/start")).status_code == 202
        await world.service.join(pid)
        assert (await b.post(f"/projects/{pid}/resume")).status_code == 429  # start + resume share one bucket
        a = (await pending(world, pid))[0]
        bogus = str(uuid.uuid4())
        await b.post(f"/approvals/{bogus}/resolve", {"status": "APPROVED", "version": 1})
        assert (await b.post(f"/approvals/{a['id']}/resolve", {"status": "APPROVED", "version": a["version"]})).status_code == 429
        await world.close()


async def test_forged_cookie_does_not_pick_a_bucket(tmp_path):
    world = await build_world(tmp_path, login_limit="3/minute")
    async with world.app.router.lifespan_context(world.app):
        c = Api(world.app)
        c.c.cookies.set("access_token", "forged.token.value", path="/api/v1")
        codes = [(await c.login("nobody@sourceloop.test", "wrong-password-x")).status_code for _ in range(5)]
        assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
        await c.close()
        await world.close()


async def test_lockout_is_per_account_and_ip_first(tmp_path):
    world = await build_world(tmp_path, max_failed_logins=3, max_failed_logins_account=6, max_failed_logins_per_ip=100)
    async with world.app.router.lifespan_context(world.app):
        email = world.emails["buyer"]
        attacker = Api(world.app)
        for _ in range(3):
            assert (await attacker.login(email, "wrong-password-xx")).status_code == 401
        assert (await attacker.login(email)).status_code == 401  # this (account, ip) pair is locked, even with the right password
        # the victim from a different IP is not locked out by the attacker
        ip2 = Api(world.app)
        ip2.c._transport = None
        import httpx
        ip2.c = httpx.AsyncClient(transport=httpx.ASGITransport(app=world.app, client=("203.0.113.9", 1234)), base_url="http://testserver")
        assert (await ip2.login(email)).status_code == 200
        await ip2.close()
        await attacker.close()
        await world.close()


async def test_single_ip_cannot_lock_many_accounts_and_is_throttled(tmp_path):
    world = await build_world(tmp_path, max_failed_logins=2, max_failed_logins_per_ip=5)
    async with world.app.router.lifespan_context(world.app):
        atk = Api(world.app)
        codes = []
        for key in ("buyer", "legal", "finance", "quality", "compliance", "buyer_b"):
            codes.append((await atk.login(world.emails[key], "wrong-password-xx")).status_code)
        assert codes[:5] == [401] * 5 and codes[5] == 429  # per-IP throttle kicks in
        async with dbm.get_sessionmaker()() as s:
            from sourceloop.data.models import User
            locked = (await s.execute(select(func.count()).select_from(User).where(User.locked_until.is_not(None)))).scalar_one()
        assert locked == 0  # no account was locked by one IP
        await atk.close()
        await world.close()


# ---------------------------------------------------------------- R2-7 token budget
async def test_budget_setting_lowers_cap_for_new_projects(w):  # noqa: F811
    a = await w.client("admin")
    r = await a.put("/settings", {"token_budget_per_project": 1000})
    assert r.status_code == 200
    pid = (await (await w.client("buyer")).post("/projects", {"title": "t"})).json()["id"]
    assert w.service.handles[pid].rt.llm.cfg.tokens_per_project == 1000
    await a.put("/settings", {"token_budget_per_project": 400000})
    pid2 = (await (await w.client("buyer")).post("/projects", {"title": "t"})).json()["id"]
    assert w.service.handles[pid2].rt.llm.cfg.tokens_per_project <= 400000
    assert w.service.handles[pid].rt.llm.cfg.tokens_per_project == 1000  # existing project is not rewritten


async def test_tool_runner_reraises_budget_exceeded():
    from pydantic import BaseModel

    class I(BaseModel):  # noqa: E742
        x: int = 1

    class Out(BaseModel):
        y: int = 1

    async def boom(i: I):
        raise BudgetExceeded("p")
    runner = ToolRunner([Tool("t", I, Out, boom, retries=2)])
    with pytest.raises(BudgetExceeded):
        await runner.call("t", I())
    assert runner.stats["budget_exceeded"] == 1 and not runner._inflight


async def test_budget_exceeded_in_a_tool_pauses_the_run(w):  # noqa: F811
    b = await w.client("buyer")
    pid = (await b.post("/projects", {"title": "t"})).json()["id"]
    h = w.service.handles[pid]
    h.rt.llm.used[pid] = h.rt.llm.cfg.tokens_per_project  # the budget is spent: the first LLM-backed tool call must pause the run
    await b.post(f"/projects/{pid}/start")
    await w.service.join(pid)
    assert (await b.get(f"/projects/{pid}")).json()["status"] == "BUDGET_EXCEEDED"  # paused, not failed and not silently continued


# ---------------------------------------------------------------- R2-8 live handle cap / draft TTL
async def test_live_handle_cap_and_draft_eviction_with_rebuild(tmp_path):
    world = await build_world(tmp_path, max_live_handles=2, draft_ttl_minutes=1)
    async with world.app.router.lifespan_context(world.app):
        b = await world.client("buyer")
        p1 = (await b.post("/projects", {"title": "1"})).json()["id"]
        (await b.post("/projects", {"title": "2"})).raise_for_status()
        r = await b.post("/projects", {"title": "3"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "CAPACITY_EXCEEDED"
        world.service.handles[p1].touched -= 3600  # idle draft: evictable
        assert (await b.post("/projects", {"title": "3"})).status_code == 201
        assert p1 not in world.service.handles  # evicted, the row stays
        assert (await b.get(f"/projects/{p1}")).json()["status"] == "DRAFT"
        # start on the evicted draft rebuilds the runtime from the pinned config + stored seed
        world.service.handles[next(iter(world.service.handles))].touched -= 3600
        r = await b.post(f"/projects/{p1}/start")
        assert r.status_code == 202, r.text
        await world.service.join(p1)
        assert (await b.get(f"/projects/{p1}")).json()["status"] == "ESCALATED"
        await world.close()


# ---------------------------------------------------------------- R2-9 / R2-10 supplier view
async def test_supplier_view_is_closed_neutral_dense_and_paged(w):  # noqa: F811
    pid = await create_and_start(w)
    proj = await run_to_done(w, pid)
    assert proj["status"] == "DONE"
    full = (await (await w.client("org_admin")).get(f"/projects/{pid}/audit?limit=500")).json()["items"]
    for key in ("supplier1", "supplier2"):
        sup = await w.client(key)
        view = (await sup.get(f"/projects/{pid}")).json()
        assert view["title"].startswith("Sourcing request") and "PCB" not in view["title"]
        assert view["status"] == "DONE" and view["outcome"] in ("DEAL", "CLOSED")
        items = (await sup.get(f"/projects/{pid}/audit?limit=500")).json()["items"]
        assert [i["seq"] for i in items] == list(range(1, len(items) + 1))  # dense: no gaps reveal hidden events
        assert len(items) < len(full)
        assert items[-1]["action"] == "closed" and "hash" not in items[-1]
        assert all(i["action"] != "run_finished" for i in items)
    outcomes = {(await (await w.client(k)).get(f"/projects/{pid}")).json()["outcome"] for k in ("supplier1", "supplier2")}
    assert outcomes != {"DEAL"}  # at most one supplier (the awardee) sees DEAL
    # SQL paging with a cursor: small pages concatenate to the full view
    sup = await w.client("supplier1")
    whole = (await sup.get(f"/projects/{pid}/audit?limit=500")).json()["items"]
    got, cur = [], 0
    for _ in range(50):
        pg = (await sup.get(f"/projects/{pid}/audit?limit=3&after_seq={cur}")).json()
        if not pg["items"]:
            break
        got += pg["items"]
        cur = pg["next_after_seq"]
    assert [g["seq"] for g in got] == [i["seq"] for i in whole] and [g["digest"] for g in got] == [i["digest"] for i in whole]
    msgs = (await sup.get(f"/projects/{pid}/messages?limit=500")).json()["items"]
    assert [m["seq"] for m in msgs] == list(range(1, len(msgs) + 1))
    # SSE view: same ranks, closed instead of the raw outcome, resumable by rank
    fs = _frames((await sup.get(f"/projects/{pid}/stream")).text)
    assert fs[-2]["event"] in ("run_finished", "audit_event") and any(f["event"] == "run_finished" for f in fs)
    assert [f["id"] for f in fs if f["event"] == "audit_event"] == [i["seq"] for i in whole]
    fin = [f for f in fs if f["event"] == "run_finished"][0]
    assert fin["data"]["outcome"] in ("DEAL", "CLOSED")
    resumed = _frames((await sup.get(f"/projects/{pid}/stream", headers={"Last-Event-ID": "2"})).text)
    assert resumed and min(f["id"] for f in resumed) == 3


# ---------------------------------------------------------------- R2-11 SSE cap under concurrency
async def test_sse_cap_holds_under_concurrent_opens(w):  # noqa: F811
    pid = await create_and_start(w)
    cap = w.settings.max_streams_per_user
    # hold the slots open: a stream that never finishes (project parked, nothing to replay)
    async def one():
        try:
            return (await asyncio.wait_for((await w.client("org_admin")).get(f"/projects/{pid}/stream"), 1.5)).status_code
        except TimeoutError:
            return "open"
    res = await asyncio.gather(*[one() for _ in range(60)])
    assert res.count(429) >= 60 - cap
    assert w.app.state.broker.streams[str(w.ids["buyer"])] <= cap
    await asyncio.sleep(0.3)


# ---------------------------------------------------------------- R2-13 prod config / headers / XFF
def test_prod_requires_explicit_https_cors_and_forces_secure_cookies(monkeypatch):
    from cryptography.fernet import Fernet
    for k in ("CORS_ORIGINS", "COOKIE_SECURE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET", "j" * 40)
    monkeypatch.setenv("ORG_SIGNING_SECRET", "s" * 40)
    monkeypatch.setenv("SOURCELOOP_VAULT_KEY", Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        load_settings()
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3010")
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        load_settings()
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.test")
    s = load_settings()
    assert s.cookie_secure is True and s.cors_origins == ("https://app.example.test",)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        load_settings()


async def test_security_headers_and_no_store(w):  # noqa: F811
    c = await w.client("buyer")
    r = await c.get("/auth/me")
    csp = r.headers["content-security-policy"]
    for part in ("frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'", "object-src 'none'"):
        assert part in csp
    assert r.headers["cache-control"] == "no-store"


def test_client_ip_joins_all_forwarded_for_lines():
    import httpx

    class Req:
        client = type("C", (), {"host": "10.0.0.9"})()
        app = type("A", (), {"state": type("S", (), {"settings": None})()})()
        headers = httpx.Headers([("x-forwarded-for", "6.6.6.6, 1.2.3.4"), ("x-forwarded-for", "10.1.1.1")])
    assert client_ip(Req(), 1) == "10.1.1.1"
    assert client_ip(Req(), 2) == "1.2.3.4"


# ---------------------------------------------------------------- R2-15 hold in the same transaction as the approval CAS
async def test_hold_release_is_atomic_with_the_approval(w, monkeypatch):  # noqa: F811
    pid = await create_and_start(w)
    await run_to_done(w, pid)
    a = [x for x in await pending(w, pid) if x["kind"] == "HOLD_RELEASE"][0]
    state = _fail_once(monkeypatch, "__never__")
    orig = dbm.session_scope
    calls = {"n": 0}

    @asynccontextmanager
    async def flaky():
        async with orig() as s:
            yield s
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("commit failed")  # first attempt rolls back BOTH the approval and the hold
    monkeypatch.setattr(dbm, "session_scope", flaky)
    fin = await w.client("buyer")
    r = await fin.post(f"/approvals/{a['id']}/resolve", {"status": "APPROVED", "version": a["version"]})
    assert r.status_code == 200  # the retry succeeded
    async with dbm.get_sessionmaker()() as s:
        hold = (await s.execute(select(LedgerHold).where(LedgerHold.project_id == uuid.UUID(pid)))).scalar_one()
        row = await s.get(Approval, uuid.UUID(a["id"]))
    assert row.status.value == "APPROVED" and hold.status.value == "RELEASED"
    assert state["hit"] == 0


# ---------------------------------------------------------------- coordinator wiring
async def test_tamper_blocks_start_and_resume_with_409(w):  # noqa: F811
    b = await w.client("buyer")
    pid = (await b.post("/projects", {"title": "t"})).json()["id"]
    from sqlalchemy import text
    async with dbm.session_scope() as s:  # tail truncation: the anchor (forward-only) claims events that are not in the log
        await s.execute(text("insert into audit_anchor (project_id, seq, hash, updated_at) values (:p, 3, :h, '2026-01-01 00:00:00')"),
                        {"p": pid, "h": "f" * 64})
    r = await b.post(f"/projects/{pid}/start")
    assert r.status_code == 409 and r.json()["error"]["code"] == "TAMPER_SUSPECTED"
    assert (await b.get(f"/projects/{pid}")).json()["status"] == "DRAFT"


async def test_edit_error_maps_to_422_invalid_edit(w, monkeypatch):  # noqa: F811
    from sourceloop.orchestrator.approvals import ApprovalEditError
    pid = await create_and_start(w)
    a = (await pending(w, pid))[0]
    h = w.service.handles[pid]

    async def bad(*x, **kw):
        raise ApprovalEditError("nope")
    monkeypatch.setattr(h.rt.approvals, "resolve", bad)
    r = await (await w.client("buyer")).post(f"/approvals/{a['id']}/resolve", {"status": "APPROVED", "version": a["version"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_EDIT"
    async with dbm.get_sessionmaker()() as s:
        assert (await s.get(Approval, uuid.UUID(a["id"]))).status.value == "PENDING"


async def test_approvals_list_has_sla_fields(w):  # noqa: F811
    pid = await create_and_start(w)
    items = (await pending(w, pid))
    assert items and all("due_at" in i and i["overdue"] is False for i in items)
    from datetime import UTC, datetime, timedelta

    from sourceloop.data.enums import ApprovalKindEnum as K
    from sourceloop.data.enums import ApprovalStatusEnum as S
    f = sla_fields(K.HOLD_RELEASE, S.PENDING, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=3))
    assert f["overdue"] is True
    _ = time, sse, Project, RefreshToken
