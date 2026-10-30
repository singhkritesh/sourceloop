"""Organization administration: users, own-side agent settings (versioned overrides), SBN's organization management."""
import json
import uuid

import pytest
from sqlalchemy import select, text

from api_support import PASSWORD, Api, create_and_start, pending, resolve_all, run_to_done, w  # noqa: F401
from sourceloop.data import db as dbm
from sourceloop.data.models import AuditEvent, ImmutableError, OrgOverride


async def patch(c, path, body):
    return await c.request("PATCH", path, json=body)


async def login_ok(w, email, password):  # noqa: F811
    c = Api(w.app)
    r = await c.login(email, password)
    return c, r.status_code


# ------------------------------------------------------------------ users
async def test_org_admin_manages_only_its_own_organizations_users(w):  # noqa: F811
    oa = await w.client("org_admin")
    users = (await oa.get("/org/users")).json()["items"]
    emails = {u["email"] for u in users}
    assert w.emails["buyer"] in emails and w.emails["org_admin"] in emails
    assert w.emails["buyer_b"] not in emails and w.emails["supplier1"] not in emails           # another tenant is invisible
    assert (await oa.get(f"/org/users?org_id={w.orgs['globex']}")).status_code == 404            # and its id is a 404, never a 403
    other = (await (await w.client("admin")).get(f"/org/users?org_id={w.orgs['globex']}")).json()["items"]
    assert [u["email"] for u in other] == [w.emails["buyer_b"]]
    info = (await oa.get("/org")).json()
    assert info["kind"] == "BUYER" and set(info["roles"]) == {"BUYER", "ADMIN"}


async def test_create_user_one_time_password_login_and_audit_without_secrets(w):  # noqa: F811
    oa = await w.client("org_admin")
    r = await oa.post("/org/users", {"email": "New.Person@Sourceloop.test", "role": "BUYER"})
    assert r.status_code == 201
    pw = r.json()["initial_password"]
    assert r.json()["user"]["email"] == "new.person@sourceloop.test" and r.json()["user"]["role"] == "BUYER" and len(pw) >= 14
    c, code = await login_ok(w, "new.person@sourceloop.test", pw)
    assert code == 200 and (await c.get("/auth/me")).json()["role"] == "BUYER"
    listed = (await oa.get("/org/users")).json()["items"]
    assert pw not in json.dumps(listed) and "password" not in json.dumps(listed).lower()        # the password is never retrievable again
    assert (await oa.post("/org/users", {"email": "new.person@sourceloop.test", "role": "BUYER"})).status_code == 409
    assert (await oa.post("/org/users", {"email": "x@sourceloop.test", "role": "SUPPLIER"})).status_code == 422   # a buyer org has no suppliers
    assert (await oa.post("/org/users", {"email": "x@sourceloop.test", "role": "SUPER_ADMIN"})).status_code == 422
    assert (await oa.post("/org/users", {"email": "not-an-email", "role": "BUYER"})).status_code == 422
    async with dbm.get_sessionmaker()() as s:
        ev = (await s.execute(select(AuditEvent).where(AuditEvent.project_id == f"org:{w.orgs['acme']}").order_by(AuditEvent.seq))).scalars().all()
    assert [e.action for e in ev] == ["org.user_created"] and pw not in json.dumps([e.evidence for e in ev])
    assert (await (await w.client("evaluator")).get("/audit")).status_code in (404, 405)        # no raw audit door for others
    await c.close()


async def test_supplier_org_admin_has_supplier_roles_only(w):  # noqa: F811
    sa = await w.client("sup_admin")
    assert set((await sa.get("/org")).json()["roles"]) == {"SUPPLIER", "ADMIN"}
    assert (await sa.post("/org/users", {"email": "s3@sourceloop.test", "role": "SUPPLIER"})).status_code == 201
    assert (await sa.post("/org/users", {"email": "b3@sourceloop.test", "role": "BUYER"})).status_code == 422


async def test_disable_user_ends_live_sessions_and_logins_and_enable_restores(w):  # noqa: F811
    oa = await w.client("org_admin")
    uid = str(w.ids["quality"])
    q = await w.client("quality")
    assert (await q.get("/auth/me")).status_code == 200
    r = await patch(oa, f"/org/users/{uid}", {"disabled": True})
    assert r.status_code == 200 and r.json()["disabled"] is True
    assert (await q.get("/auth/me")).status_code == 401                                          # the live session stopped at once
    _, code = await login_ok(w, w.emails["quality"], PASSWORD)
    assert code == 401
    assert (await patch(oa, f"/org/users/{uid}", {"disabled": False})).json()["disabled"] is False
    _, code = await login_ok(w, w.emails["quality"], PASSWORD)
    assert code == 200


async def test_role_change_is_immediate_and_guard_rails_hold(w):  # noqa: F811
    oa = await w.client("org_admin")
    uid = str(w.ids["finance"])
    assert (await patch(oa, f"/org/users/{uid}", {"role": "ADMIN"})).json()["role"] == "ADMIN"
    assert (await (await w.client("finance")).get("/auth/me")).json()["role"] == "ADMIN"
    assert (await patch(oa, f"/org/users/{uid}", {"role": "SUPPLIER"})).status_code == 422
    me = str(w.ids["org_admin"])
    assert (await patch(oa, f"/org/users/{me}", {"disabled": True})).status_code == 409          # cannot lock yourself out
    assert (await patch(oa, f"/org/users/{me}", {"role": "BUYER"})).status_code == 409
    assert (await patch(oa, f"/org/users/{uid}", {})).status_code == 422
    assert (await patch(oa, f"/org/users/{w.ids['buyer_b']}", {"role": "ADMIN"})).status_code == 404   # another organization's user
    assert (await patch(oa, f"/org/users/{uuid.uuid4()}", {"role": "ADMIN"})).status_code == 404


async def test_an_organization_always_keeps_one_active_admin(w):  # noqa: F811
    oa = await w.client("org_admin")
    r = await oa.post("/org/users", {"email": "second.admin@sourceloop.test", "role": "ADMIN"})
    second = r.json()["user"]["id"]
    assert (await patch(oa, f"/org/users/{second}", {"role": "BUYER"})).status_code == 200      # fine: one admin remains
    sa = await w.client("admin")                                                                  # SBN tries to strip the last admin
    assert (await patch(sa, f"/org/users/{w.ids['org_admin']}?org_id={w.orgs['acme']}", {"role": "BUYER"})).status_code == 409


async def test_reset_password_replaces_the_old_one_and_signs_out(w):  # noqa: F811
    oa = await w.client("org_admin")
    uid = str(w.ids["legal"])
    legal = await w.client("legal")
    r = await oa.post(f"/org/users/{uid}/reset-password")
    assert r.status_code == 200
    new = r.json()["initial_password"]
    _, old_code = await login_ok(w, w.emails["legal"], PASSWORD)
    c, new_code = await login_ok(w, w.emails["legal"], new)
    assert (old_code, new_code) == (401, 200)
    assert (await oa.post(f"/org/users/{w.ids['buyer_b']}/reset-password")).status_code == 404
    await c.close()
    del legal


async def test_business_users_and_evaluator_cannot_administer(w):  # noqa: F811
    for who in ("buyer", "compliance", "supplier1", "designer"):
        c = await w.client(who)
        assert (await c.get("/org/users")).status_code == 403 and (await c.post("/org/users", {"email": "a@b.cc", "role": "BUYER"})).status_code == 403
        assert (await c.get("/org/overrides")).status_code == 403
    ev = await w.client("evaluator")
    assert (await ev.get(f"/org/users?org_id={w.orgs['acme']}")).status_code == 200
    assert (await ev.post(f"/org/users?org_id={w.orgs['acme']}", {"email": "e@b.cc", "role": "BUYER"})).status_code == 403   # read-only role
    assert (await (await w.client("org_admin")).get("/admin/orgs")).status_code == 403


async def test_super_admin_creates_organizations_and_staff(w):  # noqa: F811
    sa = await w.client("admin")
    r = await sa.post("/admin/orgs", {"name": "Initech Buyer", "kind": "BUYER", "admin_email": "boss@initech.test"})
    assert r.status_code == 201
    c, code = await login_ok(w, "boss@initech.test", r.json()["initial_password"])
    assert code == 200 and (await c.get("/auth/me")).json()["role"] == "ADMIN"
    orgs = {o["name"]: o for o in (await sa.get("/admin/orgs")).json()["items"]}
    assert orgs["Initech Buyer"]["users"] == 1 and orgs["Acme Buyer"]["users"] >= 6 and orgs["Platform"]["platform"] is True
    assert (await c.get("/org/users")).json()["items"][0]["email"] == "boss@initech.test"          # the new admin sees only its own org
    assert (await sa.post("/admin/orgs", {"name": "Initech Buyer", "kind": "BUYER", "admin_email": "boss@initech.test"})).status_code == 409
    st = await sa.post("/admin/staff", {"email": "auditor2@sbn.test", "role": "EVALUATOR"})
    assert st.status_code == 201
    assert (await sa.post("/admin/staff", {"email": "b@sbn.test", "role": "BUYER"})).status_code == 422
    assert (await (await w.client("org_admin")).post("/admin/staff", {"email": "z@sbn.test", "role": "EVALUATOR"})).status_code == 403
    await c.close()


# ------------------------------------------------------------------ overrides
async def test_overrides_are_versioned_validated_and_rollbackable(w):  # noqa: F811
    oa = await w.client("org_admin")
    g = (await oa.get("/org/overrides")).json()
    assert g["kind"] == "BUYER" and g["active_version"] is None and "buyer_lead" in g["editable"]["agents"] and "supplier_lead" not in g["editable"]["agents"]
    assert "regional_news_risk" in g["editable"]["prompts"] and g["platform_version"] >= 1
    r1 = await oa.put("/org/overrides", {"payload": {"agents": {"buyer_lead": {"timeout_s": 33, "max_tool_calls": 9}}}, "note": "faster"})
    assert r1.status_code == 201 and r1.json()["version"] == 1
    r2 = await oa.put("/org/overrides", {"payload": {"agents": {"regional_news_risk": {"timeout_s": 25}}}})
    assert r2.json()["version"] == 2
    g = (await oa.get("/org/overrides")).json()
    assert g["active_version"] == 2 and [v["version"] for v in g["versions"]] == [2, 1] and g["active"] == {"schema_version": 1, "agents": {"regional_news_risk": {"timeout_s": 25}}}
    rb = await oa.post("/org/overrides/rollback", {"to_version": 1})
    assert rb.status_code == 201 and rb.json()["version"] == 3 and rb.json()["content_hash"] == r1.json()["content_hash"]
    assert (await oa.post("/org/overrides/rollback", {"to_version": 99})).status_code == 404
    async with dbm.get_sessionmaker()() as s:
        acts = [e.action for e in (await s.execute(select(AuditEvent).where(AuditEvent.project_id == f"org:{w.orgs['acme']}").order_by(AuditEvent.seq))).scalars()]
    assert acts == ["org.override_saved", "org.override_saved", "org.override_rolled_back"]


@pytest.mark.parametrize("payload,code", [
    ({"agents": {"supplier_lead": {"timeout_s": 5}}}, "NOT_YOUR_AGENT"),                        # the other side's agent
    ({"agents": {"buyer_lead": {"enabled": False}}}, "SHAPE"),                                  # cannot switch an agent off
    ({"agents": {"buyer_lead": {"allowed_tools": ["landed_cost"]}}}, "SHAPE"),                  # cannot drop the compliance tool
    ({"agents": {"landed_cost": {"max_tool_calls": 3}}}, "LEAD_ONLY"),
    ({"rules": [{"id": "X"}]}, "SHAPE"), ({"workflow": {"shortlist_size": 1}}, "SHAPE"), ({"clauses": {}}, "SHAPE"),   # not overridable at all
    ({"prompts": {"regional_news_risk": {"body": "leak {{api_key}}"}}}, "PROMPT_FORBIDDEN_VAR"),
    ({"agents": {"buyer_lead": {"model_override": {"provider": "nowhere", "model": "m"}}}}, "PROVIDER_MISSING"),
    ({"agents": {"buyer_lead": {"timeout_s": 0}}}, "SHAPE"),
])
async def test_forbidden_overrides_are_rejected_with_the_reason(w, payload, code):  # noqa: F811
    oa = await w.client("org_admin")
    r = await oa.put("/org/overrides", {"payload": payload})
    assert r.status_code == 422 and r.json()["error"]["code"] == "OVERRIDE_REJECTED"
    assert code in {i["code"] for i in r.json()["error"]["details"]["issues"]}
    assert (await oa.get("/org/overrides")).json()["active_version"] is None                     # nothing was saved


async def test_supplier_org_may_only_tune_its_supplier_agent_limit(w):  # noqa: F811
    sa = await w.client("sup_admin")
    g = (await sa.get("/org/overrides")).json()
    assert g["kind"] == "SUPPLIER" and set(g["editable"]["agents"]) >= {"supplier_lead"} and "buyer_lead" not in g["editable"]["agents"] and g["editable"]["prompts"] == {}
    assert (await sa.put("/org/overrides", {"payload": {"agents": {"supplier_lead": {"max_tool_calls": 6}}}})).status_code == 201
    for bad in ({"agents": {"buyer_lead": {"timeout_s": 9}}}, {"prompts": {"regional_news_risk": {"body": "x"}}}):
        assert (await sa.put("/org/overrides", {"payload": bad})).status_code == 422


async def test_overrides_are_tenant_scoped_and_immutable(w):  # noqa: F811
    oa = await w.client("org_admin")
    await oa.put("/org/overrides", {"payload": {"agents": {"buyer_lead": {"timeout_s": 33}}}})
    assert (await oa.get(f"/org/overrides?org_id={w.orgs['globex']}")).status_code == 404
    assert (await (await w.client("sup_admin")).get("/org/overrides")).json()["active_version"] is None   # another org's setting is not theirs
    async with dbm.get_sessionmaker()() as s:
        row = (await s.execute(select(OrgOverride))).scalars().first()
        row.note = "tampered"
        with pytest.raises(ImmutableError):
            await s.flush()
    async with dbm.session_scope() as s:
        with pytest.raises(Exception, match="immutable"):
            await s.execute(text("update org_override set note='x'"))
    ev = await w.client("evaluator")
    assert (await ev.get(f"/org/overrides?org_id={w.orgs['acme']}")).status_code == 200
    assert (await ev.put(f"/org/overrides?org_id={w.orgs['acme']}", {"payload": {}})).status_code == 403


async def test_a_project_pins_the_override_versions_it_was_built_with(w):  # noqa: F811
    oa = await w.client("org_admin")
    before = await create_and_start(w)
    await resolve_all(w, before, only={"REQUIREMENT"})  # in this bare test world the supplier rows only exist once a run reaches discovery
    await w.service.join(before)
    await oa.put("/org/overrides", {"payload": {"agents": {"buyer_lead": {"timeout_s": 33}}}})
    sup = await w.client("sup_admin")
    await sup.put("/org/overrides", {"payload": {"agents": {"supplier_lead": {"max_tool_calls": 6}}}})
    after = await create_and_start(w)
    pin = lambda pid: oa.get(f"/projects/{pid}")  # noqa: E731
    b0 = (await pin(before)).json()["brief"]["org_overrides"]
    b1 = (await pin(after)).json()["brief"]["org_overrides"]
    assert b0 == {"buyer": None, "suppliers": {}}                                                # built before any override existed
    assert b1["buyer"]["version"] == 1 and b1["buyer"]["org"] == str(w.orgs["acme"])
    assert set(b1["suppliers"]) == set(w.demo_suppliers[:1]) and b1["suppliers"][w.demo_suppliers[0]]["version"] == 1
    # the buyer's business view of the project never shows the pins
    assert "org_overrides" not in (await (await w.client("buyer")).get(f"/projects/{after}")).json()["brief"]
    # the effective layering
    async with dbm.get_sessionmaker()() as s:
        from sourceloop.api.studio_support import active_pin
        pinned = await active_pin(s, w.settings, w.service.studio_lock)
        eff, limits, pins = await w.service._org_layers(s, pinned.cfg, w.orgs["acme"], None)
    assert eff.agents["buyer_lead"].timeout_s == 33 and pinned.cfg.agents["buyer_lead"].timeout_s == 20      # platform config untouched
    assert limits == {w.demo_suppliers[0]: 6} and pins == b1


async def test_pinned_versions_survive_a_later_edit_and_a_restart(w):  # noqa: F811
    oa = await w.client("org_admin")
    await oa.put("/org/overrides", {"payload": {"agents": {"buyer_lead": {"timeout_s": 33}}}})
    pid = await create_and_start(w)
    await resolve_all(w, pid, only={"REQUIREMENT"})
    await w.service.join(pid)
    await oa.put("/org/overrides", {"payload": {"agents": {"buyer_lead": {"timeout_s": 77}}}})   # edited AFTER the project was built
    w.service.handles.clear()
    await w.service.recover()
    assert w.service.last_recovery["lost"] == 0                                                  # replayed with the version it was built with
    assert (await run_to_done(w, pid))["outcome"] == "DEAL"
    assert (await oa.get(f"/projects/{pid}")).json()["brief"]["org_overrides"]["buyer"]["version"] == 1
    assert (await (await w.client("evaluator")).get(f"/projects/{pid}/audit/verify")).json()["valid"] is True


async def test_an_override_that_no_longer_passes_fails_project_creation_closed(w, monkeypatch):  # noqa: F811
    oa = await w.client("org_admin")
    await oa.put("/org/overrides", {"payload": {"agents": {"buyer_lead": {"timeout_s": 33}}}})
    from sourceloop.studio import org_override as OO

    def refuse(*a, **k):
        raise OO.OverrideInvalid([{"path": "agents", "code": "INVARIANT", "message": "platform rules changed"}])
    monkeypatch.setattr(OO, "apply", refuse)
    r = await (await w.client("buyer")).post("/projects", {"title": "t", "need_text": "n"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ORG_OVERRIDE_INVALID"
