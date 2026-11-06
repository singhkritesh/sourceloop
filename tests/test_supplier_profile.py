"""A supplier's saved profile answers the buyer agent's request for information: only fresh figures, only blanks, labelled self-declared."""
import dataclasses
from datetime import timedelta

from api_support import Api, resolve_all, w  # noqa: F401
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.synthetic import generate
from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion
from test_studio_api import no_network  # noqa: F401

VIS, FULL, _ = generate(42)


async def _run(saved):
    spec = ScenarioSpec(seed=42, need_index=0, saved_profiles=saved)
    spec.run = dataclasses.replace(spec.run, agent_rfi=True, saved_profile_fields=frozenset(f"{s}:{k}" for s, v in saved.items() for k in v))
    rt = build_project(spec, VIS, FULL)
    await run_to_completion(rt)
    return rt


async def test_the_saved_figure_is_what_the_agent_declares_and_the_audit_says_so():
    base = await _run({})
    asked = sorted({e.to_agent.split(":", 1)[1] for e in base.bus.log if e.type is T.RFI})
    assert asked
    sid = asked[0]
    field = next(e for e in base.bus.log if e.type is T.RFI and e.to_agent == f"supplier:{sid}").payload["fields"][0]
    rt = await _run({sid: {field: 7.0}} if field != "financial_risk" else {sid: {field: 0.07}})
    reply = next(e for e in rt.bus.log if e.type is T.RFI_REPLY and e.from_agent == f"supplier:{sid}")
    assert reply.payload["values"][field] in (7.0, 0.07)
    rows = [r for r in await rt.audit.store.read(rt.project_id) if r.action == "rfi_round"]
    assert rows and f"{sid}:{field}" in rows[0].evidence[0]["from_saved_profile"]


async def test_without_a_saved_profile_nothing_is_marked_prefilled():
    rt = await _run({})
    rows = [r for r in await rt.audit.store.read(rt.project_id) if r.action == "rfi_round"]
    assert rows and rows[0].evidence[0]["from_saved_profile"] == []


async def test_profile_routes_are_the_suppliers_own(w):  # noqa: F811
    s = await w.client("supplier1")
    assert (await s.get("/supplier-profile")).json()["values"] == {}
    assert (await s.put("/supplier-profile", {"values": {"years_in_business": 12}})).status_code == 200
    got = (await s.get("/supplier-profile")).json()
    assert got["values"] == {"years_in_business": 12} and got["stale"] is False and "not verified" in got["source"]
    assert (await s.put("/supplier-profile", {"values": {"financial_risk": 7}})).status_code == 422   # implausible
    assert (await s.put("/supplier-profile", {"values": {"unit_price": 5}})).status_code == 422       # not on the closed list
    assert (await s.put("/supplier-profile", {"values": {}})).status_code == 422
    b = await w.client("buyer")
    assert (await b.get("/supplier-profile")).status_code == 403 and (await b.put("/supplier-profile", {"values": {"years_in_business": 3}})).status_code == 403
    assert (await s.request("DELETE", "/supplier-profile")).status_code == 204
    assert (await s.get("/supplier-profile")).json()["values"] == {}


async def test_a_stale_profile_is_not_used_and_a_fresh_one_is(w):  # noqa: F811
    from sqlalchemy import select, update

    from sourceloop.data import db as dbm
    from sourceloop.data.models import Supplier, SupplierSavedProfile
    s = await w.client("supplier1")
    assert (await s.put("/supplier-profile", {"values": {"years_in_business": 12}})).status_code == 200
    spec = ScenarioSpec(seed=42, need_index=0)
    async with dbm.session_scope() as db:
        org = (await db.execute(select(SupplierSavedProfile.org_id))).scalar_one()
        db.add(Supplier(id="S-PROFILE-TEST", org_id=org, name="Profile Test Co", name_norm="profile test co", country="DE"))
        await db.flush()
        await w.service._saved_profiles(db, spec, 90)
        assert spec.saved_profiles == {"S-PROFILE-TEST": {"years_in_business": 12}}
        assert spec.run.saved_profile_fields == frozenset({"S-PROFILE-TEST:years_in_business"})
        await db.execute(update(SupplierSavedProfile).values(confirmed_at=w.service.clock.now() - timedelta(days=91)))
        await db.commit()
    old = ScenarioSpec(seed=42, need_index=0)
    async with dbm.session_scope() as db:
        await w.service._saved_profiles(db, old, 90)
    assert old.saved_profiles == {} and old.run.saved_profile_fields == frozenset()
    assert (await s.get("/supplier-profile")).json()["stale"] is True
