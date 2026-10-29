"""Typed bus messages. Every payload model is strict (extra=forbid): extra keys are how injected
fields would smuggle through."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.enums import StageEnum as S

Money = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=4)]
Country = Annotated[str, Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Brief(Strict):
    category: str = Field(max_length=80)
    industry: Literal["high_tech", "industrial", "consumer"]
    volume_units: int = Field(gt=0)
    required_certificates: tuple[str, ...] = ()
    excluded_countries: tuple[Country, ...] = ()
    max_lead_time_weeks: int = Field(gt=0, le=104)
    target_unit_price: Money | None = None
    diversify: bool = True
    spec_summary: str = Field(default="", max_length=1000)


class Invite(Strict):
    category: str
    volume_units: int = Field(gt=0)
    required_certificates: tuple[str, ...] = ()
    max_lead_time_weeks: int


class InterestReply(Strict):
    interested: bool
    capacity_units: int | None = Field(default=None, ge=0)
    reason_code: str | None = Field(default=None, max_length=40)


class DocRequest(Strict):
    required_certificates: tuple[str, ...]


class CertClaim(Strict):
    type: str
    claimed: bool = True


class DocSubmission(Strict):
    certificates: tuple[CertClaim, ...] = ()
    answers: dict[str, str] = Field(default_factory=dict, max_length=40)


class QualResult(Strict):
    status: Literal["QUALIFIED", "REJECTED", "REVIEW", "BLOCKED"]
    reasons: tuple[str, ...] = ()


class RFQ(Strict):
    category: str
    volume_units: int = Field(gt=0)
    incoterm: str = Field(default="FCA", max_length=8)
    target_lead_time_weeks: int = Field(gt=0, le=104)
    spec_summary: str = Field(default="", max_length=1000)
    disclosed_target_price: Money | None = None


class Offer(Strict):
    round: int = Field(ge=1, le=8)
    unit_price: Money
    lead_time_weeks: int = Field(gt=0, le=104)
    capacity_units: int = Field(gt=0)
    note: str = Field(default="", max_length=300)


class Counter(Offer):
    pass


class Accept(Strict):
    offer_id: UUID


class Reject(Strict):
    reason_code: str = Field(max_length=40)


class ContractDraft(Strict):
    clause_keys: tuple[str, ...]
    deviations: tuple[str, ...] = ()


class Redline(Strict):
    clause_key: str
    action: Literal["accept", "propose_alternative", "reject"]
    alternative_key: str | None = None


class SignRequest(Strict):
    contract_id: UUID


class OnboardData(Strict):
    fields: dict[str, str] = Field(max_length=40)


class Escalate(Strict):
    kind: str
    reasons: tuple[str, ...]


PAYLOADS: dict[T, type[Strict]] = {
    T.BRIEF: Brief, T.INVITE: Invite, T.INTEREST_REPLY: InterestReply, T.DOC_REQUEST: DocRequest,
    T.DOC_SUBMISSION: DocSubmission, T.QUAL_RESULT: QualResult, T.RFQ: RFQ, T.OFFER: Offer,
    T.COUNTER: Counter, T.ACCEPT: Accept, T.REJECT: Reject, T.CONTRACT_DRAFT: ContractDraft,
    T.REDLINE: Redline, T.SIGN_REQUEST: SignRequest, T.ONBOARD_DATA: OnboardData, T.ESCALATE: Escalate,
}

# Which message types may appear in which stage (ESCALATE and REJECT are valid anywhere work happens).
STAGE_TYPES: dict[S, frozenset[T]] = {
    S.NEED: frozenset({T.BRIEF, T.ESCALATE}),
    S.DISCOVERY: frozenset({T.INVITE, T.INTEREST_REPLY, T.ESCALATE}),
    S.QUALIFICATION: frozenset({T.DOC_REQUEST, T.DOC_SUBMISSION, T.QUAL_RESULT, T.ESCALATE}),
    S.SOURCING: frozenset({T.RFQ, T.OFFER, T.COUNTER, T.ACCEPT, T.REJECT, T.ESCALATE}),
    S.CONTRACTING: frozenset({T.CONTRACT_DRAFT, T.REDLINE, T.SIGN_REQUEST, T.ACCEPT, T.REJECT, T.ESCALATE}),
    S.ONBOARDING: frozenset({T.ONBOARD_DATA, T.ESCALATE}),
    S.DONE: frozenset(),
}


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    project_id: str
    seq: int = 0  # assigned by the bus
    stage: S
    type: T
    from_agent: str  # "buyer" | "supplier:<id>" | "orchestrator"
    to_agent: str
    mandate_ref: str | None = None
    payload: dict[str, Any]
    in_reply_to: UUID | None = None
    ts: datetime | None = None
    signature: str = ""

    def signing_body(self) -> dict[str, Any]:
        d = self.model_dump(mode="json", exclude={"signature", "seq", "ts"})
        return d


def parse_payload(t: T, payload: dict[str, Any]) -> Strict:
    """Raises ValidationError on schema violation; callers map to 422 `INVALID_PAYLOAD`."""
    return PAYLOADS[t].model_validate(payload)


def safe_parse_payload(t: T, payload: dict[str, Any]) -> tuple[Strict | None, str | None]:
    try:
        return parse_payload(t, payload), None
    except ValidationError as e:  # do not leak internals: only the first location
        loc = ".".join(str(x) for x in e.errors()[0]["loc"]) if e.errors() else "?"
        return None, f"INVALID_PAYLOAD:{loc}"
