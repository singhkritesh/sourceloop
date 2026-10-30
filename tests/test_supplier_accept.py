"""The awarded supplier's own user confirms the final terms (workflow.checkpoints.supplier_acceptance): who sees it, who can decide it,
what it shows, and what a refusal does."""

import dataclasses

from api_support import Api, resolve_all, w  # noqa: F401
from test_studio_api import new_project, no_network, publish_change  # noqa: F401


async def _run_until_supplier_asked(w):  # noqa: ANN001, F811
    r = await publish_change(w, lambda c: c["workflow"]["checkpoints"].update(supplier_acceptance=True))
    assert r.status_code == 201, r.text
    b = await w.client("buyer")
    p = await new_project(w)
    await b.post(f"/projects/{p['id']}/start")
    await w.service.join(p["id"])
    for _ in range(12):
        asked = [a for a in (await (await w.client("org_admin")).get(f"/approvals?status=PENDING&project_id={p['id']}")).json()["items"]
                 if a["kind"] == "SUPPLIER_ACCEPT"]
        if asked:  # the test bed made an account for the supplier the moment it was asked (the role picker lists it)
            c = Api(w.app)  # the account has no known password: it is entered through the role picker, as a person would
            assert (await c.post("/auth/demo-login", {"email": f"supplier-{asked[0]['evidence']['supplier'].lower()}@sourceloop.test"})).status_code == 200
            w.clients["winner"] = c
            return p["id"], "winner", (await (await w.client("winner")).get(f"/approvals?status=PENDING&project_id={p['id']}")).json()["items"][0]
        await resolve_all(w, p["id"], only={"COMPLIANCE_FLAG", "AWARD", "CLAUSE"})
        await w.service.join(p["id"])
    raise AssertionError("the supplier was never asked")


async def test_only_the_awarded_supplier_is_asked_and_sees_only_its_own_terms(w):  # noqa: F811
    pid, key, item = await _run_until_supplier_asked(w)
    assert item["kind"] == "SUPPLIER_ACCEPT" and item["target_role"] == "SUPPLIER"
    assert set(item["evidence"]) == {"supplier", "price", "lead_time_weeks", "deviations"}  # its own terms; nothing of the buyer's
    other = "supplier1"
    assert (await (await w.client(other)).get(f"/approvals?project_id={pid}")).json()["items"] == []
    assert (await (await w.client("outsider")).post(f"/approvals/{item['id']}/resolve", {"status": "APPROVED", "version": item["version"]})).status_code == 404
    assert (await (await w.client(other)).post(f"/approvals/{item['id']}/resolve", {"status": "APPROVED", "version": item["version"]})).status_code == 404
    b = await w.client("buyer")
    assert all(a["kind"] != "SUPPLIER_ACCEPT" for a in (await b.get(f"/approvals?project_id={pid}")).json()["items"])  # not in the buyer's inbox
    assert (await b.post(f"/approvals/{item['id']}/resolve", {"status": "APPROVED", "version": item["version"]})).status_code == 403
    for admin in ("org_admin", "admin"):
        assert (await (await w.client(admin)).post(f"/approvals/{item['id']}/resolve", {"status": "APPROVED", "version": item["version"]})).status_code == 403


async def test_supplier_confirms_and_the_deal_completes(w):  # noqa: F811
    pid, key, item = await _run_until_supplier_asked(w)
    r = await (await w.client(key)).post(f"/approvals/{item['id']}/resolve", {"status": "APPROVED", "version": item["version"]})
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"
    await w.service.join(pid)
    await resolve_all(w, pid)
    await w.service.join(pid)
    b = await w.client("buyer")
    assert (await b.get(f"/projects/{pid}")).json()["outcome"] == "DEAL"


async def test_supplier_refusal_ends_without_a_deal_and_needs_a_reason(w):  # noqa: F811
    pid, key, item = await _run_until_supplier_asked(w)
    c = await w.client(key)
    assert (await c.post(f"/approvals/{item['id']}/resolve", {"status": "REJECTED", "version": item["version"]})).status_code == 422
    r = await c.post(f"/approvals/{item['id']}/resolve", {"status": "REJECTED", "version": item["version"], "comment": "capacity is gone this quarter"})
    assert r.status_code == 200
    await w.service.join(pid)
    proj = (await (await w.client("buyer")).get(f"/projects/{pid}")).json()
    assert proj["outcome"] == "NO_DEAL"


async def test_outside_the_test_bed_a_supplier_without_a_user_is_not_asked_and_the_skip_is_audited(w):  # noqa: F811
    w.service.settings = dataclasses.replace(w.service.settings, demo_role_login=False)  # no accounts are made on the fly
    r = await publish_change(w, lambda c: c["workflow"]["checkpoints"].update(supplier_acceptance=True))
    assert r.status_code == 201
    b = await w.client("buyer")
    p = await new_project(w)
    await b.post(f"/projects/{p['id']}/start")
    await w.service.join(p["id"])
    for _ in range(12):
        await resolve_all(w, p["id"])
        await w.service.join(p["id"])
    assert (await b.get(f"/projects/{p['id']}")).json()["outcome"] == "DEAL"
    audit = (await (await w.client("evaluator")).get(f"/projects/{p['id']}/audit?limit=500")).json()["items"]
    assert any(i["action"] == "checkpoint_skipped" and i.get("rule_id") == "supplier_has_no_user_account" for i in audit)
