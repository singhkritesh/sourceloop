"""Seeded synthetic supplier universe with planted problems and a hidden answer key.

Planted: duplicate pairs (suffix/typo variants), look-alikes (same name, other country), watchlist
records (some with altered names), expired certificates, missing fields, inflated free-text claims,
forced-labor flags. Deterministic for a given seed (replay determinism).
"""
from __future__ import annotations

import random
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from sourceloop.compliance.resolution import normalize_name
from sourceloop.data.registry import AnswerKey, Cert, Need, Registry, SupplierRec, WatchEntry

TODAY = date(2026, 10, 20)
CATEGORIES = ("pcb_assembly", "connectors", "metal_castings", "precision_machining")
INDUSTRY = {"pcb_assembly": "high_tech", "connectors": "high_tech", "metal_castings": "industrial",
            "precision_machining": "industrial"}
COUNTRIES = ["CN", "VN", "MX", "DE", "PL", "TR", "IN", "US", "MY", "TH", "KR", "RU"]
CERTS = ["ISO9001", "IATF16949", "ISO14001", "RoHS", "UL"]
BASE_COST = {"pcb_assembly": 90, "connectors": 12, "metal_castings": 55, "precision_machining": 140}
_A = ["Nordic", "Apex", "Vertex", "Orion", "Sino", "Delta", "Prime", "Atlas", "Kestrel", "Helix", "Summit", "Zenith",
      "Aurora", "Pioneer", "Meridian", "Titan", "Cobalt", "Falcon", "Granite", "Lumen"]
_N = ["Circuit", "Precision", "Metalworks", "Electronics", "Components", "Castings", "Dynamics", "Industrial",
      "Fabrication", "Systems", "Interconnect", "Assembly", "Tooling", "Manufacturing", "Micro"]
_S = ["Inc", "Ltd", "GmbH", "LLC", "S.A. de C.V.", "Co", "Corp", "AG", "BV", "Sdn Bhd", "Oy"]


def _name(r: random.Random, used: set[str]) -> str:
    for _ in range(50):
        n = f"{r.choice(_A)} {r.choice(_N)} {r.choice(_A)}" if r.random() < 0.5 else f"{r.choice(_A)} {r.choice(_N)}"
        n = f"{n} {r.choice(_S)}"
        if normalize_name(n) not in used:
            used.add(normalize_name(n))
            return n
    n = f"{r.choice(_A)} {r.choice(_N)} {len(used)} {r.choice(_S)}"
    used.add(normalize_name(n))
    return n


def _variant(r: random.Random, name: str) -> str:
    parts = name.rsplit(" ", 1)
    base, suf = (parts[0], parts[1]) if len(parts) == 2 else (name, "")
    choice = r.randrange(4)
    if choice == 0:
        return f"{base} {r.choice([s for s in _S if s != suf])}"
    if choice == 1:
        return f"{base.upper()} {suf}"
    if choice == 2 and len(base) > 6:  # drop one interior letter (typo)
        i = r.randrange(2, len(base) - 2)
        return f"{base[:i]}{base[i + 1:]} {suf}"
    return f"{base.replace(' ', '-')} {suf}"


def generate(seed: int = 42, n: int = 1500, n_dups: int = 100, n_lookalikes: int = 40, n_watch: int = 33
             ) -> tuple[Registry, Registry, AnswerKey]:
    """Returns (visible registry, complete registry incl. hidden true values, answer key)."""
    r = random.Random(seed)
    used: set[str] = set()
    full: list[SupplierRec] = []
    key = AnswerKey()

    for i in range(n):
        cats = tuple(r.sample(CATEGORIES, k=1 if r.random() < 0.8 else 2))
        country = r.choices(COUNTRIES, weights=[14, 9, 8, 9, 7, 6, 8, 10, 4, 4, 5, 1])[0]
        certs: list[Cert] = []
        for ct in CERTS:
            if r.random() < 0.6:
                expired = r.random() < 0.36
                days = -r.randint(10, 700) if expired else r.randint(30, 1100)
                certs.append(Cert(ct, "SyntheticCB", TODAY + timedelta(days=days)))
        have = {c.type for c in certs}
        claimed = tuple(ct for ct in CERTS if ct not in have and r.random() < 0.08)
        full.append(SupplierRec(
            id=f"S{i:05d}", name=_name(r, used), country=country,
            tax_id=f"{country}{r.randint(10**7, 10**8 - 1)}", categories=cats, certs=tuple(certs),
            claimed_certs=claimed, capacity_units=int(r.lognormvariate(9.2, 0.9)), lead_time_weeks=r.randint(3, 20),
            financial_risk=round(r.betavariate(2, 5), 3), years_in_business=r.randint(1, 45),
            forced_labor_flag=r.random() < 0.015, subtier_origin=("XJ",) if r.random() < 0.01 else (),
            network_status=r.choices(["member", "invited", "external"], weights=[55, 15, 30])[0],
            on_time_pct=round(r.betavariate(8, 2), 3), quality_score=round(r.betavariate(7, 2), 3),
            unit_cost=Decimal(str(round(BASE_COST[cats[0]] * r.uniform(0.8, 1.25), 2))),
        ))

    # duplicates: same company, different spelling; half also lose their tax id
    base_ids = r.sample(range(n), n_dups)
    nxt = n
    for bi in base_ids:
        b = full[bi]
        d = replace(b, id=f"S{nxt:05d}", name=_variant(r, b.name), tax_id=b.tax_id if r.random() < 0.5 else None)
        full.append(d)
        key.duplicate_pairs.add(frozenset({b.id, d.id}))
        key.canonical_of[d.id] = b.id
        nxt += 1

    # look-alikes: same name, different company in another country
    for bi in r.sample([i for i in range(n) if i not in set(base_ids)], n_lookalikes):
        b = full[bi]
        other = r.choice([c for c in COUNTRIES if c != b.country])
        la = replace(b, id=f"S{nxt:05d}", country=other, tax_id=f"{other}{r.randint(10**7, 10**8 - 1)}",
                     certs=(), claimed_certs=())
        full.append(la)
        key.lookalike_pairs.add(frozenset({b.id, la.id}))
        nxt += 1

    # watchlist: 20 altered names of real suppliers + the rest unrelated entities
    wl: list[WatchEntry] = []
    targets = r.sample([s for s in full if s.id not in key.canonical_of], min(n_watch * 3 // 5, n_watch))
    for i, s in enumerate(targets):
        altered = _variant(r, s.name) if r.random() < 0.6 else s.name
        wl.append(WatchEntry(f"W{i:03d}", altered, s.country, "SYNTH-SDN", aliases=()))
        key.sanctioned_ids.add(s.id)
        for d, c in key.canonical_of.items():  # duplicates of a sanctioned company are sanctioned too
            if c == s.id:
                key.sanctioned_ids.add(d)
    for i in range(len(targets), n_watch):
        wl.append(WatchEntry(f"W{i:03d}", f"{r.choice(_A)} {r.choice(_N)} Holdings {r.randint(100, 999)}",
                             r.choice(COUNTRIES), "SYNTH-SDN"))

    # visible registry: blank some fields (missing data)
    vis: list[SupplierRec] = []
    for s in full:
        upd: dict = {"unit_cost": None}  # supplier cost is mandate-private: never in the buyer-visible copy
        for f in ("capacity_units", "lead_time_weeks", "financial_risk", "years_in_business"):
            if r.random() < 0.06:
                upd[f] = None
        if r.random() < 0.15:
            upd["on_time_pct"] = None
        vis.append(replace(s, **upd))

    idx = {c: Decimal(str(BASE_COST[c])) * Decimal("1.05") for c in CATEGORIES}
    return Registry(vis, wl, idx), Registry(full, wl, idx), key


def make_needs(seed: int = 42) -> list[Need]:
    r = random.Random(seed + 1)
    needs: list[Need] = []
    for i in range(12):
        cat = CATEGORIES[i % 4]
        needs.append(Need(
            id=f"N{i:02d}", category=cat, industry=INDUSTRY[cat], volume_units=r.choice([500, 2000, 5000, 20000]),
            required_certs=tuple(r.sample(CERTS[:4], k=r.choice([1, 2]))),
            excluded_countries=tuple(r.sample(["RU", "CN", "TR"], k=r.choice([0, 1]))),
            max_lead_time_weeks=r.choice([8, 10, 12, 16]), max_financial_risk=r.choice([0.45, 0.6]),
            min_years_in_business=r.choice([2, 5]),
        ))
    return needs
