"""Studio over the API: draft -> validate -> publish -> project pins the version -> run uses it; invariants; keys never
leak; rollback; diff; SQL immutability; tamper detection; determinism; live mode gating (no real Ollama calls)."""
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from api_support import World, build_world, run_to_done, w  # noqa: F401
from sourceloop.api.deps import Principal
from sourceloop.audit.chain import verify_chain
from sourceloop.data import db as dbm
from sourceloop.data.audit_store import SqlAuditStore, to_record
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import AuditEvent, ConfigVersion, ImmutableError
from sourceloop.studio.defaults import default_config

SECRET = "sk-TEST-super-secret-key-0123456789"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    async def fake(name, d, vault):  # noqa: ANN001 - the suite never reaches a real Ollama
        return {"ok": False, "models": [], "error": "UNREACHABLE"}
    monkeypatch.setattr("sourceloop.api.routers.studio.probe", fake)
    monkeypatch.setattr("sourceloop.api.service.probe", fake)


async def publish_change(world, mutate, note="test change"):
    a = await world.client("admin")
    cfg = (await a.get("/studio/draft")).json()["config"]
    mutate(cfg)
    r = await a.put("/studio/draft", {"config": cfg})
    assert r.status_code == 200, r.text
    return await a.post("/studio/publish", {"note": note})


async def new_project(world, **body):
    b = await world.client("buyer")
    r = await b.post("/projects", {"title": "PCB", "need_text": "500 PCB", **body})
    assert r.status_code == 201, r.text
    return r.json()


async def test_default_config_is_version_one_and_active(w):  # noqa: F811
    a = await w.client("admin")
    vs = (await a.get("/studio/versions")).json()
    assert [(v["version"], v["active"], v["created_by"]) for v in vs] == [(1, True, "system")]
    act = (await a.get("/studio/active")).json()
    assert act["version"] == 1 and act["config"]["workflow"]["shortlist_size"] == 5 and len(act["content_hash"]) == 64
    sch = (await a.get("/studio/schema")).json()
    assert "properties" in sch["schema"] and any(i["id"] == "INV_LOCKED_CLAUSES" for i in sch["invariants"])


async def _inspect(w, pid):  # noqa: F811
    return (await (await w.client("org_admin")).get(f"/projects/{pid}")).json()


async def test_flow_draft_validate_publish_pin_run_and_old_project_keeps_old_version(w):  # noqa: F811
    old = await new_project(w)  # pinned to v1 at creation
    assert (await _inspect(w, old["id"]))["config_version"] == 1  # the pin is run internals: an inspector reads it, not the buyer
    a = await w.client("admin")
    cfg = (await a.get("/studio/draft")).json()["config"]
    cfg["workflow"]["shortlist_size"] = 2
    cfg["workflow"]["negotiate_top"] = 1
    cfg["clauses"]["warranty"]["negotiable"] = False
    assert (await a.put("/studio/draft", {"config": cfg})).status_code == 200
    rep = (await a.post("/studio/validate", {})).json()
    assert rep["errors"] == []
    r = await a.post("/studio/publish", {"note": "smaller shortlist"})
    assert r.status_code == 201 and r.json()["version"] == 2 and len(r.json()["content_hash"]) == 64
    assert [v["version"] for v in (await a.get("/studio/versions")).json() if v["active"]] == [2]

    new = await new_project(w)  # pins v2
    assert (await _inspect(w, new["id"]))["config_version"] == 2
    rt_new, rt_old = w.service.handles[new["id"]].rt, w.service.handles[old["id"]].rt
    assert rt_new.cfg.shortlist_size == 2 and rt_new.cfg.negotiate_top == 1 and rt_new.cfg.config_version == 2
    assert rt_new.bus.cfg.clause_library["warranty"]["negotiable"] is False
    assert rt_old.cfg.shortlist_size == 5 and rt_old.bus.cfg.clause_library["warranty"]["negotiable"] is True

    for pid in (new["id"], old["id"]):  # the OLD project starts AFTER the publish and must still use v1
        b = await w.client("buyer")
        assert (await b.post(f"/projects/{pid}/start")).status_code == 202
        await w.service.join(pid)
        assert (await run_to_done(w, pid))["status"] == "DONE"
    assert len(rt_new.state.shortlist) == 2 and len(rt_old.state.shortlist) == 5
    ev = await w.client("evaluator")
    for pid, want in ((new["id"], 2), (old["id"], 1)):
        items = (await ev.get(f"/projects/{pid}/audit?limit=500")).json()["items"]
        assert {i["config_version"] for i in items} - {None} == {want}  # human events carry no config version
        assert (await ev.get(f"/projects/{pid}/audit/verify")).json()["valid"] is True
    # DB pin is the version row, not 'active'
    async with dbm.session_scope() as s:
        from sqlalchemy import select

        from sourceloop.data.models import Project
        pins = {str(p.id): p.config_version_id for p in (await s.execute(select(Project))).scalars()}
        rows = {r.id: r.version for r in (await s.execute(select(ConfigVersion))).scalars()}
    assert rows[pins[new["id"]]] == 2 and rows[pins[old["id"]]] == 1


async def test_scenario_and_negotiation_come_from_the_pinned_config(w):  # noqa: F811
    r = await publish_change(w, lambda c: c["scenarios"].update(
        cheap={"name": "Tight budget", "buyer_max_factor": "0.5", "buyer_target_factor": "0.4"}))
    assert r.status_code == 201, r.text
    p = await new_project(w, scenario="cheap")
    b = await w.client("buyer")
    assert (await b.post("/projects", {"title": "x", "scenario": "nope"})).status_code == 422
    await b.post(f"/projects/{p['id']}/start")
    await w.service.join(p["id"])
    done = await run_to_done(w, p["id"])
    assert done["outcome"] == "NO_DEAL"  # the custom scenario's factors replace the built-in ones


async def test_requirement_checkpoint_is_off_by_default_and_can_be_switched_on(w):  # noqa: F811
    p = await new_project(w)
    b = await w.client("buyer")
    await b.post(f"/projects/{p['id']}/start")
    await w.service.join(p["id"])
    first = [a["kind"] for a in (await b.get(f"/projects/{p['id']}/escalations")).json()["items"]]
    assert "REQUIREMENT" not in first  # the buyer typed the need, so it is not asked back
    done = await run_to_done(w, p["id"])
    assert done["status"] == "DONE"
    kinds = {a["kind"] for a in (await b.get(f"/projects/{p['id']}/escalations")).json()["items"]}
    assert {"AWARD", "HOLD_RELEASE"} <= kinds  # the human checkpoints that remain, all the buyer user's
    assert not kinds & {"TECHNICAL_QUAL", "SIGNATURE", "BANK_DETAILS"}  # rule-settled, audited
    audit = (await (await w.client("evaluator")).get(f"/projects/{p['id']}/audit?limit=500")).json()["items"]
    assert any(i["action"] == "checkpoint_skipped" for i in audit)  # the skip is audited, never silent
    r = await publish_change(w, lambda c: c["workflow"]["checkpoints"].update(requirement_confirmation=True))
    assert r.status_code == 201
    p2 = await new_project(w)
    await b.post(f"/projects/{p2['id']}/start")
    await w.service.join(p2["id"])
    assert "REQUIREMENT" in [a["kind"] for a in (await b.get(f"/projects/{p2['id']}/escalations")).json()["items"]]


async def test_invariant_attempts_via_api_are_rejected_422(w):  # noqa: F811
    a = await w.client("admin")
    cfg = (await a.get("/studio/draft")).json()["config"]
    # 1. smuggled switch: unparseable, never saved
    bad = json.loads(json.dumps(cfg))
    bad["audit_enabled"] = False
    r = await a.put("/studio/draft", {"config": bad})
    assert r.status_code == 422 and r.json()["error"]["code"] == "CONFIG_UNPARSEABLE"
    assert any(e["invariant_id"] == "INV_AUDIT_CHAIN" for e in r.json()["error"]["details"]["errors"])
    # 2. parseable but invalid: draft saves (with inline errors) yet publish is refused with the report
    cfg["clauses"]["liability"]["locked"] = False
    cfg["clauses"]["liability"]["negotiable"] = True
    r = await a.put("/studio/draft", {"config": cfg})
    assert r.status_code == 200 and r.json()["validation"]["errors"]
    r = await a.post("/studio/publish", {"note": "unlock liability"})
    assert r.status_code == 422
    errs = r.json()["error"]["details"]["errors"]
    assert any(e["invariant_id"] == "INV_LOCKED_CLAUSES" for e in errs)
    assert [v["version"] for v in (await a.get("/studio/versions")).json()] == [1]  # nothing published
    # 3. a designer cannot publish at all
    d = await w.client("designer")
    assert (await d.post("/studio/publish", {"note": "x"})).status_code == 403


async def test_provider_key_is_write_only_and_never_leaks(w, caplog):  # noqa: F811
    a = await w.client("admin")
    assert (await a.get("/studio/providers")).json() == [{"name": "ollama", "kind": "OLLAMA", "base_url": "http://localhost:11434",
                                                           "is_local": True, "has_key": False}]
    seen: list[str] = []
    r = await a.put("/studio/providers/ollama/key", {"secret": SECRET})
    assert r.status_code == 200 and r.json() == {"has_key": True}
    seen.append(r.text)
    assert (await a.put("/studio/providers/nope/key", {"secret": SECRET})).status_code == 404
    assert (await a.put("/studio/providers/ollama/key", {"secret": ""})).status_code == 422
    for path in ("/studio/providers", "/studio/active", "/studio/versions", "/studio/versions/1", "/studio/draft", "/studio/audit",
                 "/studio/export", "/studio/schema", "/studio/diff?a=1&b=1"):
        seen.append((await a.get(path)).text)
    seen.append((await a.post("/studio/providers/ollama/test")).text)
    await publish_change(w, lambda c: c["workflow"].update(shortlist_size=3))
    seen += [(await a.get("/studio/diff?a=1&b=2")).text, (await a.get("/studio/export")).text]
    assert (await a.get("/studio/providers")).json()[0]["has_key"] is True
    ev = await w.client("evaluator")
    seen.append((await ev.get("/studio/providers")).text)
    assert not any(SECRET in t for t in seen)
    assert SECRET not in caplog.text
    # stored encrypted; not in any audit row, config body, or the database file at all
    async with dbm.session_scope() as s:
        ct = (await s.execute(text("select ciphertext from provider_key"))).scalar_one()
        evs = (await s.execute(text("select action || evidence from audit_event"))).scalars().all()
        bodies = (await s.execute(text("select body from config_version"))).scalars().all()
    assert SECRET.encode() not in bytes(ct) and not any(SECRET in str(x) for x in (*evs, *bodies))
    assert SECRET.encode() not in Path(w.settings.database_url.split(":///", 1)[1]).read_bytes()
    assert any("provider_key_set" in str(x) for x in evs)  # the event exists, without the value
    # delete
    r = await a.request("DELETE", "/studio/providers/ollama/key")
    assert r.json() == {"has_key": False} and (await a.get("/studio/providers")).json()[0]["has_key"] is False
    # server-side resolution still works for the vault itself
    from sourceloop.api.studio_support import make_ctx
    await a.put("/studio/providers/ollama/key", {"secret": SECRET})
    async with dbm.session_scope() as s:
        assert (await make_ctx(s, w.settings)).vault.resolve("ollama") == SECRET


async def test_provider_test_reports_unreachable_without_error_and_audits(w, monkeypatch):  # noqa: F811
    a = await w.client("admin")
    r = await a.post("/studio/providers/ollama/test")
    assert r.status_code == 200 and r.json() == {"ok": False, "models": [], "error": "UNREACHABLE"}

    async def ok(name, d, vault):  # noqa: ANN001
        return {"ok": True, "models": ["gemma4:e2b-mlx"]}
    monkeypatch.setattr("sourceloop.api.routers.studio.probe", ok)
    assert (await a.post("/studio/providers/ollama/test")).json() == {"ok": True, "models": ["gemma4:e2b-mlx"]}
    actions = [i["action"] for i in (await a.get("/studio/audit")).json()["items"]]
    assert actions.count("studio.provider_tested") == 2


async def test_rollback_creates_new_version_and_diff_is_structured(w):  # noqa: F811
    a = await w.client("admin")
    assert (await publish_change(w, lambda c: c["workflow"].update(shortlist_size=3, longlist_size=12))).json()["version"] == 2
    d = (await a.get("/studio/diff?a=1&b=2")).json()
    ch = {c["path"]: c for c in d["changes"]}
    assert ch["workflow.shortlist_size"]["before"] == 5 and ch["workflow.shortlist_size"]["after"] == 3
    assert ch["workflow.longlist_size"]["kind"] == "changed" and (await a.get("/studio/diff?a=1&b=99")).status_code == 404
    r = await a.post("/studio/rollback", {"to_version": 1})
    assert r.status_code == 201 and r.json()["version"] == 3 and r.json()["rollback_of"] == 1
    vs = {v["version"]: v for v in (await a.get("/studio/versions")).json()}
    assert vs[3]["active"] and vs[3]["rollback_of"] == 1 and vs[3]["content_hash"] == vs[1]["content_hash"]
    assert (await a.get("/studio/active")).json()["config"]["workflow"]["shortlist_size"] == 5
    assert (await a.post("/studio/rollback", {"to_version": 42})).status_code == 404
    assert (await (await w.client("designer")).post("/studio/rollback", {"to_version": 1})).status_code == 403


async def test_stale_draft_publish_is_409_and_discard_recovers(w):  # noqa: F811
    a, d = await w.client("admin"), await w.client("designer")
    cfg = (await d.get("/studio/draft")).json()["config"]  # designer's draft starts at v1
    assert (await publish_change(w, lambda c: c["workflow"].update(shortlist_size=4))).status_code == 201
    adm_cfg = (await a.get("/studio/draft")).json()
    assert adm_cfg["base_version"] == 2
    # admin publishes from a draft that was based on v1
    r = await a.put("/studio/draft", {"config": cfg})
    assert r.status_code == 200 and r.json()["base_version"] == 2  # the admin draft restarted after the publish
    # simulate staleness: another publish lands between draft start and publish
    await a.get("/studio/draft")
    assert (await a.post("/studio/publish", {"note": "x", "base_version": 1})).status_code == 409
    assert (await a.post("/studio/publish", {"note": "x", "base_version": 1})).json()["error"]["code"] == "BASE_VERSION_STALE"
    assert (await d.request("DELETE", "/studio/draft")).json() == {"discarded": True}


async def test_export_import_roundtrip_makes_a_validated_draft(w):  # noqa: F811
    a = await w.client("admin")
    bundle = (await a.get("/studio/export")).json()
    assert bundle["config"]["providers"]["ollama"]["key_ref"] is None and bundle["schema_version"] == 1
    bundle["config"]["workflow"]["shortlist_size"] = 4
    r = await a.post("/studio/import", {"bundle": bundle})
    assert r.status_code == 200 and r.json()["config"]["workflow"]["shortlist_size"] == 4 and r.json()["validation"]["errors"] == []
    assert (await a.post("/studio/publish", {"note": "imported"})).json()["version"] == 2
    bundle["config"]["not_a_field"] = 1
    assert (await a.post("/studio/import", {"bundle": bundle})).status_code == 422
    assert (await a.post("/studio/import", {"bundle": {**bundle, "schema_version": 99}})).status_code == 422
    assert (await (await w.client("designer")).post("/studio/import", {"bundle": bundle})).status_code == 403


async def test_studio_audit_is_hash_chained_and_tail_truncation_is_detected(w):  # noqa: F811
    a = await w.client("admin")
    await publish_change(w, lambda c: c["workflow"].update(shortlist_size=3))
    audit = (await a.get("/studio/audit")).json()
    assert audit["valid"] is True and audit["events"] >= 3
    assert {"studio.published", "studio.draft_saved"} <= {i["action"] for i in audit["items"]}
    async with dbm.session_scope() as s:  # a DBA with trigger privileges deletes the last event
        await s.execute(text("drop trigger audit_event_no_delete"))
        n = (await s.execute(text("select max(seq) from audit_event where project_id='studio'"))).scalar_one()
        await s.execute(text("delete from audit_event where project_id='studio' and seq=:n"), {"n": n})
    after = (await a.get("/studio/audit")).json()
    assert after["valid"] is False and after["first_bad_seq"] == n


async def test_published_config_rows_are_immutable_in_sql(w):  # noqa: F811
    async with dbm.get_sessionmaker()() as s:  # ORM guard
        row = (await s.execute(text("select id from config_version where version=1"))).scalar_one()
        obj = await s.get(ConfigVersion, uuid.UUID(str(row)) if not isinstance(row, uuid.UUID) else row)
        obj.body = {"tampered": True}
        with pytest.raises(ImmutableError):
            await s.flush()
        await s.rollback()
    async with dbm.get_sessionmaker()() as s:  # DB trigger (raw SQL bypasses the ORM)
        for stmt in ("update config_version set body='{}' where version=1", "update config_version set content_hash='x' where version=1",
                     "update config_version set version=9 where version=1", "delete from config_version where version=1"):
            with pytest.raises(DBAPIError):
                await s.execute(text(stmt))
            await s.rollback()


async def test_tampered_config_body_is_detected_when_a_project_is_created(w):  # noqa: F811
    async with dbm.session_scope() as s:  # attacker drops the trigger and edits the stored body
        await s.execute(text("drop trigger config_version_frozen_update"))
        body = (await s.execute(text("select body from config_version where version=1"))).scalar_one()
        edited = json.loads(body)
        edited["workflow"]["shortlist_size"] = 1
        await s.execute(text("update config_version set body=:b where version=1"), {"b": json.dumps(edited)})
    b = await w.client("buyer")
    r = await b.post("/projects", {"title": "t"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFIG_TAMPERED"
    assert (await b.get("/projects")).json()["items"] == []  # nothing was created


async def test_live_mode_needs_a_reachable_provider_and_pins_the_config(w, monkeypatch):  # noqa: F811
    a, b = await w.client("admin"), await w.client("buyer")
    await a.put("/settings", {"llm_mode": "live"})
    r = await b.post("/projects", {"title": "t"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "LLM_PROVIDER_UNREACHABLE"

    async def ok(name, d, vault):  # noqa: ANN001
        return {"ok": True, "models": [m.model for m in default_config().llm.roles.values()]}
    monkeypatch.setattr("sourceloop.api.service.probe", ok)
    r = await b.post("/projects", {"title": "t"})
    seen = await _inspect(w, r.json()["id"])
    assert r.status_code == 201 and seen["llm_mode"] == "live" and seen["config_version"] == 1  # created, NOT started
    assert "llm_mode" not in r.json() and "config_version" not in r.json()  # a business user never sees run internals
    rt = w.service.handles[r.json()["id"]].rt
    assert rt.llm.cfg.mode == "live" and set(rt.llm.providers) == {"ollama"} and rt.llm.cfg.roles["fast"].model == "gemma4:e2b-mlx"

    async def missing(name, d, vault):  # noqa: ANN001
        return {"ok": True, "models": ["something-else"]}
    monkeypatch.setattr("sourceloop.api.service.probe", missing)
    r = await b.post("/projects", {"title": "t"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "MODEL_NOT_FOUND"
    await a.put("/settings", {"llm_mode": "replay"})


FIXED_PID = uuid.UUID("22222222-3333-4444-8555-666666666666")


async def _fixed_run_v2(tmp_path, i: int) -> tuple[str, int]:
    world: World = await build_world(tmp_path / f"d{i}", fixed_ids=True)
    async with world.app.router.lifespan_context(world.app):
        assert (await publish_change(world, lambda c: c["workflow"].update(shortlist_size=3, negotiate_top=1))).status_code == 201
        async with dbm.get_sessionmaker()() as db:
            org = world.orgs["acme"]
            await world.service.create_project(db, Principal(world.ids["buyer"], "b", R.BUYER, org, "BUYER"), title="t",
                                               need_text="n", scenario="happy", need_index=0, seed=42, project_id=FIXED_PID)
        b = await world.client("buyer")
        assert (await b.post(f"/projects/{FIXED_PID}/start")).status_code == 202
        await world.service.join(FIXED_PID)
        assert (await run_to_done(world, str(FIXED_PID)))["status"] == "DONE"
        v = (await (await world.client("evaluator")).get(f"/projects/{FIXED_PID}/audit/verify")).json()
        await world.close()
        return v["head_hash"], v["events"]


async def test_same_pinned_version_gives_identical_audit_hash(tmp_path):
    a, b = await _fixed_run_v2(tmp_path, 0), await _fixed_run_v2(tmp_path, 1)
    assert a == b and a[1] > 80


async def test_sql_audit_anchor_detects_truncation_of_a_project_log(w):  # noqa: F811
    p = await new_project(w)
    bc = await w.client("buyer")
    await bc.post(f"/projects/{p['id']}/start")
    await w.service.join(p["id"])
    ev = await w.client("evaluator")
    assert (await ev.get(f"/projects/{p['id']}/audit/verify")).json()["valid"] is True
    async with dbm.session_scope() as s:
        await s.execute(text("drop trigger audit_event_no_delete"))
        n = (await s.execute(text("select max(seq) from audit_event where project_id=:p"), {"p": p["id"]})).scalar_one()
        await s.execute(text("delete from audit_event where project_id=:p and seq=:n"), {"p": p["id"], "n": n})
    v = (await ev.get(f"/projects/{p['id']}/audit/verify")).json()
    assert v["valid"] is False and v["first_bad_seq"] == n
    # the anchor cannot be rewound to hide it
    async with dbm.get_sessionmaker()() as s:
        with pytest.raises(DBAPIError):
            await s.execute(text("update audit_anchor set seq=1 where project_id=:p"), {"p": p["id"]})
        await s.rollback()
    store = SqlAuditStore()
    recs = await store.read(p["id"])
    assert verify_chain(recs)[0] is True and verify_chain(recs, anchor=await store.anchor(p["id"]))[0] is False
    assert isinstance(to_record(AuditEvent(project_id="x", seq=1, ts=recs[0].ts, actor_type="a", actor_id="b", action="c", evidence=[],
                                           prev_hash="0" * 64, hash="1" * 64)), type(recs[0]))
