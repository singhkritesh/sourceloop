"""Test-bed security controls: per-run agent identity, repeated-violation quarantine, external audit anchor, kill switch."""
import json

from api_support import build_world, create_and_start, run_to_done, w  # noqa: F401
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.synthetic import generate
from sourceloop.orchestrator.scenario import ScenarioSpec, build_project


def _rt():
    vis, full, _ = generate(42)
    return build_project(ScenarioSpec(need_index=0), vis, full)


async def test_identity_a_message_without_the_agents_own_capability_is_refused():
    rt = _rt()
    env = rt.bus.build("buyer", "orchestrator", T.BRIEF, {"category": "x"})
    res = await rt.bus.submit(env)                         # correctly signed, but not sent through the buyer's channel
    assert getattr(res, "code", None) == "IDENTITY_INVALID"
    res = await rt.bus.submit(env, rt.bus.mint("orchestrator"))   # somebody else's capability
    assert getattr(res, "code", None) == "IDENTITY_INVALID"
    cap = rt.bus.mint("buyer")
    agent, scopes, mac = cap.rsplit(".", 2)
    widened = f"{agent}.{scopes},{T.OFFER.value}.{mac}"
    assert getattr(await rt.bus.submit(env, widened), "code", None) == "IDENTITY_INVALID"   # a capability cannot be widened


async def test_identity_is_scoped_to_one_run():
    a, b = _rt(), _rt()
    env = a.bus.build("buyer", "orchestrator", T.BRIEF, {"category": "x"})
    assert getattr(await a.bus.submit(env, b.bus.mint("buyer")), "code", None) == "IDENTITY_INVALID"   # another run's token is useless here


async def test_identity_token_scopes_follow_the_sender_matrix():
    rt = _rt()
    buyer = rt.bus.mint("buyer").rsplit(".", 2)[1].split(",")
    sup = rt.bus.mint("supplier:S1").rsplit(".", 2)[1].split(",")
    assert T.RFQ.value in buyer and T.OFFER.value not in buyer
    assert T.OFFER.value in sup and T.RFQ.value not in sup


async def test_repeated_violations_quarantine_the_sender_and_are_audited():
    rt = _rt()
    for _ in range(10):
        r = await rt.buyer.ch.send(T.OFFER, "orchestrator", {"round": 1})   # a buyer may never send OFFER
        assert getattr(r, "code", None) in ("SENDER_NOT_ALLOWED", "INVALID_MESSAGE_FOR_STAGE")
    r = await rt.buyer.ch.send(T.BRIEF, "orchestrator", {"category": "x"})
    assert getattr(r, "code", None) == "QUARANTINED"
    ev = [e for e in await rt.audit.store.read("P1") if e.action == "participant_quarantined"]
    assert len(ev) == 1 and ev[0].evidence[0] == {"agent": "buyer"}
    assert await rt.audit.verify("P1") == (True, None)


async def test_external_anchor_is_published_and_catches_a_chain_that_no_longer_matches(tmp_path):
    anchor = tmp_path / "anchors" / "a.jsonl"
    world = await build_world(tmp_path, audit_anchor_path=str(anchor))
    async with world.app.router.lifespan_context(world.app):
        pid = await create_and_start(world)
        await run_to_done(world, pid)
        a = await world.client("admin")
        v = (await a.get(f"/projects/{pid}/audit/verify")).json()
        assert v["valid"] is True and v["external_anchor"]["anchors"] >= 1 and v["external_anchor"]["matches"] is True
        text = anchor.read_text()
        assert "payload" not in text and pid in text              # hashes and positions only
        # someone rewrote the database chain consistently: the anchor published earlier no longer matches
        lines = [json.loads(x) for x in text.splitlines()]
        bad = {**lines[-1], "head": "f" * 64}
        bad.pop("line_hash")
        world.service.anchors.path.write_text("\n".join(json.dumps(x) for x in [*lines[:-1], bad]) + "\n")
        v = (await a.get(f"/projects/{pid}/audit/verify")).json()
        assert v["valid"] is False and v["external_anchor"]["file_intact"] is False
        await world.close()


async def test_kill_switch_stops_new_runs_until_switched_off(w):  # noqa: F811
    a = await w.client("admin")
    assert (await a.put("/settings", {"kill_switch": True})).json()["kill_switch"] is True
    b = await w.client("buyer")
    pid = (await b.post("/projects", {"title": "t", "need_text": "500 PCB assemblies", "scenario": "happy"})).json()["id"]
    r = await b.post(f"/projects/{pid}/start")
    assert r.status_code == 503 and r.json()["error"]["code"] == "KILL_SWITCH"
    assert (await (await w.client("evaluator")).put("/settings", {"kill_switch": False})).status_code == 403   # SBN super admin only
    await a.put("/settings", {"kill_switch": False})
    assert (await b.post(f"/projects/{pid}/start")).status_code == 202
    await w.service.join(pid)


def test_anchor_sink_detects_a_tampered_file(tmp_path):
    from sourceloop.audit.anchor_sink import FileAnchorSink
    s = FileAnchorSink(tmp_path / "x.jsonl")
    s.publish("p", 3, "a" * 64, "t1")
    s.publish("p", 9, "b" * 64, "t2")
    assert s.file_intact() and s.check("p", {3: "a" * 64, 9: "b" * 64})["matches"]
    assert s.check("p", {3: "a" * 64, 9: "c" * 64})["first_mismatch_seq"] == 9
    s.path.write_text(s.path.read_text().replace("t1", "t9"))
    assert not s.file_intact()


async def test_role_picker_signs_in_a_demo_account_without_a_password(w):  # noqa: F811
    from api_support import Api
    c = Api(w.app)
    roles = (await c.get("/auth/demo-roles")).json()
    assert roles["enabled"] and {a["role"] for a in roles["accounts"]} >= {"BUYER", "COMPLIANCE", "SUPPLIER", "SUPER_ADMIN"}
    assert all(a["email"].endswith("@sourceloop.test") for a in roles["accounts"])
    assert "password" not in json.dumps(roles).lower()
    r = await c.post("/auth/demo-login", {"email": w.emails["buyer"]})
    assert r.status_code == 200 and r.json()["role"] == "BUYER"
    assert (await c.get("/auth/me")).json()["email"] == w.emails["buyer"]      # a real session: cookies work for the next call
    assert (await c.post("/auth/demo-login", {"email": "nobody@sourceloop.test"})).status_code == 404
    assert (await c.post("/auth/demo-login", {"email": "x@evil.test"})).status_code == 404   # only seeded demo accounts, never others
    await c.close()


async def test_role_picker_is_off_when_disabled_and_ignored_in_prod(w, monkeypatch):  # noqa: F811
    import dataclasses

    from api_support import Api
    from sourceloop.api.config import load_settings
    w.app.state.settings = dataclasses.replace(w.app.state.settings, demo_role_login=False)
    c = Api(w.app)
    assert (await c.get("/auth/demo-roles")).json() == {"enabled": False, "accounts": []}
    assert (await c.post("/auth/demo-login", {"email": w.emails["buyer"]})).status_code == 404
    await c.close()
    for k, v in {"APP_ENV": "prod", "JWT_SECRET": "j" * 40, "DEMO_ROLE_LOGIN": "true", "ORG_SIGNING_SECRET": "s" * 40, "CONFIG_ENCRYPTION_KEY": "ZmFrZWtleWZha2VrZXlmYWtla2V5ZmFrZWtleTEyMzQ=", "CORS_ORIGINS": "https://x.example"}.items():
        monkeypatch.setenv(k, v)
    assert load_settings().demo_role_login is False       # never honoured in prod, whatever the env says
