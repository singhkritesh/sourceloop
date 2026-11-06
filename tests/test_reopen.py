"""Reopen a negotiation before signing: the old project stops, a linked successor starts with the buyer's new limits, both chains record it."""
import uuid

from api_support import resolve_all, w  # noqa: F401
from sourceloop.data import db as dbm
from sourceloop.data.models import Mandate, Project
from test_mailbox import _until_buyer_is_asked
from test_studio_api import no_network  # noqa: F401

LIMITS = {"target_price": "90", "max_price": "400"}


async def test_reopening_a_paused_negotiation_stops_it_and_starts_a_linked_successor(w):  # noqa: F811
    b, pid = await _until_buyer_is_asked(w)
    r = await b.post(f"/projects/{pid}/reopen", LIMITS)
    assert r.status_code == 201 and r.json()["id"] != pid
    nid = r.json()["id"]
    await w.service.join(nid)
    old, new = (await b.get(f"/projects/{pid}")).json(), (await b.get(f"/projects/{nid}")).json()
    assert old["status"] == "STOPPED" and old["reopened_to"] == nid and new["reopened_from"] == pid and new["title"].endswith("(reopened)")
    async with dbm.session_scope() as s:  # the successor carries a NEW signed mandate version with the buyer's new limits; the old one is untouched
        om, nm = [(await s.get(Mandate, (await s.get(Project, uuid.UUID(x))).buyer_mandate_id)) for x in (pid, nid)]
    assert nm.version == om.version + 1 and nm.payload["max_unit_price"] == "400.0000" and nm.payload["target_unit_price"] == "90.0000"
    assert om.payload["max_unit_price"] != "400.0000"
    assert (await b.get(f"/approvals?status=PENDING&project_id={pid}")).json()["items"] == []     # nobody can still answer the old decisions
    ins = await w.client("admin")
    for p, key in ((pid, "successor"), (nid, "predecessor")):
        assert (await ins.get(f"/projects/{p}/audit/verify")).json()["valid"] is True
        ev = [e for e in (await ins.get(f"/projects/{p}/audit", params={"limit": 500})).json()["items"] if e["action"] == "negotiation_reopened"]
        assert ev and key in str(ev[0]["evidence"])
    assert (await b.post(f"/projects/{pid}/reopen", LIMITS)).status_code == 409                    # once


async def test_only_the_owning_buyer_can_reopen_and_the_limits_must_make_sense(w):  # noqa: F811
    b, pid = await _until_buyer_is_asked(w)
    assert (await (await w.client("outsider")).post(f"/projects/{pid}/reopen", LIMITS)).status_code in (403, 404)
    assert (await (await w.client("supplier1")).post(f"/projects/{pid}/reopen", LIMITS)).status_code == 403
    assert (await b.post(f"/projects/{pid}/reopen", {"target_price": "500", "max_price": "400"})).status_code == 422
    assert (await b.post(f"/projects/{pid}/reopen", {"target_price": "0", "max_price": "400"})).status_code == 422
    assert (await b.post(f"/projects/{pid}/reopen", {**LIMITS, "extra": 1})).status_code == 422


async def test_a_signed_deal_cannot_be_reopened(w):  # noqa: F811
    b, pid = await _until_buyer_is_asked(w)
    for _ in range(12):
        if (await b.get(f"/projects/{pid}")).json()["status"] == "DONE":
            break
        await resolve_all(w, pid)
        await w.service.join(pid)
    assert (await b.get(f"/projects/{pid}")).json()["outcome"] == "DEAL"
    assert (await b.post(f"/projects/{pid}/reopen", LIMITS)).status_code == 409
