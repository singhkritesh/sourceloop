"""`python -m sourceloop.data.migrate`: SQLite goes through the Alembic history; PostgreSQL's trigger text compares JSON as text (PostgreSQL has no equality for json)."""
import sqlite3

from alembic.config import Config
from alembic.script import ScriptDirectory

from sourceloop.data import migrate
from sourceloop.data.models import postgres_trigger_ddl


def test_a_new_sqlite_database_is_brought_to_the_latest_revision(tmp_path, monkeypatch):
    db = tmp_path / "fresh.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    migrate.main()
    con = sqlite3.connect(db)
    try:
        head = con.execute("select version_num from alembic_version").fetchone()[0]
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    finally:
        con.close()
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    assert head == ScriptDirectory.from_config(cfg).get_current_head() and {"negotiation_memory", "supplier_saved_profile", "audit_event"} <= tables


async def test_only_a_new_postgres_database_is_created_from_the_models():
    assert await migrate._fresh_postgres("sqlite+aiosqlite:///x.db") is False


def test_the_postgres_published_config_trigger_does_not_compare_json_directly():
    ddl = " ".join(s for _t, n, s in postgres_trigger_ddl() if n == "config_version_frozen_update")
    assert "NEW.body::text IS DISTINCT FROM OLD.body::text" in ddl and "NEW.body IS DISTINCT FROM OLD.body" not in ddl
