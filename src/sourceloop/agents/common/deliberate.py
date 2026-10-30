"""How an agent chooses its next move. The CODE lists the legal moves (prices included, all inside the agent's own mandate); a deliberator
picks one. The default picks the first, which is the rule-based move, so behaviour is unchanged until a model is plugged in. A model can
only choose among the listed moves: it can never name a price or an action that is not on the menu (CLAUDE.md rule 8)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class Move:
    label: str                 # short code shown to the chooser, e.g. COUNTER_STEADY
    kind: str                  # accept | counter | walk | award
    price: Decimal | None
    text: str                  # one plain sentence describing the move to the chooser


@dataclass
class Pick:
    index: int
    label: str
    kind: str
    source: str                # "model" | "rule"
    confidence: float = 1.0    # the model's own certainty over the menu (1 - normalised entropy); 1.0 for a rule
    mass: float = 1.0          # probability the model put on valid options at all
    p_chosen: float = 1.0
    agrees_with_rule: bool = True
    fallback: str | None = None  # why the rule move was used instead of the model's (LOW_CONFIDENCE, UNAVAILABLE, ...)
    tokens: int = 0
    ms: int = 0
    model: str = ""


class Deliberator(Protocol):
    async def pick(self, agent: str, sid: str | None, rnd: int, situation: Sequence[str], moves: Sequence[Move]) -> Pick: ...


class RuleDeliberator:
    """Always the first move: the code's own strategy."""

    async def pick(self, agent: str, sid: str | None, rnd: int, situation: Sequence[str], moves: Sequence[Move]) -> Pick:
        m = moves[0]
        return Pick(0, m.label, m.kind, "rule")
