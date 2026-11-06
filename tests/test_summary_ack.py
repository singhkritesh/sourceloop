"""The negotiation summary: each side reads only its own view, and where a company is committed or money moves the decision is refused until the
summary as it stands NOW was confirmed. The confirmation is part of the audit chain."""
import uuid

from sqlalchemy import select

from api_support import Api, resolve_all, w  # noqa: F401
from sourceloop.data import db as dbm
from sourceloop.data.models import AuditEvent
from test_studio_api import new_project, no_network, publish_change  # noqa: F401


async def _until_award(w):  # noqa: ANN001, F811
    r = await publish_change(w, lambda c: c["workflow"].update(require_summary_ack=True) or c["workflow"]["checkpoints"].update(supplier_acceptance=True))
    assert r.status_code == 201, r.text
    b = await w.client("buyer")
    p = await new_project(w)
    await b.post(f"/projects/{p['id']}/start")
    await w.service.join(p["id"])
    for _ in range(12):
        items = (await b.get(f"/approvals?status=PENDING&project_id={p['id']}")).json()["items"]
        if any(a["kind"] == "AWARD" for a in items):
            return b, p["id"], next(a for a in items if a["kind"] == "AWARD")
        await resolve_all(w, p["id"], only={"COMPLIANCE_FLAG"})
        await w.service.join(p["id"])
    raise AssertionError("the award was never asked")


async def test_the_award_needs_the_summary_confirmed_and_the_confirmation_is_audited(w):  # noqa: F811
    b, pid, item = await _until_award(w)
    s = (await b.get(f"/projects/{pid}/negotiation-summary")).json()
    assert s["ack_required"] is True and s["threads"] and s["side"] == "BUYER" and s["record"]["head_hash"] and len(s["hash"]) == 64
    assert any(t["steps"] and "per unit" in t["steps"][0]["text"] for t in s["threads"])
    body = {"status": "APPROVED", "version": item["version"]}
    r = await b.post(f"/approvals/{item['id']}/resolve", body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "SUMMARY_NOT_CONFIRMED"
    r = await b.post(f"/approvals/{item['id']}/resolve", {**body, "summary_hash": "0" * 64})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SUMMARY_CHANGED"
    r = await b.post(f"/approvals/{item['id']}/resolve", {**body, "summary_hash": s["hash"]})
    assert r.status_code == 200, r.text
    async with dbm.session_scope() as db:
        rows = (await db.execute(select(AuditEvent).where(AuditEvent.project_id == str(pid), AuditEvent.action == "approval_resolved"))).scalars().all()
    assert any((e.evidence[0] or {}).get("summary_ack") == s["hash"] for e in rows)


async def test_declining_never_needs_the_summary(w):  # noqa: F811
    b, pid, item = await _until_award(w)
    r = await b.post(f"/approvals/{item['id']}/resolve", {"status": "REJECTED", "version": item["version"], "comment": "none of these suit us this quarter"})
    assert r.status_code == 200, r.text


async def test_a_supplier_sees_only_its_own_thread_and_never_the_buyers_limits(w):  # noqa: F811
    from sourceloop.api.negotiation_summary import build
    from sourceloop.data.models import Project
    b, pid, item = await _until_award(w)
    full = (await b.get(f"/projects/{pid}/negotiation-summary")).json()
    assert len(full["threads"]) >= 1 and full["your_limits"]
    sid = full["threads"][0]["supplier_id"]
    async with dbm.session_scope() as db:
        mine = await build(db, await db.get(Project, uuid.UUID(str(pid))), "SUPPLIER", {sid})
    assert mine["side"] == "SUPPLIER" and mine["your_limits"] is None and [t["supplier_id"] for t in mine["threads"]] == [sid]
    assert all("by" in st and st["by"] in ("supplier", "buyer") for st in mine["threads"][0]["steps"])
    assert "target_unit_price" not in str(mine) and "highest_unit_price" not in str(mine) and full["your_limits"]["highest_unit_price"] not in str(mine["threads"])
    assert mine["hash"] != full["hash"]
    assert (await (await w.client("outsider")).get(f"/projects/{pid}/negotiation-summary")).status_code == 404
