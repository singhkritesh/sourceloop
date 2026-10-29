"""Mandates: signed, immutable limits an agent may not exceed. Thresholds never cross the side boundary."""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sourceloop.bus.messages import Country, Money
from sourceloop.bus.signing import KeyRing

LOCKED_CLAUSES_DEFAULT: tuple[str, ...] = ("liability", "termination", "ip")  # invariant: always locked

Autonomy = Annotated[int, Field(ge=1, le=4)]


class BuyerMandate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    side: Literal["buyer"] = "buyer"
    max_unit_price: Money
    target_unit_price: Money | None = None
    max_lead_time_weeks: int = Field(gt=0, le=104)
    max_rounds: int = Field(default=4, ge=1, le=8)
    min_step_pct: Decimal = Field(default=Decimal("0.5"), ge=0, le=20)
    excluded_countries: tuple[Country, ...] = ()
    locked_clauses: tuple[str, ...] = LOCKED_CLAUSES_DEFAULT
    autonomy: dict[str, Autonomy] = Field(default_factory=dict)
    award_value_cap: Money | None = None
    disclose_target: bool = False
    price_band_pct: Decimal = Field(default=Decimal("15"), gt=0, le=100)

    @model_validator(mode="after")
    def _locked_invariant(self) -> BuyerMandate:
        missing = set(LOCKED_CLAUSES_DEFAULT) - set(self.locked_clauses)
        if missing:  # INV_LOCKED_CLAUSES: cannot be configured away
            raise ValueError(f"locked clauses cannot be removed: {sorted(missing)}")
        return self


class SupplierMandate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    side: Literal["supplier"] = "supplier"
    unit_cost: Money
    min_margin_pct: Decimal = Field(ge=0, le=500)
    max_capacity_units: int = Field(gt=0)
    lead_time_weeks: int = Field(gt=0, le=104)
    provable_certificates: tuple[str, ...] = ()
    unchangeable_clauses: tuple[str, ...] = LOCKED_CLAUSES_DEFAULT
    min_buyer_rating: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    max_rounds: int = Field(default=4, ge=1, le=8)
    min_step_pct: Decimal = Field(default=Decimal("0.5"), ge=0, le=20)

    @property
    def min_unit_price(self) -> Decimal:
        return (self.unit_cost * (1 + self.min_margin_pct / 100)).quantize(Decimal("0.0001"))


Mandate = BuyerMandate | SupplierMandate


class SignedMandate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: str
    version: int
    payload: dict
    signature: str

    @classmethod
    def create(cls, ring: KeyRing, org_id: str, version: int, mandate: Mandate) -> SignedMandate:
        payload = mandate.model_dump(mode="json")
        body = {"org_id": org_id, "version": version, "payload": payload}
        return cls(org_id=org_id, version=version, payload=payload, signature=ring.sign_payload(org_id, body))

    def verify(self, ring: KeyRing) -> Mandate | None:
        body = {"org_id": self.org_id, "version": self.version, "payload": self.payload}
        if not ring.verify_payload(self.org_id, body, self.signature):
            return None
        side = self.payload.get("side")
        model = BuyerMandate if side == "buyer" else SupplierMandate
        return model.model_validate(self.payload)


def overlap(buyer: BuyerMandate, supplier: SupplierMandate) -> bool:
    """Predicate probe: can any deal exist? Returns a bare bool so no threshold is revealed."""
    return (
        buyer.max_unit_price >= supplier.min_unit_price
        and supplier.lead_time_weeks <= buyer.max_lead_time_weeks
    )
