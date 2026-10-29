"""Entity resolution: merge records that describe the same company, never merge on conflicting facts.

Optimization: records are BLOCKED by (country, first 3 normalized chars) so comparison cost is the sum
of squared block sizes rather than n^2. Exact tax-id matches merge without fuzzy scoring.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from rapidfuzz import fuzz

from sourceloop.data.registry import SupplierRec

_LEGAL = re.compile(
    r"\b(incorporated|inc|corporation|corp|company|co|limited|ltd|llc|gmbh|ag|sa de cv|s a de c v|s\.?a\.?|bv|nv|plc|pte|pvt|"
    r"sdn bhd|oy|ab|srl|spa|kg|ou)\b\.?", re.I)


def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = _LEGAL.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


class _UF:
    def __init__(self) -> None:
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _conflict(a: SupplierRec, b: SupplierRec) -> bool:
    if a.country != b.country:
        return True
    return bool(a.tax_id and b.tax_id and a.tax_id != b.tax_id)


def resolve(records: list[SupplierRec], threshold: float = 88.0) -> dict[str, str]:
    """Returns {record_id: canonical_id}. Canonical = lowest id in the cluster."""
    uf = _UF()
    norm = {r.id: normalize_name(r.name) for r in records}
    by_tax: dict[tuple[str, str], list[SupplierRec]] = defaultdict(list)
    blocks: dict[tuple[str, str, str], list[SupplierRec]] = defaultdict(list)
    for r in records:
        uf.find(r.id)
        if r.tax_id:
            by_tax[(r.country, r.tax_id)].append(r)
        n = norm[r.id]
        # multi-pass blocking: a typo can break the prefix OR the suffix, rarely both
        blocks[(r.country, "pre", n[:3])].append(r)
        blocks[(r.country, "suf", n[-3:])].append(r)
    for grp in by_tax.values():
        for o in grp[1:]:
            uf.union(grp[0].id, o.id)
    seen: set[tuple[str, str]] = set()
    for blk in blocks.values():
        for i in range(len(blk)):
            for j in range(i + 1, len(blk)):
                a, b = blk[i], blk[j]
                pair = (a.id, b.id) if a.id < b.id else (b.id, a.id)
                if pair in seen or _conflict(a, b):
                    continue
                seen.add(pair)
                if fuzz.token_sort_ratio(norm[a.id], norm[b.id]) >= threshold:
                    uf.union(a.id, b.id)
    return {r.id: uf.find(r.id) for r in records}
