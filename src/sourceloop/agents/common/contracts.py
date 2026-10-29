"""Typed tool contracts shared by lead agents (untrusted side) and subagent implementations (trusted side).
Lead agents know only tool NAMES and these models; implementations close over private data."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---- tool names ----
RESEARCH = "research_suppliers"
COMPLIANCE = "compliance_screen"
NEWS = "regional_news_risk"
WEATHER = "weather_logistics"
LANDED = "landed_cost"
QUOTE = "quote_generation"
BUYER_VERIFY = "buyer_verification"
CAPACITY = "capacity_margin"
DEMAND = "demand_forecast"
DOCS = "compliance_docs"


class C(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NeedSpec(C):
    id: str
    category: str
    industry: str
    volume_units: int
    required_certs: tuple[str, ...] = ()
    excluded_countries: tuple[str, ...] = ()
    max_lead_time_weeks: int
    max_financial_risk: float = 0.6
    min_years_in_business: int = 3


# ---- buyer side ----
class ResearchIn(C):
    need: NeedSpec
    longlist_size: int = 10
    min_countries: int = 2


class Cand(C):
    supplier_id: str
    country: str
    score: float
    status: str
    reasons: tuple[str, ...] = ()


class ResearchOut(C):
    candidates: tuple[Cand, ...]
    considered: int
    merged_duplicates: int
    filtered: dict[str, int]


class ComplianceIn(C):
    need: NeedSpec
    supplier_id: str


class ComplianceOut(C):
    supplier_id: str
    status: Literal["QUALIFIED", "REJECTED", "REVIEW", "BLOCKED"]
    reasons: tuple[str, ...]


class NewsIn(C):
    country: str = Field(pattern=r"^[A-Z]{2}$")
    horizon_days: int = 90


class NewsSignal(C):
    kind: Literal["strike", "unrest", "port_closure", "sanction", "regulation", "weather", "none"]
    severity: float = Field(ge=0, le=1)
    article_index: int = Field(ge=0)


class NewsExtraction(C):
    """Schema the LLM must fill. Free text is not part of the schema on purpose."""
    signals: tuple[NewsSignal, ...]


class NewsOut(C):
    country: str
    max_severity: float
    kinds: tuple[str, ...]
    injection_suspected: bool = False


class WeatherIn(C):
    origin_country: str
    lead_time_weeks: int


class WeatherOut(C):
    delay_probability: float = Field(ge=0, le=1)
    drivers: tuple[str, ...] = ()


class LandedIn(C):
    unit_price: Decimal
    origin_country: str
    delay_probability: float
    delay_penalty_pct: Decimal = Decimal("20")


class LandedOut(C):
    unit_price: Decimal
    freight: Decimal
    duty: Decimal
    risk_cost: Decimal
    total: Decimal


# ---- supplier side ----
class BuyerVerifyIn(C):
    buyer_org: str


class BuyerVerifyOut(C):
    rating: float
    acceptable: bool
    flags: tuple[str, ...] = ()


class CapacityIn(C):
    volume_units: int
    target_lead_time_weeks: int


class CapacityOut(C):
    feasible: bool
    offered_capacity: int
    lead_time_weeks: int
    reason_code: str | None = None


class DemandIn(C):
    horizon_weeks: int = 12


class DemandOut(C):
    load_pct: float


class QuoteIn(C):
    volume_units: int
    lead_time_weeks: int
    capacity_units: int
    index_price: Decimal | None = None


class QuoteOut(C):
    ladder: tuple[Decimal, ...]  # descending price rungs, last rung = floor; PRIVATE to the supplier agent
    lead_time_weeks: int
    capacity_units: int
    rationale: str = ""


class DocsIn(C):
    required_certificates: tuple[str, ...]


class DocsOut(C):
    claims: tuple[str, ...]
