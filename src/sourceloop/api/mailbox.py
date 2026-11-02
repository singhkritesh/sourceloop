"""The in-app mailbox: the email and text windows that stand in for real mail and SMS.

What the platform would send a person (a decision is waiting, a reminder, a request for information, a chase after silence) lands here, and the person
can answer a decision in the same window by replying "APPROVE" or "DECLINE <reason>". The reply goes through the very same resolve path as the web
button (`ProjectService.resolve_approval`): same role checks, same version lock, same audit. Nothing here carries prices, limits or counterparty names."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourceloop.data.enums import ApprovalStatusEnum as AS
from sourceloop.data.models import Approval, Mailbox, Project

KIND_TEXT = {
    "COMPLIANCE_FLAG": "review flagged suppliers", "TECHNICAL_QUAL": "confirm the technical shortlist", "AWARD": "choose the winning supplier",
    "CLAUSE": "review a changed contract clause", "HOLD_RELEASE": "confirm bank details and release payment",
    "SUPPLIER_ACCEPT": "confirm the final terms", "NEED_CONFIRM": "confirm the requirement"}
APPROVE_WORDS = {"approve", "approved", "yes", "ok", "okay", "confirm", "accept"}
DECLINE_WORDS = {"decline", "reject", "no", "deny", "stop"}
REMINDER_MAX = 3


def decision_text(kind: str) -> str:
    return KIND_TEXT.get(kind, kind.replace("_", " ").lower())


def parse_reply(text: str) -> tuple[AS | None, str]:
    """"APPROVE" -> (APPROVED, ""); "DECLINE too expensive for us" -> (REJECTED, "too expensive for us"); anything else -> (None, "")."""
    words = text.strip().split(None, 1)
    if not words:
        return None, ""
    head = words[0].strip(".,!:;").lower()
    rest = words[1].strip() if len(words) > 1 else ""
    if head in APPROVE_WORDS:
        return AS.APPROVED, rest
    if head in DECLINE_WORDS:
        return AS.REJECTED, rest
    return None, ""


def add(s: AsyncSession, now: datetime, *, org: uuid.UUID, role: str, channel: str, kind: str, subject: str, body: str,
        project: uuid.UUID | None = None, approval: uuid.UUID | None = None, direction: str = "IN") -> Mailbox:
    m = Mailbox(owner_org_id=org, owner_role=role, channel=channel, direction=direction, kind=kind, subject=subject[:200], body=body[:1000],
                project_id=project, approval_id=approval, created_at=now)
    s.add(m)
    return m


def post_decision(s: AsyncSession, now: datetime, *, org: uuid.UUID, role: str, kind: str, project: uuid.UUID, approval: uuid.UUID,
                  base_url: str, reminder: int = 0) -> None:
    """One email and one text for a decision that is waiting. Both name only the kind of decision and where to answer it."""
    what = decision_text(kind)
    link = f"{base_url.rstrip('/')}/projects/{project}"
    lead = f"Reminder {reminder}: " if reminder else ""
    k = "REMINDER" if reminder else "DECISION"
    add(s, now, org=org, role=role, channel="EMAIL", kind=k, project=project, approval=approval,
        subject=f"{lead}Your decision is needed: {what}",
        body=f"A sourcing project is waiting for you to {what}.\n\nOpen it: {link}\nOr just reply to this message with APPROVE, or DECLINE and a reason.")
    if not reminder:
        add(s, now, org=org, role=role, channel="SMS", kind=k, project=project, approval=approval, subject="",
            body=f"SourceLoop: please {what}. Reply APPROVE or DECLINE <reason>.")


async def due_reminders(s: AsyncSession, now: datetime, after_hours: int) -> list[tuple[Approval, uuid.UUID, str, int]]:
    """PENDING decisions that have waited `after_hours` since they were raised or last reminded, and have been reminded fewer than REMINDER_MAX
    times. Returns (approval, owner org, owner role, next reminder number)."""
    out: list[tuple[Approval, uuid.UUID, str, int]] = []
    pend = (await s.execute(select(Approval).where(Approval.status == AS.PENDING))).scalars().all()
    for a in pend:
        first = (await s.execute(select(Mailbox).where(Mailbox.approval_id == a.id, Mailbox.kind == "DECISION", Mailbox.channel == "EMAIL")
                                 .order_by(Mailbox.created_at).limit(1))).scalar_one_or_none()
        if first is None:
            continue
        n, last = (await s.execute(select(func.count(), func.max(Mailbox.created_at)).where(
            Mailbox.approval_id == a.id, Mailbox.kind == "REMINDER"))).one()
        if n >= REMINDER_MAX:
            continue
        if now - (last or first.created_at) >= timedelta(hours=after_hours):
            out.append((a, first.owner_org_id, first.owner_role, n + 1))
    return out


async def project_title(s: AsyncSession, pid: uuid.UUID) -> str:
    return (await s.execute(select(Project.title).where(Project.id == pid))).scalar_one_or_none() or ""


def out(m: Mailbox) -> dict[str, Any]:
    return {"id": str(m.id), "channel": m.channel, "direction": m.direction, "kind": m.kind, "subject": m.subject, "body": m.body,
            "project_id": str(m.project_id) if m.project_id else None, "approval_id": str(m.approval_id) if m.approval_id else None,
            "read": m.read_at is not None, "created_at": m.created_at.isoformat()}
