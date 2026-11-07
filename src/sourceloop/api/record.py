"""The project record in plain sentences, for the person who is allowed to see it (business users, not inspectors).

Built by CODE from audit events: only the action, the outcome, the rule id and a few named fields are read, never the raw evidence, so no price,
limit, hash or other side's detail can reach the sentence. A supplier is handed only the events its own view of the chain contains (the caller
filters), and every mention of a supplier other than itself is dropped there. The inspector audit list stays as it is.
"""
from __future__ import annotations

from typing import Any

from sourceloop.api.negotiation_summary import MOVE_TEXT

_DECISION = {"APPROVED": "approved", "REJECTED": "declined", "EDITED": "changed and approved"}
_KIND = {
    "COMPLIANCE_FLAG": "the flagged supplier", "TECHNICAL_QUAL": "the technical shortlist", "AWARD": "the award", "CLAUSE": "a contract clause change",
    "SIGNATURE": "the contract for signature", "SUPPLIER_ACCEPT": "the final terms", "HOLD_RELEASE": "the bank details and payment release",
    "MANDATE_BREACH": "a blocked attempt to exceed a limit", "REQUIREMENT": "the requirement", "RFI": "a request for information", "FOLLOW_UP": "a reminder",
}
_MESSAGE = {
    "BRIEF": "the sourcing brief", "INVITE": "an invitation to quote", "INTEREST_REPLY": "an interest reply", "DOC_REQUEST": "a document request",
    "DOC_SUBMISSION": "documents", "RFI": "a request for information", "RFI_REPLY": "information", "FOLLOW_UP": "a reminder", "QUAL_RESULT": "a qualification result",
    "RFQ": "a request for quote", "OFFER": "an offer", "COUNTER": "a counter-offer", "ACCEPT": "an acceptance", "REJECT": "a refusal", "LEVERAGE": "a note about competing offers",
    "CONTRACT_DRAFT": "a contract draft", "REDLINE": "a contract change request", "SIGN_REQUEST": "a signature request", "ONBOARD_DATA": "onboarding details",
    "ESCALATE": "an escalation",
}
_END = {"DEAL": "a deal was reached", "NO_DEAL": "no deal was reached", "CLOSED": "the project is closed"}


def _ev(e: dict[str, Any]) -> dict[str, Any]:
    ev = e.get("evidence")
    return ev[0] if isinstance(ev, list) and ev and isinstance(ev[0], dict) else {}


def _party(token: Any, names: dict[str, str], mine: set[str] | None) -> str:
    """'buyer', 'supplier:S1' or 'orchestrator' as a reader-facing name; for a supplier reader its own id reads 'you'."""
    t = str(token or "")
    if t.startswith("supplier:") or t in names:
        sid = t.split(":", 1)[-1]
        if mine is not None:
            return "you" if sid in mine else "another supplier"
        return names.get(sid, "a supplier")
    return "the buyer" if t == "buyer" else "the platform" if t == "orchestrator" else "an agent"


def describe(e: dict[str, Any], names: dict[str, str], mine: set[str] | None = None) -> tuple[str, str]:
    """(who, what) for one audit event. `mine` is set for a supplier reader."""
    a, out, rule, ev = str(e.get("action") or ""), str(e.get("outcome") or ""), e.get("rule_id"), _ev(e)
    kind = _KIND.get(str(rule), "a decision")
    if a == "run_started":
        return "Platform", "The sourcing run started."
    if a == "stage_changed":
        return "Platform", f"Moved to the {out.replace('_', ' ').lower()} stage."
    if a == "message_delivered":
        frm, to = _party(ev.get("from"), names, mine), _party(ev.get("to"), names, mine)
        what = _MESSAGE.get(str(ev.get("type")), "a message")
        return "Agents", f"{frm.capitalize()} sent {what} to {to}."
    if a == "checkpoint_skipped":
        return "Platform", "A step that needed no person was settled by a rule and recorded."
    if a == "discovery_done":
        return "Buyer's agent", f"Looked at {ev.get('considered', 'the')} suppliers and shortlisted {ev.get('longlist', 'some')}."
    if a == "rfi_round":
        return "Buyer's agent", f"Asked suppliers for missing information; {len(ev.get('answered') or [])} answered."
    if a == "leverage_stated":
        return "Buyer's agent", "Mentioned competing offers to a supplier, in rounded terms and without naming anyone."
    if a == "leverage_blocked":
        return "Platform", "A claim about competing offers was held back because the facts did not support it."
    if a == "agent_decision":
        who = "Supplier's agent" if str(ev.get("agent", "")).startswith("supplier") else "Buyer's agent"
        by = "the language model" if ev.get("source") == "model" else "the standing rule"
        return who, f"Chose {MOVE_TEXT.get(str(ev.get('chosen')), 'its next step')}, picked by {by}."
    if a == "negotiation_judged":
        who = "Supplier's agent" if ev.get("actor") == "supplier" else "Buyer's agent"
        return "Platform", f"Conduct check on {who.lower().replace(chr(39) + 's agent', ' side')}, round {ev.get('round', '?')}: {out.replace('_', ' ').lower()}."
    if a == "escalation_created":
        return "Platform", f"A person was asked to decide on {kind}."
    if a == "run_paused":
        return "Platform", f"The run paused until a person decides on {kind}."
    if a == "run_resumed":
        return "Platform", "The run continued."
    if a == "approval_resolved":
        return "A person", f"{_DECISION.get(out, out.lower())} {kind}.".capitalize()
    if a == "bank_checked":
        return "Platform", f"Bank details checked: {'passed' if out == 'PASS' else 'did not pass'}."
    if a == "ledger_hold_created":
        return "Platform", "A payment hold was placed for the winning supplier."
    if a == "run_finished":
        return "Platform", f"The run finished: {_END.get(out, out.replace('_', ' ').lower())}."
    if a == "closed":
        return "Platform", f"The project closed for you: {_END.get(out, 'closed')}."
    if a == "run_stopped":
        return "Platform", "The run was stopped."
    return "Platform", a.replace("_", " ").capitalize() + "."
