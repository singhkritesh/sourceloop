"""python -m sourceloop.connectors list | sync-ofac [--dry-run] | probe-weather <ISO2>"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sourceloop.connectors.base import ConnectorError
from sourceloop.connectors.catalog import CATALOG


def _list() -> int:
    print(f"{'id':<22}{'status':<11}{'capability':<18}provider")
    for c in CATALOG:
        print(f"{c.id:<22}{c.status.value:<11}{c.capability.value:<18}{c.provider}")
    return 0


async def _sync(dry: bool) -> int:
    from sourceloop.api.config import load_settings
    from sourceloop.connectors.ofac import LIST_NAME, OfacSdn
    from sourceloop.connectors.sync import sync_sanctions
    from sourceloop.data import db as dbm

    src = OfacSdn()
    if dry:
        ents = await src.fetch()
        print(f"OFAC SDN: {len(ents)} entity rows ({sum(1 for e in ents if e.country == 'XX')} without a country). Dry run: nothing written.")
        return 0
    dbm.configure(load_settings().database_url)
    try:
        async with dbm.session_scope() as s:
            r = await sync_sanctions(s, src, list_name=LIST_NAME)
    finally:
        await dbm.dispose()
    print(f"{r.list_name}: loaded {r.inserted} rows (replaced {r.removed}); {r.unknown_country} without a country")
    return 0


async def _weather(country: str) -> int:
    from sourceloop.connectors.open_meteo import OpenMeteoWeather

    sig = await OpenMeteoWeather().signal(country)
    print(sig.model_dump())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m sourceloop.connectors")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    so = sub.add_parser("sync-ofac")
    so.add_argument("--dry-run", action="store_true")
    pw = sub.add_parser("probe-weather")
    pw.add_argument("country")
    a = ap.parse_args()
    try:
        if a.cmd == "list":
            return _list()
        if a.cmd == "sync-ofac":
            return asyncio.run(_sync(a.dry_run))
        return asyncio.run(_weather(a.country))
    except ConnectorError as e:
        print(f"connector failed: {e.code}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
