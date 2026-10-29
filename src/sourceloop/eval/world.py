"""Synthetic world for one seed plus the hidden answer key (evaluation-only: agents never see this)."""
from __future__ import annotations

from dataclasses import dataclass, field

from sourceloop.compliance.rules import WatchlistIndex, qualify
from sourceloop.compliance.scoring import Scored, score, shortlist
from sourceloop.data.registry import AnswerKey, Need, Registry
from sourceloop.data.synthetic import TODAY, generate, make_needs

HARD = frozenset({"SANCTIONED", "FORCED_LABOR"})  # blocked classes: must never reach a shortlist
SOFT = frozenset({"CERT_EXPIRED", "CERT_MISSING", "DISQUALIFIED_OTHER"})
EMPTY_WL = WatchlistIndex([])


@dataclass
class NeedTruth:
    classes: dict[str, frozenset[str]]       # supplier id -> bad classes under the TRUE (unblanked) record
    good: frozenset[str]                     # truly qualified, canonical (non-duplicate) suppliers
    oracle: tuple[str, ...]                  # shortlist an all-knowing process would pick (k=5)
    canon: dict[str, str]

    def is_bad(self, sid: str) -> bool:
        return bool(self.classes.get(sid, frozenset()) & (HARD | SOFT))

    def is_hard(self, sid: str) -> bool:
        return bool(self.classes.get(sid, frozenset()) & HARD)

    def canonical(self, sid: str) -> str:
        return self.canon.get(sid, sid)


@dataclass
class World:
    seed: int
    vis: Registry
    full: Registry
    key: AnswerKey
    needs: list[Need]
    _truth: dict[int, NeedTruth] = field(default_factory=dict)

    def truth(self, idx: int) -> NeedTruth:
        if idx not in self._truth:
            self._truth[idx] = _build_truth(self, self.needs[idx])
        return self._truth[idx]


def build_world(seed: int) -> World:
    vis, full, key = generate(seed)
    return World(seed, vis, full, key, make_needs(seed))


def _build_truth(w: World, need: Need) -> NeedTruth:
    classes: dict[str, frozenset[str]] = {}
    good: list[Scored] = []
    for s in w.full.in_category(need.category):
        d = qualify(s, need, EMPTY_WL, TODAY)  # the watchlist truth comes from the answer key, not from fuzzy matching
        c: set[str] = set()
        if s.id in w.key.sanctioned_ids:
            c.add("SANCTIONED")
        for r in d.reasons:
            if r == "FORCED_LABOR_RISK":
                c.add("FORCED_LABOR")
            elif r.startswith("CERT_EXPIRED"):
                c.add("CERT_EXPIRED")
            elif r.startswith(("CERT_MISSING", "UNVERIFIED_CLAIM")):
                c.add("CERT_MISSING")
            else:
                c.add("DISQUALIFIED_OTHER")
        if s.id in w.key.canonical_of:
            c.add("DUPLICATE")
        classes[s.id] = frozenset(c)
        if not c:
            good.append(score(s, need))
    gset = frozenset(g.supplier_id for g in good)
    oracle = tuple(x.supplier_id for x in shortlist(good, k=5, min_countries=2))
    return NeedTruth(classes, gset, oracle, dict(w.key.canonical_of))
