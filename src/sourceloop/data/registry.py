"""Registry records (the 'verified supplier network') and a fast in-memory index."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Cert:
    type: str
    issuer: str
    expires_on: date


@dataclass(frozen=True, slots=True)
class SupplierRec:
    id: str
    name: str
    country: str
    tax_id: str | None
    categories: tuple[str, ...]
    certs: tuple[Cert, ...] = ()
    claimed_certs: tuple[str, ...] = ()       # free-text claims NOT in the registry
    capacity_units: int | None = None
    lead_time_weeks: int | None = None
    financial_risk: float | None = None       # 0 good .. 1 bad
    years_in_business: int | None = None
    forced_labor_flag: bool = False
    subtier_origin: tuple[str, ...] = ()
    network_status: str = "member"            # member | invited | external
    aliases: tuple[str, ...] = ()
    on_time_pct: float | None = None          # SBN transaction history (the data rivals lack)
    quality_score: float | None = None
    unit_cost: Decimal | None = None


@dataclass(frozen=True, slots=True)
class WatchEntry:
    id: str
    name: str
    country: str
    list_name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Need:
    id: str
    category: str
    industry: str
    volume_units: int
    required_certs: tuple[str, ...]
    excluded_countries: tuple[str, ...]
    max_lead_time_weeks: int
    max_financial_risk: float = 0.6
    min_years_in_business: int = 3
    target_unit_price: Decimal | None = None


@dataclass
class Registry:
    suppliers: list[SupplierRec]
    watchlist: list[WatchEntry]
    index_price: dict[str, Decimal] = field(default_factory=dict)  # category -> index price
    _by_id: dict[str, SupplierRec] = field(init=False, default_factory=dict)
    _by_cat: dict[str, list[SupplierRec]] = field(init=False, default_factory=lambda: defaultdict(list))

    def __post_init__(self) -> None:
        for s in self.suppliers:
            self._by_id[s.id] = s
            for c in s.categories:
                self._by_cat[c].append(s)

    def get(self, sid: str) -> SupplierRec | None:
        return self._by_id.get(sid)

    def in_category(self, category: str) -> list[SupplierRec]:
        return self._by_cat.get(category, [])


@dataclass
class AnswerKey:
    """Hidden ground truth. Agents never receive this; only evaluation code does."""

    duplicate_pairs: set[frozenset[str]] = field(default_factory=set)
    sanctioned_ids: set[str] = field(default_factory=set)
    lookalike_pairs: set[frozenset[str]] = field(default_factory=set)
    canonical_of: dict[str, str] = field(default_factory=dict)  # dup id -> canonical id
