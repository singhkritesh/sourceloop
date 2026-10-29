"""Modeled (NOT measured) numbers for the manual baseline and for human wait time at agent checkpoints.

Design matters, numbers don't: every figure here is an illustrative assumption, printed next to the results
and exported in the JSON. Change them via `Assumptions(...)` to see the comparison move; the structure
(what steps exist, how many humans touch them, which errors a skimming human makes) is the finding.
All times are in WORKING days (8 working hours). Distributions are lognormal given (median, sigma).
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any

LABEL = "ILLUSTRATIVE ASSUMPTION - modeled, not measured"


@dataclass(frozen=True, slots=True)
class Dist:
    median: float  # working days
    sigma: float = 0.5

    def sample(self, rng: random.Random) -> float:
        return round(rng.lognormvariate(math.log(self.median), self.sigma), 3)


def _steps() -> dict[str, Dist]:
    return {
        "requirement_drafting": Dist(1.0, 0.4),
        "supplier_search": Dist(2.0, 0.4),
        "verify_candidate": Dist(0.5, 0.5),       # registry + certificate + watchlist + duplicate look-up, per candidate
        "chase_missing_data": Dist(1.5, 0.6),     # phone/email a supplier for blank registry fields
        "compliance_review": Dist(1.0, 0.5),
        "rfq_preparation": Dist(1.0, 0.4),
        "rfq_send": Dist(0.1, 0.3),               # per supplier
        "supplier_reply": Dist(4.0, 0.6),         # per supplier, human supplier side; buyer waits for the slowest
        "quote_comparison": Dist(1.0, 0.4),
        "negotiation_round": Dist(2.5, 0.5),      # buyer prep + supplier turnaround, per round
        "award_approval": Dist(2.0, 0.6),
        "contract_drafting": Dist(2.0, 0.4),
        "legal_review": Dist(3.0, 0.5),
        "signature": Dist(2.0, 0.6),
        "bank_details": Dist(1.0, 0.5),
        # agent side: wait for ONE human decision at a checkpoint (queue latency, not working time)
        "approval_wait": Dist(0.5, 0.7),
    }


@dataclass(frozen=True)
class Assumptions:
    steps: dict[str, Dist] = field(default_factory=_steps)
    work_hours_per_day: float = 8.0
    review_depth: int = 25             # most candidates a human will verify before giving up
    longlist_size: int = 10            # same as RunConfig.longlist_size
    manual_negotiation_rounds: int = 2
    rfq_reply_cap_days: float = 10.0   # buyer stops waiting after this
    # error model of a human skimming a registry (probabilities per check)
    p_skim_miss_exact: float = 0.05    # misses a watchlist/duplicate entry that matches exactly
    p_fuzzy_catch: float = 0.15        # notices a watchlist entry that matches only approximately (altered spelling)
    p_fuzzy_dup_catch: float = 0.35    # notices a duplicate with no shared tax id
    p_miss_expiry: float = 0.04        # overlooks an expired certificate date
    # agent side machine time
    machine_s_per_message: float = 2.0
    machine_s_per_llm_call: float = 4.0

    def sample(self, rng: random.Random, step: str) -> float:
        return self.steps[step].sample(rng)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["steps"] = {k: {"median_days": v.median, "sigma": v.sigma} for k, v in self.steps.items()}
        d["label"] = LABEL
        return d
