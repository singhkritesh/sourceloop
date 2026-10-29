"""Deterministic model of the manual / human-bottleneck process for the SAME needs and registry.

Apples to apples: the baseline uses the same data, the same qualification rules (`compliance.rules.qualify`),
the same scoring and shortlist function as the agent. What differs is WHO executes each step:

* every step is a sequential human action with an assumed latency distribution (seeded RNG, injected Clock);
* the human has no entity resolution and no fuzzy watchlist screen: duplicates are spotted by tax id or by eye,
  watchlist entries by exact name or by eye, expired certificates by reading dates;
* a skimming human errs with the probabilities in `Assumptions` (all labeled assumptions).

Outputs: elapsed working days, number of human touches, error events committed, and which bad suppliers
escaped into the shortlist (scored against the answer key exactly like the agent's shortlist).
"""
from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sourceloop.clock import Clock
from sourceloop.compliance.resolution import normalize_name
from sourceloop.compliance.rules import WatchlistIndex, qualify
from sourceloop.compliance.scoring import Scored, score, shortlist
from sourceloop.data.enums import QualStatusEnum as Q
from sourceloop.data.synthetic import TODAY
from sourceloop.eval.assumptions import Assumptions
from sourceloop.eval.metrics import assess_shortlist
from sourceloop.eval.world import EMPTY_WL, World


class SimClock(Clock):
    """Working-time clock. now() is stable until advance() is called, so timelines are reproducible."""

    def __init__(self, start: datetime | None = None, hours_per_day: float = 8.0) -> None:
        self._t = start or datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
        self._t0 = self._t
        self._h = hours_per_day

    def now(self) -> datetime:
        return self._t

    def advance(self, days: float) -> None:
        self._t += timedelta(hours=days * self._h)

    def elapsed_days(self) -> float:
        return round((self._t - self._t0) / timedelta(hours=self._h), 3)


class Timeline:
    def __init__(self, A: Assumptions, rng: random.Random, clock: SimClock) -> None:
        self.A, self.rng, self.clock = A, rng, clock
        self.touches = 0
        self.steps: list[dict[str, Any]] = []

    def step(self, name: str, *, touches: int = 1, days: float | None = None) -> float:
        d = self.A.sample(self.rng, name) if days is None else days
        self.clock.advance(d)
        self.touches += touches
        self.steps.append({"step": name, "days": d, "touches": touches, "t": self.clock.elapsed_days()})
        return d


def _missing_only(reasons: tuple[str, ...]) -> bool:
    return bool(reasons) and all(r.startswith("MISSING_DATA") for r in reasons)


def run_baseline(w: World, idx: int, A: Assumptions, clock: SimClock | None = None) -> dict[str, Any]:
    need = w.needs[idx]
    t = w.truth(idx)
    rng = random.Random(f"baseline:{w.seed}:{idx}")  # noqa: S311  (seeded simulation, not security)
    tl = Timeline(A, rng, clock or SimClock(hours_per_day=A.work_hours_per_day))
    errors: Counter[str] = Counter()

    tl.step("requirement_drafting")
    tl.step("supplier_search")
    pool = [s for s in w.vis.in_category(need.category) if s.country not in need.excluded_countries]
    ranked = sorted((score(s, need) for s in pool), key=lambda x: (-x.score, x.supplier_id))

    wl_exact = {normalize_name(n) for e in w.vis.watchlist for n in (e.name, *e.aliases)}
    wl_fuzzy = WatchlistIndex(w.vis.watchlist)
    accepted: list[Scored] = []
    seen_tax: dict[tuple[str, str], str] = {}
    accepted_canon: set[str] = set()
    checked = 0
    for sc in ranked[: A.review_depth]:
        if len(accepted) >= A.longlist_size:
            break
        s = w.vis.get(sc.supplier_id)
        assert s is not None
        checked += 1
        tl.step("verify_candidate")
        canon = t.canonical(s.id)
        # duplicate screen: exact tax id (skim error) or eyeballing near-identical names
        is_dup = canon in accepted_canon
        if is_dup:
            same_tax = bool(s.tax_id) and (s.country, s.tax_id) in seen_tax
            caught = rng.random() < ((1 - A.p_skim_miss_exact) if same_tax else A.p_fuzzy_dup_catch)
            if caught:
                continue
            errors["missed_duplicate"] += 1
        # watchlist screen: exact name only, or approximate by eye
        if s.id in w.key.sanctioned_ids:
            exact = normalize_name(s.name) in wl_exact
            fuzzy = wl_fuzzy.hit(s.name, s.aliases) is not None
            p = (1 - A.p_skim_miss_exact) if exact else (A.p_fuzzy_catch if fuzzy else 0.0)
            if rng.random() < p:
                continue
            errors["missed_watchlist_exact" if exact else "missed_watchlist_lookalike"] += 1
        # the remaining rules are the same pure functions the agent uses (humans just run them by hand)
        d = qualify(s, need, EMPTY_WL, TODAY)
        if d.status is Q.REVIEW and _missing_only(d.reasons):
            tl.step("chase_missing_data")  # phone the supplier for the blank fields
            full = w.full.get(s.id)
            assert full is not None
            d = qualify(full, need, EMPTY_WL, TODAY)
        if d.status is Q.REJECTED and all(r.startswith("CERT_EXPIRED") for r in d.reasons) and rng.random() < A.p_miss_expiry:
            errors["missed_expired_cert"] += 1
        elif d.status is not Q.QUALIFIED:
            continue
        accepted.append(sc)
        accepted_canon.add(canon)
        if s.tax_id:
            seen_tax[(s.country, s.tax_id)] = s.id

    tl.step("compliance_review")
    picks = shortlist(accepted, k=5, min_countries=2)
    sl_ids = [p.supplier_id for p in picks]
    awarded: str | None = None
    outcome = "NO_VIABLE_SUPPLIER"
    if sl_ids:
        tl.step("rfq_preparation")
        for _ in sl_ids:
            tl.step("rfq_send")
        replies = [A.sample(rng, "supplier_reply") for _ in sl_ids]
        tl.step("supplier_reply", touches=0, days=min(max(replies), A.rfq_reply_cap_days))  # buyer waits for the slowest
        tl.step("quote_comparison")
        for _ in range(A.manual_negotiation_rounds):
            tl.step("negotiation_round")
        tl.step("award_approval")
        awarded = sl_ids[0]  # manual process awards the best-scored supplier it has vetted; price not modeled
        tl.step("contract_drafting")
        tl.step("legal_review")
        tl.step("signature")
        tl.step("bank_details")
        outcome = "DEAL"

    return {
        "seed": w.seed, "need": need.id, "need_index": idx, "category": need.category, "outcome": outcome,
        "awarded": awarded, "shortlist": sl_ids, "candidates_checked": checked,
        "elapsed_days": clock_days(tl), "human_touches": tl.touches,
        "errors": dict(sorted(errors.items())), "error_events": sum(errors.values()),
        "shortlist_quality": assess_shortlist(w, idx, sl_ids, awarded),
        "steps": tl.steps,
    }


def clock_days(tl: Timeline) -> float:
    return tl.clock.elapsed_days()
