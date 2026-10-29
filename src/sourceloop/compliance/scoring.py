"""Candidate scoring and diversified shortlist selection (pure, deterministic)."""
from __future__ import annotations

from dataclasses import dataclass

from sourceloop.data.registry import Need, SupplierRec

DEFAULT_WEIGHTS = {"quality": 0.25, "on_time": 0.20, "financial": 0.20, "lead": 0.15, "capacity": 0.10, "network": 0.10}
_NETWORK = {"member": 1.0, "invited": 0.6, "external": 0.3}


@dataclass(frozen=True, slots=True)
class Scored:
    supplier_id: str
    country: str
    score: float
    reasons: tuple[str, ...]


def score(s: SupplierRec, need: Need, weights: dict[str, float] | None = None) -> Scored:
    w = weights or DEFAULT_WEIGHTS
    q = s.quality_score if s.quality_score is not None else 0.5
    ot = s.on_time_pct if s.on_time_pct is not None else 0.5
    fin = 1 - (s.financial_risk if s.financial_risk is not None else 0.5)
    lead = max(0.0, 1 - ((s.lead_time_weeks or need.max_lead_time_weeks) / max(need.max_lead_time_weeks, 1))) if s.lead_time_weeks else 0.3
    cap = min(1.0, ((s.capacity_units or 0) / max(need.volume_units, 1)) / 3)
    net = _NETWORK.get(s.network_status, 0.3)
    total = w["quality"] * q + w["on_time"] * ot + w["financial"] * fin + w["lead"] * lead + w["capacity"] * cap + w["network"] * net
    reasons = [f"quality {q:.2f}", f"on-time {ot:.2f}", f"financial {fin:.2f}"]
    if s.on_time_pct is None:
        reasons.append("no delivery history (neutral)")
    return Scored(s.id, s.country, round(total, 6), tuple(reasons))


def shortlist(scored: list[Scored], k: int = 5, min_countries: int = 2) -> list[Scored]:
    """Top-k by score, but guarantee country diversity when the pool allows it."""
    ranked = sorted(scored, key=lambda x: (-x.score, x.supplier_id))
    pick = ranked[:k]
    countries = {p.country for p in pick}
    if len(countries) < min_countries and len({s.country for s in ranked}) >= min_countries:
        for cand in ranked[k:]:
            if cand.country not in countries:
                pick[-1] = cand  # swap in best candidate from a new country
                break
    return sorted(pick, key=lambda x: (-x.score, x.supplier_id))
