"""Rules-only qualification. LLM output NEVER sets these statuses (invariant).

Fail closed: missing mandatory data => REVIEW, never pass. Watchlist hit => BLOCKED, never auto-cleared.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isnan

from rapidfuzz import fuzz, process

from sourceloop.compliance.resolution import normalize_name
from sourceloop.data.enums import QualStatusEnum as Q
from sourceloop.data.registry import Need, SupplierRec, WatchEntry

HIGH_RISK_ORIGINS = frozenset({"XJ"})  # synthetic sub-tier origin code for forced-labor-risk regions
_RANK = {Q.BLOCKED: 3, Q.REJECTED: 2, Q.REVIEW: 1, Q.QUALIFIED: 0}


@dataclass(frozen=True, slots=True)
class QualDecision:
    supplier_id: str
    status: Q
    reasons: tuple[str, ...]


def _sim(a: str, b: str, **_: object) -> float:
    """Typo-robust but not partial-match-happy: max of plain and token-order-insensitive ratios."""
    return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b))


class WatchlistIndex:
    def __init__(self, entries: list[WatchEntry], cutoff: float = 88.0) -> None:
        self.cutoff = cutoff
        self._names: list[str] = []
        self._ids: list[str] = []
        for e in entries:
            for n in (e.name, *e.aliases):
                self._names.append(normalize_name(n))
                self._ids.append(e.id)

    def hit(self, name: str, aliases: tuple[str, ...] = ()) -> str | None:
        for n in (name, *aliases):
            m = process.extractOne(normalize_name(n), self._names, scorer=_sim, score_cutoff=self.cutoff)
            if m is not None:
                return self._ids[m[2]]
        return None


def qualify(s: SupplierRec, need: Need, wl: WatchlistIndex, today: date) -> QualDecision:
    reasons: list[tuple[Q, str]] = []

    if wl.hit(s.name, s.aliases) is not None:
        reasons.append((Q.BLOCKED, "WATCHLIST_HIT"))
    if s.forced_labor_flag or HIGH_RISK_ORIGINS & set(s.subtier_origin):
        reasons.append((Q.BLOCKED, "FORCED_LABOR_RISK"))
    if s.country in need.excluded_countries:
        reasons.append((Q.REJECTED, "EXCLUDED_COUNTRY"))
    if need.category not in s.categories:
        reasons.append((Q.REJECTED, "CATEGORY_MISMATCH"))

    registry = {c.type: c for c in s.certs}
    for ct in need.required_certs:
        c = registry.get(ct)
        if c is None:
            reasons.append((Q.REVIEW, f"UNVERIFIED_CLAIM:{ct}") if ct in s.claimed_certs
                           else (Q.REJECTED, f"CERT_MISSING:{ct}"))
        elif c.expires_on < today:
            reasons.append((Q.REJECTED, f"CERT_EXPIRED:{ct}"))

    mandatory = {"capacity_units": s.capacity_units, "lead_time_weeks": s.lead_time_weeks,
                 "financial_risk": s.financial_risk, "years_in_business": s.years_in_business}
    missing = [k for k, v in mandatory.items() if v is None or (isinstance(v, float) and isnan(v))]  # NaN is missing
    if missing:
        reasons.append((Q.REVIEW, "MISSING_DATA:" + ",".join(missing)))
    else:
        if not (s.lead_time_weeks <= need.max_lead_time_weeks):  # type: ignore[operator]
            reasons.append((Q.REJECTED, "LEAD_TIME"))
        if not (s.capacity_units >= need.volume_units):  # type: ignore[operator]
            reasons.append((Q.REJECTED, "CAPACITY"))
        if not (s.financial_risk <= need.max_financial_risk):  # type: ignore[operator]
            reasons.append((Q.REJECTED, "FINANCIAL_RISK"))
        if not (s.years_in_business >= need.min_years_in_business):  # type: ignore[operator]
            reasons.append((Q.REJECTED, "YEARS_IN_BUSINESS"))

    status = max((r[0] for r in reasons), key=_RANK.__getitem__, default=Q.QUALIFIED)
    return QualDecision(s.id, status, tuple(r[1] for r in reasons))
