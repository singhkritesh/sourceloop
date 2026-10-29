"""Shared helpers for the adversarial suite."""
from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from sourceloop.bus.bus import Delivered, Rejected
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.enums import StageEnum as S

# a representative valid payload per message type (used when only routing/signing is under test)
VALID: dict[T, dict[str, Any]] = {
    T.BRIEF: {"category": "pcb_assembly", "industry": "high_tech", "volume_units": 100, "max_lead_time_weeks": 10},
    T.INVITE: {"category": "pcb_assembly", "volume_units": 100, "max_lead_time_weeks": 10},
    T.INTEREST_REPLY: {"interested": True, "capacity_units": 100},
    T.DOC_REQUEST: {"required_certificates": ["ISO9001"]},
    T.DOC_SUBMISSION: {"certificates": [{"type": "ISO9001", "claimed": True}]},
    T.QUAL_RESULT: {"status": "REVIEW", "reasons": ["X"]},
    T.RFQ: {"category": "pcb_assembly", "volume_units": 100, "target_lead_time_weeks": 10},
    T.OFFER: {"round": 1, "unit_price": "100", "lead_time_weeks": 8, "capacity_units": 500},
    T.COUNTER: {"round": 1, "unit_price": "100", "lead_time_weeks": 8, "capacity_units": 500},
    T.ACCEPT: {"offer_id": "00000000-0000-0000-0000-000000000001"},
    T.REJECT: {"reason_code": "X"},
    T.CONTRACT_DRAFT: {"clause_keys": ["payment_terms"]},
    T.REDLINE: {"clause_key": "payment_terms", "action": "accept"},
    T.SIGN_REQUEST: {"contract_id": "00000000-0000-0000-0000-000000000001"},
    T.ONBOARD_DATA: {"fields": {"a": "b"}},
    T.ESCALATE: {"kind": "X", "reasons": ["y"]},
}


def offer(price: str | Decimal, rnd: int = 1, lead: int = 8, cap: int = 500) -> dict[str, Any]:
    return {"round": rnd, "unit_price": str(price), "lead_time_weeks": lead, "capacity_units": cap}


def stage_for(t: T) -> S:
    from sourceloop.bus.messages import STAGE_TYPES
    return next(s for s in (S.NEED, S.DISCOVERY, S.QUALIFICATION, S.SOURCING, S.CONTRACTING, S.ONBOARDING)
                if t in STAGE_TYPES[s])


def assert_rejected(res, code: str, rule: str | None = None):
    assert isinstance(res, Rejected), f"expected Rejected({code}) got {res!r}"
    assert res.code == code, res
    if rule is not None:
        assert res.rule_id == rule, res


def assert_delivered(res):
    assert isinstance(res, Delivered), f"expected Delivered got {res!r}"


def blob(obj: Any) -> str:
    """Everything printable about an object, for threshold scanning."""
    def d(o):
        if hasattr(o, "__dataclass_fields__"):
            return asdict(o)
        if hasattr(o, "model_dump"):
            return o.model_dump(mode="json")
        return str(o)
    return json.dumps(obj, default=d, sort_keys=True)


def number_forms(x: Decimal) -> set[str]:
    """Textual forms a decimal threshold could leak as (4dp, normalised, plain)."""
    q = x.quantize(Decimal("0.0001"))
    forms = {str(q), format(q.normalize(), "f"), str(x)}
    return {f for f in forms if len(f) >= 4}
