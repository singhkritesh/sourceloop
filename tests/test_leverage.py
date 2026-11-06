"""Leverage: the buyer's agent may say, truthfully and in rounded terms, that other offers exist. The code builds the statement from the real
quotes and blocks any claim they do not support; the supplier reacts only inside its own floor; nothing names a supplier or gives an exact price."""
import dataclasses
from datetime import UTC
from decimal import Decimal

import pytest
from pydantic import ValidationError

from api_support import w  # noqa: F401
from sourceloop.bus.messages import Leverage
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.synthetic import generate
from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion
from sourceloop.policy.leverage import Quote, Statement, band, build, describe, verify
from test_studio_api import no_network  # noqa: F401

VIS, FULL, _ = generate(42)
D = Decimal


def q(sid, price, lead=8, delay=0.2):
    return Quote(sid, D(price), lead, delay)


def test_the_price_gap_is_a_rounded_band_never_the_figure():
    assert [band(D(x)) for x in ("3", "5", "5.1", "10", "12", "20", "35")] == ["UP_TO_5", "UP_TO_5", "5_TO_10", "5_TO_10", "10_TO_20", "10_TO_20", "OVER_20"]
    s = build(q("A", "100"), [q("B", "92"), q("C", "99")])
    assert s.signals[:2] == ("OTHER_OFFERS", "PRICE_BELOW") and s.price_gap_band == "5_TO_10" and s.based_on == 2
    text = describe(s.signals, s.price_gap_band)
    assert "92" not in text and "B" not in text.replace("buyer", "").replace("But", "") and "5 to 10%" in text


def test_nothing_is_said_when_nothing_is_true():
    assert build(q("A", "90"), []) is None
    s = build(q("A", "90"), [q("B", "95", lead=9, delay=0.3)])
    assert s.signals == ("OTHER_OFFERS",) and s.price_gap_band is None   # no cheaper, no faster, no more reliable rival: only that others exist


def test_other_signals_need_real_differences():
    s = build(q("A", "100", lead=10, delay=0.4), [q("B", "100", lead=8, delay=0.2)], paid_before=D("95"))
    assert set(s.signals) == {"OTHER_OFFERS", "FASTER_DELIVERY", "MORE_RELIABLE", "PAID_LESS_BEFORE"}


def test_a_claim_the_facts_do_not_support_is_caught():
    own, others = q("A", "100"), [q("B", "99.5")]
    lie = Statement(("OTHER_OFFERS", "PRICE_BELOW"), "OVER_20", 1, "x")
    assert "L_UNTRUE:BAND" in verify(lie, own, others)
    assert verify(Statement(("OTHER_OFFERS",), None, 3, "x"), own, others) == ["L_UNTRUE:BASED_ON"]
    assert verify(Statement(("OTHER_OFFERS",), None, 1, "x"), own, []) == ["L_UNTRUE:OTHER_OFFERS", "L_UNTRUE:BASED_ON"]
    assert verify(build(own, others), own, others) == []
    # a weaker claim than the facts is fine
    assert verify(Statement(("OTHER_OFFERS",), None, 1, "x"), own, others) == []


def test_the_message_is_closed_and_strict():
    assert Leverage(signals=("OTHER_OFFERS",), based_on=2).based_on == 2
    for bad in ({"signals": (), "based_on": 1}, {"signals": ("PRICE_BELOW",), "based_on": 1},
                {"signals": ("OTHER_OFFERS",), "price_gap_band": "UP_TO_5", "based_on": 1}, {"signals": ("OTHER_OFFERS", "OTHER_OFFERS"), "based_on": 1},
                {"signals": ("OTHER_OFFERS",), "based_on": 1, "supplier": "S1"}, {"signals": ("OTHER_OFFERS",), "based_on": 1, "price": "92"}):
        with pytest.raises(ValidationError):
            Leverage(**bad)


async def _run(leverage):
    spec = ScenarioSpec(seed=42, need_index=0)
    spec.run = dataclasses.replace(spec.run, leverage=leverage)
    rt = build_project(spec, VIS, FULL)
    return rt, await run_to_completion(rt)


async def test_off_nothing_changes():
    rt, _ = await _run(False)
    assert not any(e.type is T.LEVERAGE for e in rt.bus.log)


async def test_on_a_finalist_hears_it_before_the_first_counter_and_the_audit_names_no_one():
    rt, st = await _run(True)
    msgs = [e for e in rt.bus.log if e.type is T.LEVERAGE]
    assert msgs and all(set(e.payload) <= {"signals", "price_gap_band", "based_on"} and e.from_agent == "buyer" for e in msgs)
    first_counter = {e.to_agent: e.seq for e in rt.bus.log if e.type is T.COUNTER and e.from_agent == "buyer"}
    assert all(e.seq < first_counter.get(e.to_agent, 10**9) for e in msgs)
    rows = [r for r in await rt.audit.store.read(rt.project_id) if r.action == "leverage_stated"]
    assert rows
    others = {s for s in st.offers} | {f"supplier:{s}" for s in st.offers}
    for r in rows:
        ev = r.evidence[0]
        assert len(ev["basis_hash"]) == 64 and not (set(ev) & {"price", "unit_price", "others"})
        assert not any(o in str({k: v for k, v in ev.items() if k != "supplier"}) for o in others)


async def test_the_supplier_never_goes_below_its_own_floor_under_pressure():
    rt, st = await _run(True)
    for e in rt.bus.log:
        if e.type in (T.COUNTER, T.OFFER) and e.from_agent.startswith("supplier:"):
            assert Decimal(str(e.payload["unit_price"])) >= rt.supplier(e.from_agent.split(":", 1)[1]).mandate.min_unit_price


async def test_a_finished_negotiation_is_remembered_by_each_side_and_only_its_own(w):  # noqa: F811
    from sqlalchemy import select

    from api_support import resolve_all
    from sourceloop.data import db as dbm
    from sourceloop.data.models import NegotiationMemory, Organization
    from test_studio_api import new_project
    b = await w.client("buyer")
    p = await new_project(w)
    await b.post(f"/projects/{p['id']}/start")
    await w.service.join(p["id"])
    for _ in range(12):
        await resolve_all(w, p["id"])
        await w.service.join(p["id"])
    async with dbm.session_scope() as db:
        rows = (await db.execute(select(NegotiationMemory))).scalars().all()
        orgs = {o.id: o.kind.value for o in (await db.execute(select(Organization))).scalars()}
    assert rows
    buyer_rows = [r for r in rows if r.owner_side == "BUYER"]
    sup_rows = [r for r in rows if r.owner_side == "SUPPLIER"]
    assert buyer_rows and sup_rows and all(orgs[r.owner_org_id] == "BUYER" for r in buyer_rows) and all(orgs[r.owner_org_id] == "SUPPLIER" for r in sup_rows)
    assert all(not r.counterparty.startswith("S") for r in sup_rows)   # a supplier's memory names only the buyer organization, never another supplier
    assert any(r.outcome == "DEAL" for r in buyer_rows) and all(r.final_price > 0 and r.opening_price >= r.final_price for r in buyer_rows if r.outcome == "DEAL")


async def test_the_buyers_history_is_its_own_lowest_deal_price_per_category(w):  # noqa: F811
    import uuid
    from datetime import datetime

    from sqlalchemy import select

    from sourceloop.data import db as dbm
    from sourceloop.data.models import NegotiationMemory, Organization
    from test_studio_api import new_project
    p, p2 = await new_project(w), await new_project(w)
    async with dbm.session_scope() as db:
        org = (await db.execute(select(Organization.id).where(Organization.kind == "BUYER"))).scalars().first()
        for price, outcome in (("90", "DEAL"), ("80", "DEAL"), ("70", "NO_DEAL")):
            db.add(NegotiationMemory(owner_org_id=org, owner_side="BUYER", project_id=uuid.UUID(p["id"]), counterparty=f"S-{price}", category="widgets", rounds=2,
                                     opening_price=D("100"), final_price=D(price), outcome=outcome, created_at=datetime.now(UTC)))
        db.add(NegotiationMemory(owner_org_id=org, owner_side="BUYER", project_id=uuid.UUID(p2["id"]), counterparty="S-90", category="widgets", rounds=2,
                                 opening_price=D("100"), final_price=D("85"), outcome="DEAL", created_at=datetime.now(UTC)))
    spec = ScenarioSpec(seed=42, need_index=0)
    async with dbm.session_scope() as db:
        spec.run = dataclasses.replace(spec.run, leverage=True)
        await w.service._buyer_history(db, spec, org)
    assert spec.run.history_best == {"widgets": {"S-90": "85.0000", "S-80": "80.0000"}}   # the NO_DEAL price is not a price the buyer paid


async def test_the_summary_tells_each_side_what_was_said_without_the_rivals_details(w):  # noqa: F811
    import uuid

    from api_support import resolve_all
    from sourceloop.api.negotiation_summary import build
    from sourceloop.data import db as dbm
    from sourceloop.data.models import Project
    from test_studio_api import new_project, publish_change
    assert (await publish_change(w, lambda c: c["workflow"].update(leverage=True))).status_code == 201
    b = await w.client("buyer")
    p = await new_project(w)
    await b.post(f"/projects/{p['id']}/start")
    await w.service.join(p["id"])
    for _ in range(4):
        await resolve_all(w, p["id"], only={"COMPLIANCE_FLAG"})
        await w.service.join(p["id"])
    s = (await b.get(f"/projects/{p['id']}/negotiation-summary")).json()
    told = [t for t in s["threads"] if t["leverage"]]
    assert told and all(x["text"].startswith("Your agent told ") for t in told for x in t["leverage"])
    sid = told[0]["supplier_id"]
    async with dbm.session_scope() as db:
        mine = await build(db, await db.get(Project, uuid.UUID(p["id"])), "SUPPLIER", {sid})
    said = mine["threads"][0]["leverage"]
    assert said and all(x["text"].startswith("The buyer told you:") for x in said)
    others = [t["name"] for t in s["threads"] if t["supplier_id"] != sid]
    assert not any(n in str(mine) for n in others)


async def test_the_measurement_runs_and_reports_the_floor_check():
    from sourceloop.eval.leverage import measure, render
    m = await measure((42,), 2)
    assert m["runs"] == 2 and m["floor_respected"] is True and "Mean saving" in render(m)
