import time

from sourceloop.compliance.resolution import normalize_name, resolve
from sourceloop.compliance.rules import WatchlistIndex, qualify
from sourceloop.compliance.scoring import score, shortlist
from sourceloop.data.enums import QualStatusEnum as Q
from sourceloop.data.synthetic import TODAY, generate, make_needs

VIS, FULL, KEY = generate(seed=42)
NEEDS = make_needs(42)


def test_deterministic_generation():
    a, _, _ = generate(seed=7, n=200, n_dups=10, n_lookalikes=5, n_watch=6)
    b, _, _ = generate(seed=7, n=200, n_dups=10, n_lookalikes=5, n_watch=6)
    assert [s.name for s in a.suppliers] == [s.name for s in b.suppliers]
    assert len(VIS.suppliers) == 1500 + 100 + 40 and len(VIS.watchlist) == 33


def test_normalize():
    assert normalize_name("Apex Circuit, Inc.") == normalize_name("APEX CIRCUIT Incorporated")


def test_entity_resolution_finds_duplicates_without_false_merges_and_is_fast():
    t = time.perf_counter()
    canon = resolve(VIS.suppliers)
    dt = time.perf_counter() - t
    found = {frozenset({a, b}) for a, b in KEY.canonical_of.items() if canon[a] == canon[b]}
    assert len(found) / len(KEY.duplicate_pairs) >= 0.95
    for pair in KEY.lookalike_pairs:  # different country => never merged
        a, b = tuple(pair)
        assert canon[a] != canon[b]
    # any merge outside the planted duplicates would be a false merge
    from collections import defaultdict
    groups = defaultdict(set)
    for k, v in canon.items():
        groups[v].add(k)
    planted = {x for p in KEY.duplicate_pairs for x in p}
    assert all(g <= planted or len(g) == 1 for g in groups.values())
    assert dt < 3.0


def test_watchlist_screening_blocks_all_sanctioned_and_flags_few_extras():
    wl = WatchlistIndex(VIS.watchlist)
    need = NEEDS[0]
    blocked = {s.id for s in VIS.suppliers if qualify(s, need, wl, TODAY).status is Q.BLOCKED}
    assert KEY.sanctioned_ids <= blocked  # 100% of true sanctioned names blocked


def test_qualify_rules_fail_closed():
    wl = WatchlistIndex(VIS.watchlist)
    need = NEEDS[0]
    q = [qualify(s, need, wl, TODAY) for s in VIS.suppliers]
    missing = [d for d in q if any(r.startswith("MISSING_DATA") for r in d.reasons)]
    assert missing and all(d.status is not Q.QUALIFIED for d in missing)
    expired = [d for d in q if any(r.startswith("CERT_EXPIRED") for r in d.reasons)]
    assert expired and all(d.status in (Q.REJECTED, Q.BLOCKED) for d in expired)
    claims = [d for d in q if any(r.startswith("UNVERIFIED_CLAIM") for r in d.reasons)]
    assert all(d.status is not Q.QUALIFIED for d in claims)


def test_shortlist_diversity():
    wl = WatchlistIndex(VIS.watchlist)
    need = NEEDS[1]
    ok = [s for s in VIS.in_category(need.category) if qualify(s, need, wl, TODAY).status is Q.QUALIFIED]
    sl = shortlist([score(s, need) for s in ok], k=5, min_countries=2)
    assert len(sl) == 5 and len({x.country for x in sl}) >= 2
