"""Perspectives: every login sees the project from ITS side only. Business users never see the agent workflow; inspectors do, each within
its own scope (SUPER_ADMIN everywhere, an organization ADMIN its own side)."""
import json

from api_support import create_and_start, pending, resolve_all, run_to_done, w  # noqa: F401

# words that only exist in the agent workflow / platform internals: a business payload must not contain them
WORKFLOW_WORDS = ("orchestrator", "from_agent", "to_agent", "subagent", "prev_hash", "payload_hash", "mandate", "ai_generated",
                  "token", "llm", "unit_cost", "min_margin", "rule_id")


async def _done(w):  # noqa: F811
    pid = await create_and_start(w)
    assert (await run_to_done(w, pid))["outcome"] == "DEAL"
    return pid


async def _overview(w, who, pid):  # noqa: F811
    return await (await w.client(who)).get(f"/projects/{pid}/overview")


async def test_buyer_overview_tells_the_business_story_without_the_agent_workflow(w):  # noqa: F811
    pid = await _done(w)
    for who in ("buyer", "compliance", "quality", "legal", "finance"):
        r = await _overview(w, who, pid)
        assert r.status_code == 200, (who, r.text)
        o = r.json()
        assert o["perspective"] == "buyer" and o["candidates"] and o["offers"] and o["award"] and o["contract"]["signed"] is True
        c = o["candidates"][0]
        assert set(c) == {"supplier_id", "name", "country", "qualification", "reasons", "rounds", "latest_offer", "awarded"}
        assert c["name"] and c["qualification"] in ("QUALIFIED", "REVIEW", "REJECTED", "BLOCKED", None)
        assert [m["stage"] for m in o["milestones"]][-1] in ("DONE", "ONBOARDING")
        text = json.dumps(o).lower()
        assert not [x for x in WORKFLOW_WORDS if x in text], (who, [x for x in WORKFLOW_WORDS if x in text])
        assert {d["kind"] for d in o["decisions"]} >= {"AWARD"}


async def test_business_users_are_refused_the_raw_agent_workflow_and_run_internals(w):  # noqa: F811
    pid = await _done(w)
    for who in ("buyer", "compliance", "quality", "legal", "finance"):
        c = await w.client(who)
        for path in ("messages", "audit", "stream", "activity", "audit/verify", "audit/export"):
            assert (await c.get(f"/projects/{pid}/{path}")).status_code == 403, (who, path)
        proj = (await c.get(f"/projects/{pid}")).json()
        assert not {"llm_mode", "tokens_used", "config_version", "buyer_org_id"} & set(proj)
        assert not {"scenario", "seed", "token_cap", "watchlist_sig", "need_fields"} & set(proj["brief"])


async def test_buyer_overview_never_leaks_supplier_private_numbers(w):  # noqa: F811
    pid = await _done(w)
    text = json.dumps((await _overview(w, "buyer", pid)).json())
    for secret in ("unit_cost", "min_margin_pct", "min_unit_price", "price floor", "max_capacity"):
        assert secret not in text


async def test_overview_is_tenant_scoped(w):  # noqa: F811
    pid = await _done(w)
    assert (await _overview(w, "buyer_b", pid)).status_code == 404
    assert (await _overview(w, "outsider", pid)).status_code == 404
    assert (await _overview(w, "designer", pid)).status_code == 403


async def test_supplier_overview_is_its_own_thread_only(w):  # noqa: F811
    pid = await _done(w)
    for who in ("supplier1", "supplier2", "sup_admin"):
        r = await _overview(w, who, pid)
        assert r.status_code == 200, (who, r.text)
        o = r.json()
        assert o["perspective"] == "supplier" and set(o) - {"contract"} == {"perspective", "offers", "outcome", "negotiation_review"}
        assert ("contract" in o) == (o["outcome"] == "DEAL")  # the contract appears only for the awardee, and then only as a key that exists
        assert all(x["side"] in ("you", "buyer") for x in o["offers"])
        assert o["outcome"] in ("DEAL", "CLOSED")
        text = json.dumps(o).lower()
        assert not [x for x in ("candidates", "qualification", "reasons", "decisions", "mandate", "max_unit_price", "excluded") if x in text]
    # a supplier only ever sees offers of its OWN supplier id
    mine = {x["supplier_id"] for x in (await _overview(w, "supplier1", pid)).json()["offers"]}
    other = {x["supplier_id"] for x in (await _overview(w, "supplier2", pid)).json()["offers"]}
    assert len(mine) <= 1 and len(other) <= 1 and not (mine & other)


async def test_inspectors_see_the_workflow_each_within_its_scope(w):  # noqa: F811
    pid = await _done(w)
    # org admin of the BUYER org: the full thread of its own project, but only its own agent's activity
    oa = await w.client("org_admin")
    assert (await oa.get(f"/projects/{pid}/messages?limit=500")).status_code == 200
    assert (await oa.get(f"/projects/{pid}/audit?limit=500")).status_code == 200
    act = (await oa.get(f"/projects/{pid}/activity")).json()
    assert act["items"] and {i["agent"] for i in act["items"]} == {"buyer"} and act["summary"]
    # SUPER_ADMIN / EVALUATOR: every agent
    for who in ("admin", "evaluator"):
        full = (await (await w.client(who)).get(f"/projects/{pid}/activity")).json()
        assert {i["agent"] for i in full["items"]} >= {"buyer"} and len(full["items"]) >= len(act["items"])
    # org admin of a SUPPLIER org: only its own supplier agent's calls; never the buyer's
    sa = await w.client("sup_admin")
    sact = (await sa.get(f"/projects/{pid}/activity")).json()
    assert all(i["agent"] == "supplier" and i["supplier_id"] for i in sact["items"])
    assert len({i["supplier_id"] for i in sact["items"]}) <= 1
    assert (await sa.get(f"/projects/{pid}/escalations")).status_code == 403
    assert (await sa.get(f"/projects/{pid}/audit/verify")).status_code == 403


async def test_org_admin_sees_every_checkpoint_of_its_org_but_resolves_only_operational_ones(w):  # noqa: F811
    pid = await create_and_start(w)
    oa = await w.client("org_admin")
    listed = (await oa.get(f"/approvals?project_id={pid}")).json()["items"]
    assert listed and all(a["target_role"] for a in listed)
    first = (await pending(w, pid))[0]
    r = await oa.post(f"/approvals/{first['id']}/resolve", {"status": "APPROVED", "version": first["version"]})
    assert r.status_code == 403          # the buyer's checkpoint is the buyer's decision
    assert (await (await w.client("sup_admin")).get(f"/approvals?project_id={pid}")).json()["items"] == []
    assert await resolve_all(w, pid, only={"COMPLIANCE_FLAG"}) == 1


async def test_overview_before_the_run_and_mid_run_is_sparse_not_an_error(w):  # noqa: F811
    b = await w.client("buyer")
    pid = (await b.post("/projects", {"title": "not started", "need_text": "n"})).json()["id"]
    o = (await b.get(f"/projects/{pid}/overview")).json()
    assert (o["candidates"], o["offers"], o["award"], o["contract"], o["decisions"]) == ([], [], None, None, [])
    assert (await (await w.client("supplier1")).get(f"/projects/{pid}/overview")).status_code == 404   # not invited: invisible, not forbidden
    await b.post(f"/projects/{pid}/start")
    await w.service.join(pid)
    mid = (await b.get(f"/projects/{pid}/overview")).json()                                      # parked at the first checkpoint
    assert [(d["kind"], d["status"], d["role"]) for d in mid["decisions"]] == [("COMPLIANCE_FLAG", "PENDING", "BUYER")] and mid["award"] is None
    await resolve_all(w, pid, only={"COMPLIANCE_FLAG"})
    await w.service.join(pid)
    later = (await b.get(f"/projects/{pid}/overview")).json()
    assert later["candidates"] and all(c["awarded"] is False for c in later["candidates"]) and later["milestones"]


async def test_pending_decisions_of_other_roles_are_visible_as_who_the_run_waits_on(w):  # noqa: F811
    pid = await create_and_start(w)
    await resolve_all(w, pid, only={"REQUIREMENT"})
    await w.service.join(pid)
    pend = [(d["kind"], d["role"]) for d in (await (await w.client("buyer")).get(f"/projects/{pid}/overview")).json()["decisions"] if d["status"] == "PENDING"]
    assert pend and all(role in ("COMPLIANCE", "QUALITY", "BUYER", "LEGAL", "FINANCE") for _, role in pend)
    mine = (await (await w.client("buyer")).get(f"/projects/{pid}/escalations?status=PENDING")).json()["items"]
    assert all(a["target_role"] == "BUYER" for a in mine)   # the overview names who is awaited; the buyer's own inbox holds only the buyer's items


async def test_negotiation_review_is_shown_per_perspective_and_carries_no_numbers(w):  # noqa: F811
    pid = await _done(w)
    b = (await _overview(w, "buyer", pid)).json()["negotiation_review"]
    assert b and {t["actor"] for t in b} <= {"buyer", "supplier"} and all(set(t) == {"supplier_id", "round", "actor", "action", "level", "notes"} for t in b)
    for who in ("supplier1", "sup_admin"):
        r = await _overview(w, who, pid)
        if r.status_code != 200:
            continue
        mine = r.json().get("negotiation_review", [])
        assert all(t["actor"] == "you" for t in mine)       # a supplier sees verdicts on its own conduct only, never on the buyer's
