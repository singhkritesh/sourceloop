"""The ONE async engine / session factory (SQLite via aiosqlite in dev, Postgres-compatible).

Never call `create_async_engine` anywhere else. `configure()` (re)binds the single engine, which is how tests
point the app at a throwaway database. SQLite gets `PRAGMA foreign_keys=ON` on every connection (it is off by default).
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from sourceloop.data.models import AppSetting

DEFAULT_URL = "sqlite+aiosqlite:///./data/sourceloop.db"

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def _install_sqlite_pragmas(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_conn: Any, _rec: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")  # per connection; SQLite ignores FKs otherwise
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


def configure(url: str | None = None, *, echo: bool = False) -> AsyncEngine:
    """Create (or replace) the single engine. Call `await dispose()` first when replacing in a running loop."""
    global _engine, _sessionmaker
    url = url or database_url()
    kw: dict[str, Any] = {"echo": echo}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"timeout": 30}
        path = url.split(":///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    else:
        kw["pool_pre_ping"] = True
    _engine = create_async_engine(url, **kw)
    if url.startswith("sqlite"):
        _install_sqlite_pragmas(_engine)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def is_configured() -> bool:
    return _engine is not None


def get_engine() -> AsyncEngine:
    return _engine if _engine is not None else configure()


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        configure()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessionmaker = None, None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Handlers commit explicitly; anything uncommitted is rolled back."""
    async with get_sessionmaker()() as session:
        try:
            yield session
        finally:
            await session.rollback()


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Short transaction: commits on success, rolls back on error. Never hold across LLM work."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def create_all() -> None:
    """Dev/test convenience only; production uses `alembic upgrade head`."""
    from sourceloop.data.models import Base

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ---------------------------------------------------------------- app_setting (single row, id=1, upsert)
async def get_setting(session: AsyncSession) -> AppSetting | None:
    return (await session.execute(select(AppSetting).where(AppSetting.id == 1))).scalar_one_or_none()


async def upsert_setting(session: AsyncSession, now: datetime, **values: Any) -> AppSetting:
    """Never a plain INSERT: the table holds exactly one row with fixed id 1."""
    row = await get_setting(session)
    if row is None:
        row = AppSetting(id=1, updated_at=now, **values)
        session.add(row)
    else:
        for k, v in values.items():
            setattr(row, k, v)
        row.updated_at = now
    await session.flush()
    return row
