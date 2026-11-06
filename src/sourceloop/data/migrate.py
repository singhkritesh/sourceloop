"""Bring the database to the current schema: `python -m sourceloop.data.migrate`.

SQLite (and any database that already has an Alembic history): `alembic upgrade head`.
A brand-new PostgreSQL database: the first revisions were written against SQLite and repeat a CHECK constraint name that PostgreSQL refuses, so the
schema is created from the models (tables, constraints and the append-only triggers, written for PostgreSQL in `models.py`) and stamped at `head`;
every later revision then runs normally on top. Applied revisions are never edited."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from sourceloop.data import db as dbm

ROOT = Path.cwd() if (Path.cwd() / "alembic.ini").exists() else Path(__file__).resolve().parents[3]   # the container runs from /app; a checkout from its root


async def _fresh_postgres(url: str) -> bool:
    if not url.startswith("postgresql"):
        return False
    dbm.configure(url)
    try:
        async with dbm.get_engine().connect() as conn:
            has = await conn.run_sync(lambda s: inspect(s).has_table("alembic_version"))
        if has:
            return False
        await dbm.create_all()
        return True
    finally:
        await dbm.dispose()


def main() -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    url = dbm.database_url()
    if asyncio.run(_fresh_postgres(url)):
        command.stamp(cfg, "head")
        print("new PostgreSQL database: schema created from the models and stamped at head", file=sys.stderr)
    else:
        command.upgrade(cfg, "head")


if __name__ == "__main__":
    main()
