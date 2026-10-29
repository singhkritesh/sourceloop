"""postgres immutability triggers (audit_event, mandate, published config_version, audit_anchor); no-op elsewhere

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-22 10:00:00
"""
from alembic import op

from sourceloop.data.models import postgres_trigger_ddl

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":  # SQLite already has its triggers from 0001/0002
        return
    for _table, _name, stmt in postgres_trigger_ddl():
        op.execute(stmt)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, name, _stmt in reversed(postgres_trigger_ddl()):
        if table:
            op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS sl_immutable_abort()")
