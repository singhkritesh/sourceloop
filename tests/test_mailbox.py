"""The in-app mailbox (email and text windows): decisions arrive, can be answered by replying in words, are reminded, and stay on their own side."""
import dataclasses
import uuid

from sqlalchemy import update

from api_support import Api, resolve_all, w  # noqa: F401
from sourceloop.api import mailbox as mb
from sourceloop.data import db as dbm
from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.models import Project
from test_studio_api import new_project, no_network, publish_change  # noqa: F401
from test_supplier_accept import _run_until_supplier_asked


def test_replies_are_read_in_plain_words():
    assert mb.parse_reply("APPROVE") == (AS.APPROVED, "")
    assert mb.parse_reply("  yes, go ahead") == (AS.APPROVED, "go ahead")
    assert mb.parse_reply("Decline price is too high for us") == (AS.REJECTED, "price is too high for us")
    assert mb.parse_reply("maybe later") == (None, "") and mb.parse_reply("   ") == (None, "")


async def _until_buyer_is_asked(w):  # noqa: ANN001, F811
    b = await w.client("buyer")
    p = await new_project(w)
    await b.post(f"/projects/{p['id']}/start")
    await w.service.join(p["id"])
    return b, p["id"]


async def test_a_waiting_decision_lands_as_an_email_and_a_text_without_business_data(w):  # noqa: F811
    b, pid = await _until_buyer_is_asked(w)
    box = (await b.get("/mailbox")).json()
    assert box["unread"]["EMAIL"] >= 1 and box["unread"]["SMS"] >= 1
    kinds = {(m["channel"], m["kind"]) for m in box["items"]}
    assert ("EMAIL", "DECISION") in kinds and ("SMS", "DECISION") in kinds
    for m in box["items"]:
        assert not any(ch.isdigit() for ch in m["body"].replace(pid, "").replace("8005", "")) or "http" in m["body"]  # no prices or counts in the words
        assert m["project_id"] == pid and m["open"] is True
    assert (await b.get("/mailbox?channel=SMS")).json()["items"] and all(m["channel"] == "SMS" for m in (await b.get("/mailbox?channel=SMS")).json()["items"])
    await b.post(f"/mailbox/{box['items'][0]['id']}/read")
    assert (await b.get("/mailbox")).json()["unread"]["EMAIL"] + (await b.get("/mailbox")).json()["unread"]["SMS"] < box["unread"]["EMAIL"] + box["unread"]["SMS"]


async def test_another_organization_sees_nothing_and_cannot_reply(w):  # noqa: F811
    b, _ = await _until_buyer_is_asked(w)
    mid = (await b.get("/mailbox")).json()["items"][0]["id"]
    out = await w.client("outsider")
    assert (await out.get("/mailbox")).json()["items"] == []
    assert (await out.post(f"/mailbox/{mid}/reply", {"text": "APPROVE"})).status_code == 404
    assert (await out.post(f"/mailbox/{mid}/read")).status_code == 404
    assert (await (await w.client("supplier1")).get("/mailbox")).json()["items"] == []


async def test_replying_approve_resolves_the_decision_like_the_button(w):  # noqa: F811
    b, pid = await _until_buyer_is_asked(w)
    before = (await b.get(f"/approvals?status=PENDING&project_id={pid}")).json()["items"]
    assert before
    msg = next(m for m in (await b.get("/mailbox?channel=SMS")).json()["items"] if m["open"])
    r = await b.post(f"/mailbox/{msg['id']}/reply", {"text": "APPROVE"})
    assert r.status_code == 200 and r.json()["answer"].startswith("Done")
    await w.service.join(pid)
    now = (await b.get(f"/approvals?status=PENDING&project_id={pid}")).json()["items"]
    assert msg["approval_id"] not in {a["id"] for a in now}
    thread = [m["kind"] for m in (await b.get("/mailbox?channel=SMS")).json()["items"] if m["approval_id"] == msg["approval_id"]]
    assert "REPLY" in thread and "NOTE" in thread
    again = await b.post(f"/mailbox/{msg['id']}/reply", {"text": "APPROVE"})   # answering twice changes nothing and says so
    assert again.json()["answer"] == "This decision was already answered."


async def test_a_decline_needs_a_reason_and_unclear_words_change_nothing(w):  # noqa: F811
    b, pid = await _until_buyer_is_asked(w)
    msg = next(m for m in (await b.get("/mailbox")).json()["items"] if m["open"])
    assert "reason" in (await b.post(f"/mailbox/{msg['id']}/reply", {"text": "decline"})).json()["answer"]
    assert "APPROVE" in (await b.post(f"/mailbox/{msg['id']}/reply", {"text": "hmm let me think"})).json()["answer"]
    assert any(a["id"] == msg["approval_id"] for a in (await b.get(f"/approvals?status=PENDING&project_id={pid}")).json()["items"])
    assert (await b.post(f"/mailbox/{msg['id']}/reply", {"text": "x" * 600})).status_code == 422


async def test_the_awarded_supplier_answers_its_final_terms_from_its_own_mailbox(w):  # noqa: F811
    pid, key, item = await _run_until_supplier_asked(w)
    c = await w.client(key)
    msg = next(m for m in (await c.get("/mailbox")).json()["items"] if m["approval_id"] == item["id"])
    assert msg["project_id"] == pid
    assert "Done" in (await c.post(f"/mailbox/{msg['id']}/reply", {"text": "approve"})).json()["answer"]
    await w.service.join(pid)
    await resolve_all(w, pid)
    await w.service.join(pid)
    assert (await (await w.client("buyer")).get(f"/projects/{pid}")).json()["outcome"] == "DEAL"


async def test_a_decision_nobody_answers_is_reminded_at_most_three_times(w):  # noqa: F811
    w.service.settings = dataclasses.replace(w.service.settings, reminder_after_hours=0)
    b, pid = await _until_buyer_is_asked(w)
    sent = [await w.service.send_reminders() for _ in range(5)]
    assert sent[0] >= 1 and sum(1 for n in sent if n) == 3 and sent[3:] == [0, 0]
    reminders = [m for m in (await b.get("/mailbox")).json()["items"] if m["kind"] == "REMINDER"]
    assert len(reminders) == 3 * sent[0] and all(m["subject"].startswith("Reminder") for m in reminders)
    await resolve_all(w, pid)
    await w.service.join(pid)
    w.service.settings = dataclasses.replace(w.service.settings, reminder_after_hours=24)
    assert await w.service.send_reminders() == 0


async def test_agent_chasing_shows_up_in_the_suppliers_mailbox_and_the_answer_in_the_buyers(w):  # noqa: F811
    from types import SimpleNamespace

    from sqlalchemy import select

    from sourceloop.data.enums import MessageTypeEnum as T
    from sourceloop.data.models import Supplier
    b, pid = await _until_buyer_is_asked(w)
    async with dbm.session_scope() as s:
        sup = (await s.execute(select(Supplier).where(Supplier.org_id == w.orgs["sup1"]).limit(1))).scalar_one()
        now = w.service.clock.now()
        for env in (SimpleNamespace(type=T.RFI, to_agent=f"supplier:{sup.id}", payload={"fields": ["years_in_business", "lead_time_weeks"]}),
                    SimpleNamespace(type=T.FOLLOW_UP, to_agent=f"supplier:{sup.id}", payload={"attempt": 2}),
                    SimpleNamespace(type=T.RFI_REPLY, to_agent="buyer", payload={"values": {"years_in_business": 12}})):
            await w.service._mail_message(s, now, uuid.UUID(pid), env)
    mine = (await (await w.client("supplier1")).get("/mailbox")).json()["items"]
    assert {m["kind"] for m in mine} == {"RFI", "FOLLOW_UP"}
    assert "years in business" in next(m for m in mine if m["kind"] == "RFI")["body"] and "reminder 2" in next(m for m in mine if m["kind"] == "FOLLOW_UP")["body"]
    assert not any(ch.isdigit() for m in mine if m["kind"] == "RFI" for ch in m["body"])   # field names only, never a value
    assert "RFI_REPLY" in {m["kind"] for m in (await b.get("/mailbox")).json()["items"]}


async def test_a_run_lost_on_restart_can_be_run_again_and_nothing_else_can(w):  # noqa: F811
    b, pid = await _until_buyer_is_asked(w)
    assert (await b.post(f"/projects/{pid}/rerun")).status_code == 409   # a healthy project is not "interrupted"
    async with dbm.session_scope() as s:
        await s.execute(update(Project).where(Project.id == uuid.UUID(pid)).values(status="STOPPED", reason="RESTART_LOST_RUNTIME"))
    assert (await (await w.client("outsider")).post(f"/projects/{pid}/rerun")).status_code in (403, 404)
    r = await b.post(f"/projects/{pid}/rerun")
    assert r.status_code == 201 and r.json()["id"] != pid
    await w.service.join(r.json()["id"])
    new = (await b.get(f"/projects/{r.json()['id']}")).json()
    assert new["title"].endswith("(run again)") and new["need_text"] == (await b.get(f"/projects/{pid}")).json()["need_text"]
