"""Leverage: what a buyer may truthfully say about competing offers. Pure functions over Decimal.

A statement is built from the offers actually on the table and is a closed set of signals plus a ROUNDED price band. It never names a supplier,
never gives an exact price or terms. `verify` rebuilds the statement from the same facts and rejects any claim that is not true, so an agent
cannot bluff about a competitor that does not exist (the check is code, not a prompt)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sourceloop.audit.canonical import dumps, sha256_hex

PCT = Decimal("0.1")
MIN_LEAD_GAIN_WEEKS = 1
MIN_RELIABILITY_GAIN = 0.05
TEXT = {
    "OTHER_OFFERS": "other qualified offers are on the table",
    "PRICE_BELOW": "at least one of them is priced below this quote",
    "FASTER_DELIVERY": "at least one promises faster delivery",
    "MORE_RELIABLE": "at least one has a better delivery record",
    "PAID_LESS_BEFORE": "the buyer has paid less for this before",
}
BAND_TEXT = {"UP_TO_5": "by up to 5%", "5_TO_10": "by about 5 to 10%", "10_TO_20": "by about 10 to 20%", "OVER_20": "by more than 20%"}


@dataclass(frozen=True)
class Quote:
    supplier_id: str
    price: Decimal
    lead_weeks: int
    delay_p: float


@dataclass(frozen=True)
class Statement:
    signals: tuple[str, ...]
    price_gap_band: str | None
    based_on: int
    basis_hash: str   # fingerprint of the offers it was built from, so it can be proven later without naming anyone


def band(gap_pct: Decimal) -> str:
    return "UP_TO_5" if gap_pct <= 5 else "5_TO_10" if gap_pct <= 10 else "10_TO_20" if gap_pct <= 20 else "OVER_20"


def build(own: Quote, others: list[Quote], paid_before: Decimal | None = None) -> Statement | None:
    sig: list[str] = []
    gap_band: str | None = None
    if others:
        sig.append("OTHER_OFFERS")
        best = min(o.price for o in others)
        if best < own.price:
            sig.append("PRICE_BELOW")
            gap_band = band(((own.price - best) / own.price * 100).quantize(PCT))
        if any(o.lead_weeks <= own.lead_weeks - MIN_LEAD_GAIN_WEEKS for o in others):
            sig.append("FASTER_DELIVERY")
        if any(o.delay_p <= own.delay_p - MIN_RELIABILITY_GAIN for o in others):
            sig.append("MORE_RELIABLE")
    if paid_before is not None and paid_before < own.price:
        sig.append("PAID_LESS_BEFORE")
    if not sig:
        return None
    basis = sha256_hex(dumps(sorted([o.supplier_id, str(o.price), o.lead_weeks, round(o.delay_p, 4)] for o in others) + [str(paid_before)]))
    return Statement(tuple(sig), gap_band, len(others), basis)


def verify(claimed: Statement, own: Quote, others: list[Quote], paid_before: Decimal | None = None) -> list[str]:
    """Codes of every claim that the facts do not support (empty = the statement is true). A claim may be weaker than the facts, never stronger."""
    truth = build(own, others, paid_before)
    bad = [f"L_UNTRUE:{s}" for s in claimed.signals if truth is None or s not in truth.signals]
    if claimed.based_on > len(others):
        bad.append("L_UNTRUE:BASED_ON")
    if claimed.price_gap_band is not None and (truth is None or truth.price_gap_band is None or claimed.price_gap_band != truth.price_gap_band):
        bad.append("L_UNTRUE:BAND")
    return bad


def describe(signals: tuple[str, ...] | list[str], price_gap_band: str | None) -> str:
    parts = [TEXT[s] + (f" ({BAND_TEXT[price_gap_band]})" if s == "PRICE_BELOW" and price_gap_band else "") for s in signals if s in TEXT]
    return "The buyer said: " + "; ".join(parts) + "." if parts else ""
