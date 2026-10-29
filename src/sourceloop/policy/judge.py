"""Negotiation judge: is this turn appropriate, given the round and what both sides have done so far?

Pure function over structured facts (no mandate values, no IO), so it can sit on the neutral orchestrator and its verdict is a
deterministic part of the audit chain. It judges *conduct* (direction of movement, stalling, lowballing, crossing, giving up early,
paying far above the reference), not whether a price is inside a mandate: that stays with `policy.engine`.

Verdicts cross the side boundary as codes only, never as prices or thresholds (CLAUDE.md rule 3).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Level(StrEnum):
    APPROPRIATE = "APPROPRIATE"
    CONCERN = "CONCERN"                # recorded and shown; the negotiation continues
    INAPPROPRIATE = "INAPPROPRIATE"    # the candidate is dropped (audited); a person can re-open it by re-running


_ORDER = {Level.APPROPRIATE: 0, Level.CONCERN: 1, Level.INAPPROPRIATE: 2}

# code -> (level, plain-language reason). Friendly text lives with the code so the UI never invents its own.
CODES: dict[str, tuple[Level, str]] = {
    "J_REGRESSION": (Level.INAPPROPRIATE, "moved back against its own earlier position"),
    "J_CROSSED": (Level.INAPPROPRIATE, "countered at or beyond the other side's price instead of accepting"),
    "J_LOWBALL_EXTREME": (Level.INAPPROPRIATE, "countered far below the other side's price"),
    "J_ROUND_EXCEEDED": (Level.INAPPROPRIATE, "acted beyond the allowed number of rounds"),
    "J_LOWBALL": (Level.CONCERN, "countered well below the other side's price"),
    "J_STALL": (Level.CONCERN, "repeated its previous price without moving"),
    "J_TOKEN_MOVE": (Level.CONCERN, "moved by less than a meaningful step"),
    "J_EARLY_EXIT": (Level.CONCERN, "ended the negotiation before using all rounds"),
    "J_OVERPAY": (Level.CONCERN, "accepted a price far above the market reference"),
    "J_LLM_REVIEW": (Level.CONCERN, "the reviewer model flagged this turn"),
}

LOWBALL_EXTREME = Decimal("0.50")   # counter below 50% of the other side's price
LOWBALL = Decimal("0.70")
OVERPAY = Decimal("1.20")           # accept above 120% of the reference index


@dataclass(frozen=True)
class Turn:
    actor: str                         # "buyer" | "supplier"
    action: str                        # "offer" | "counter" | "accept" | "walk" | "reject"
    round: int
    max_rounds: int
    price: Decimal | None = None       # the price this turn puts forward (or accepts)
    own_prev: Decimal | None = None    # this actor's previous price in this negotiation
    other_last: Decimal | None = None  # the counterparty's latest price
    index_price: Decimal | None = None
    min_step_pct: Decimal = Decimal("0")


@dataclass(frozen=True)
class Verdict:
    level: Level
    codes: tuple[str, ...] = ()

    @property
    def reasons(self) -> list[str]:
        return [CODES[c][1] for c in self.codes if c in CODES]


def _level(codes: list[str]) -> Level:
    return max((CODES[c][0] for c in codes), key=_ORDER.__getitem__, default=Level.APPROPRIATE)


def judge(t: Turn) -> Verdict:
    codes: list[str] = []
    if t.round > t.max_rounds:
        codes.append("J_ROUND_EXCEEDED")
    buyer = t.actor == "buyer"
    if t.action in ("offer", "counter") and t.price is not None:
        if t.own_prev is not None:
            worse = t.price < t.own_prev if buyer else t.price > t.own_prev   # a buyer only ever raises, a supplier only ever lowers
            if worse:
                codes.append("J_REGRESSION")
            elif t.price == t.own_prev and t.round >= 2:
                codes.append("J_STALL")
            elif t.own_prev > 0 and abs(t.price - t.own_prev) < t.own_prev * t.min_step_pct / 100:
                codes.append("J_TOKEN_MOVE")
        if t.other_last is not None and t.other_last > 0:
            if buyer and t.price >= t.other_last:
                codes.append("J_CROSSED")
            elif not buyer and t.price <= t.other_last:
                codes.append("J_CROSSED")
            if buyer:
                ratio = t.price / t.other_last
                if ratio < LOWBALL_EXTREME:
                    codes.append("J_LOWBALL_EXTREME")
                elif ratio < LOWBALL:
                    codes.append("J_LOWBALL")
    elif t.action == "accept" and buyer and t.price is not None and t.index_price and t.price > t.index_price * OVERPAY:
        codes.append("J_OVERPAY")
    elif t.action == "walk" and buyer and t.round < t.max_rounds:   # a supplier may stop early for reasons only it knows (its floor)
        codes.append("J_EARLY_EXIT")
    codes = list(dict.fromkeys(codes))
    return Verdict(_level(codes), tuple(codes))


def combine(det: Verdict, llm_level: Level | None) -> Verdict:
    """A reviewer model can only tighten APPROPRIATE to CONCERN. It can never end a negotiation on its own, and it can never
    soften a deterministic verdict."""
    if llm_level in (Level.CONCERN, Level.INAPPROPRIATE) and det.level is Level.APPROPRIATE:
        return Verdict(Level.CONCERN, ("J_LLM_REVIEW",))
    if llm_level in (Level.CONCERN, Level.INAPPROPRIATE) and "J_LLM_REVIEW" not in det.codes:
        return Verdict(det.level, (*det.codes, "J_LLM_REVIEW"))
    return det


def turn_lines(t: Turn, band_pct: Decimal = Decimal("60")) -> dict[str, str]:
    """Plain-language facts about a turn for a reviewer model: perspective-neutral, relative figures only (no absolute prices, no mandates).
    A missing figure is SAID to be missing, never shown as 0 (a 0% move reads as stalling)."""
    def pct(a: Decimal | None, b: Decimal | None) -> Decimal | None:
        return ((a - b) / b * 100).quantize(Decimal("0.1")) if a is not None and b else None

    who = "buyer" if t.actor == "buyer" else "supplier"
    move = pct(t.price, t.own_prev)
    if t.action == "accept":
        move_line = f"The {who} accepts the other side's latest price"
    elif t.price is None:
        move_line = f"The {who} named no price on this turn"
    elif move is None:
        move_line = f"This is the {who}'s first price"
    elif move == 0:
        move_line = f"The {who} kept the same price as before"
    else:
        move_line = f"The {who} {'raised' if move > 0 else 'lowered'} its own price by {abs(move)} percent versus its previous price"
    buyer_p, sup_p = (t.price, t.other_last) if t.actor == "buyer" else (t.other_last, t.price)
    gap = pct(sup_p, buyer_p)
    if gap is None:
        gap_line = "The other side has no price yet"
    elif gap > 0:
        gap_line = f"After this turn the supplier's price is {gap} percent above the buyer's price"
    elif gap == 0:
        gap_line = "After this turn both sides name the same price"
    else:
        gap_line = f"After this turn the supplier's price is {abs(gap)} percent BELOW the buyer's price"
    vs = pct(t.price, t.index_price)
    # the reference only matters when a buyer ACCEPTS (the judge's OVERPAY check): shown on other turns it just invites "far from the index" flags
    index_line = ("The market reference is not relevant to this turn" if vs is None or t.action != "accept" else
                  f"Prices in this market normally sit within {band_pct} percent of the market reference; this price is {vs:+} percent versus it")
    return {"move_line": move_line, "gap_line": gap_line, "index_line": index_line}
