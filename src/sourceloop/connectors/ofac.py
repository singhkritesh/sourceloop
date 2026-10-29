"""US Treasury OFAC SDN list -> SanctionedEntity. Public, no key. The three CSVs (SDN, ALT aliases, ADD addresses) are fetched
through SafeHttp (allowlisted redirects, 16 MB cap). Parsing is pure and tolerant; every row is validated by a strict model and a
bad row is skipped, never guessed. Only ENTITIES are loaded (suppliers are companies); individuals, vessels and aircraft are not."""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict

from pydantic import ValidationError

from sourceloop.connectors.base import ConnectorError, SanctionedEntity
from sourceloop.connectors.catalog import OFAC_HOSTS
from sourceloop.connectors.countries import iso2
from sourceloop.net.safe_http import EgressBlocked, SafeHttp

LIST_NAME = "OFAC_SDN"
BASE = "https://www.treasury.gov/ofac/downloads/"
MAX_FILE_BYTES = 12 * 1024 * 1024
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _null(v: str) -> str:
    v = v.strip()
    return "" if v in ("-0-", "") else v


def _clean(v: str) -> str:
    return re.sub(r"\s+", " ", _CTRL.sub(" ", v)).strip()


def _rows(text: str, min_cols: int):
    for r in csv.reader(io.StringIO(text)):
        if len(r) >= min_cols:
            yield r


def parse(sdn: str, alt: str, add: str) -> list[SanctionedEntity]:
    """One entity per (OFAC entry, country). An entry with no usable address gets country 'XX'."""
    names: dict[str, str] = {}
    for r in _rows(sdn, 4):
        if _null(r[2]) == "":  # SDN_Type -0- => entity (company / organisation)
            n = _clean(r[1])
            if n:
                names[r[0].strip()] = n
    aliases: dict[str, list[str]] = defaultdict(list)
    for r in _rows(alt, 4):
        ent = r[0].strip()
        a = _clean(r[3])
        if ent in names and a and a != names[ent] and a not in aliases[ent]:
            aliases[ent].append(a)
    countries: dict[str, list[str]] = defaultdict(list)
    for r in _rows(add, 5):
        ent = r[0].strip()
        c = iso2(_null(r[4]))
        if ent in names and c != "XX" and c not in countries[ent]:
            countries[ent].append(c)
    out: list[SanctionedEntity] = []
    for ent, name in names.items():
        for c in (countries.get(ent) or ["XX"]):
            try:
                out.append(SanctionedEntity(source_id=ent, name=name[:200], aliases=tuple(a[:200] for a in aliases.get(ent, [])[:60]), country=c, list_name=LIST_NAME))
            except ValidationError:
                continue  # malformed row: skipped, never repaired
    return out


class OfacSdn:
    def __init__(self, http: SafeHttp | None = None, base: str = BASE):
        self.http = http or SafeHttp(set(OFAC_HOSTS), timeout_s=60.0)
        self.base = base

    async def _text(self, name: str) -> str:
        try:
            r = await self.http.get_following(self.base + name, max_redirects=3, max_bytes=MAX_FILE_BYTES)
        except EgressBlocked as e:
            raise ConnectorError(f"EGRESS_{e.code}") from None
        except Exception:  # noqa: BLE001 - network failure: code only, never upstream text
            raise ConnectorError("UNREACHABLE") from None
        if r.status_code != 200:
            raise ConnectorError(f"HTTP_{r.status_code}")
        return r.content.decode("latin-1")  # OFAC publishes latin-1 CSV

    async def fetch(self) -> list[SanctionedEntity]:
        sdn, alt, add = await self._text("sdn.csv"), await self._text("alt.csv"), await self._text("add.csv")
        ents = parse(sdn, alt, add)
        if len(ents) < 100:  # a truncated or error page must not wipe the watchlist
            raise ConnectorError("LIST_TOO_SMALL")
        return ents
