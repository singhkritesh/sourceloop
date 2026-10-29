"""Human approval / escalation service. Idempotent per (project, kind, key); never auto-approves."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sourceloop.audit.canonical import dumps, sha256_hex
from sourceloop.audit.chain import AuditLog
from sourceloop.data.enums import ApprovalKindEnum as K
from sourceloop.data.enums import ApprovalStatusEnum as S
from sourceloop.data.enums import RoleEnum as R

KIND_ROLE: dict[K, R] = {
    K.REQUIREMENT: R.BUYER, K.COMPLIANCE_FLAG: R.COMPLIANCE, K.TECHNICAL_QUAL: R.QUALITY, K.AWARD: R.BUYER,
    K.CLAUSE: R.LEGAL, K.SIGNATURE: R.LEGAL, K.BANK_DETAILS: R.FINANCE, K.HOLD_RELEASE: R.FINANCE,
    K.MANDATE_BREACH: R.BUYER, K.LOW_CONFIDENCE: R.BUYER, K.LLM_UNAVAILABLE: R.ADMIN, K.STALLED: R.ADMIN,
    K.BUDGET: R.ADMIN, K.RULE: R.BUYER,
}


EDITABLE_KINDS = frozenset({K.COMPLIANCE_FLAG, K.TECHNICAL_QUAL, K.AWARD, K.CLAUSE})  # kinds whose EDITED carries a payload


class ApprovalEditError(ValueError):
    """Defense in depth behind the API's per-kind edit validation; the API maps it to 422 INVALID_EDIT."""

    code = "INVALID_EDIT"


# Review SLA (hours) by approval kind. Defaults only: the Studio schema has no SLA field yet (documented deviation); a
# future `workflow.sla_hours` read from the PINNED config should override these. OVERDUE is derived, never stored.
DEFAULT_SLA_HOURS = 24
SLA_HOURS: dict[K, int] = {K.BANK_DETAILS: 8, K.HOLD_RELEASE: 8, K.SIGNATURE: 24, K.AWARD: 24}


def sla_fields(kind: K, status: S, created_at: Any, now: Any) -> dict[str, Any]:
    """Computed `due_at` / `overdue` for an approvals list row (`created_at`/`now` are aware datetimes)."""
    from datetime import timedelta

    due = created_at + timedelta(hours=SLA_HOURS.get(kind, DEFAULT_SLA_HOURS))
    return {"due_at": due.isoformat(), "overdue": status is S.PENDING and now > due}


class ApprovalError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Approval:
    id: str
    project_id: str
    kind: K
    key: str
    target_role: R
    reasons: tuple[str, ...]
    evidence: dict[str, Any]
    recommendation: dict[str, Any]
    status: S = S.PENDING
    resolver_id: str | None = None
    comment: str = ""
    version: int = 1
    stage: str = ""
    edit: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalService:
    audit: AuditLog
    _items: dict[str, Approval] = field(default_factory=dict)
    _by_key: dict[tuple[str, str, str], str] = field(default_factory=dict)
    _n: int = 0
    decisions: dict[tuple[str, str], list[S]] = field(default_factory=lambda: defaultdict(list))  # (role, resolver) -> statuses

    async def ensure(self, project_id: str, kind: K, key: str, reasons: tuple[str, ...], evidence: dict[str, Any] | None = None,
                     recommendation: dict[str, Any] | None = None, stage: str = "") -> Approval:
        k = (project_id, kind.value, key)
        if k in self._by_key:
            return self._items[self._by_key[k]]
        self._n += 1
        a = Approval(f"A{self._n:04d}", project_id, kind, key, KIND_ROLE[kind], reasons, evidence or {},
                     recommendation or {}, stage=stage)
        self._items[a.id], self._by_key[k] = a, a.id
        await self.audit.append(project_id, actor_type="orchestrator", actor_id="orchestrator", action="escalation_created",
                                rule_id=kind.value, evidence=[{"approval": a.id, "role": a.target_role.value, "reasons": list(reasons)}],
                                outcome="PENDING")
        return a

    def pending(self, project_id: str) -> list[Approval]:
        return [a for a in self._items.values() if a.project_id == project_id and a.status is S.PENDING]

    def all(self, project_id: str) -> list[Approval]:
        return [a for a in self._items.values() if a.project_id == project_id]

    def get(self, aid: str) -> Approval:
        return self._items[aid]

    async def resolve(self, aid: str, status: S, resolver_id: str, role: R, comment: str = "", version: int | None = None,
                      edit: dict[str, Any] | None = None) -> Approval:
        a = self._items[aid]
        if a.status is not S.PENDING or (version is not None and version != a.version):
            raise ApprovalError("APPROVAL_ALREADY_RESOLVED")
        if role is not a.target_role:  # only the target role decides; ADMIN cannot approve compliance/legal items
            raise ApprovalError("FORBIDDEN")
        if status is S.PENDING:
            raise ApprovalError("INVALID_STATUS")
        if status in (S.REJECTED, S.EDITED) and len(comment.strip()) < 10:
            raise ApprovalError("COMMENT_REQUIRED")
        edit = edit or {}
        if status is S.EDITED and (a.kind not in EDITABLE_KINDS or not edit):
            raise ApprovalEditError("EDITED requires a non-empty edit, and only for kinds that take one")
        if status is not S.EDITED and edit:
            raise ApprovalEditError("edit is only allowed with status EDITED")
        # rule 10: the audit event (binding the exact edit + comment by hash) is durable BEFORE the state mutates
        await self.audit.append(a.project_id, actor_type="human", actor_id=resolver_id, action="approval_resolved",
                                rule_id=a.kind.value,
                                evidence=[{"approval": a.id, "decision": status.value, "edit_keys": sorted(str(k) for k in edit),
                                           "decision_hash": sha256_hex(dumps({"edit": edit, "comment": comment}))}],
                                outcome=status.value, approver_id=resolver_id)
        a.status, a.resolver_id, a.comment, a.version = status, resolver_id, comment, a.version + 1
        a.edit = edit
        self.decisions[(a.target_role.value, resolver_id)].append(status)
        return a

    def override_rates(self, min_decisions: int = 20, threshold: float = 0.02) -> list[dict[str, Any]]:
        out = []
        for (role, who), sts in self.decisions.items():
            n = len(sts)
            rate = sum(s is not S.APPROVED for s in sts) / n
            out.append({"role": role, "resolver": who, "decisions": n, "override_rate": round(rate, 3),
                        "flag": "POSSIBLE_RUBBER_STAMP" if n >= min_decisions and rate < threshold else None})
        return out
