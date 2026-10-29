"""studio draft + audit anchor (tail-truncation detection)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21 12:00:00
"""
from alembic import op
import sqlalchemy as sa
import sourceloop.data.models
from sourceloop.data.models import sqlite_trigger_ddl_v2

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # plain ADD COLUMN (no table rebuild) so the config_version immutability triggers survive on SQLite
    op.add_column('config_version', sa.Column('rollback_of', sa.Integer(), nullable=True))
    op.create_table('studio_draft',
    sa.Column('owner_id', sa.String(length=80), nullable=False),
    sa.Column('body', sa.JSON(), nullable=False),
    sa.Column('base_version', sa.Integer(), nullable=True),
    sa.Column('updated_at', sourceloop.data.models.UTCDateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('owner_id')
    )
    op.create_table('audit_anchor',
    sa.Column('project_id', sa.String(length=64), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.Column('updated_at', sourceloop.data.models.UTCDateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('project_id')
    )
    # backfill: anchor every existing stream at its current head
    op.execute(
        "INSERT INTO audit_anchor (project_id, seq, hash, updated_at) "
        "SELECT e.project_id, e.seq, e.hash, e.ts FROM audit_event e "
        "WHERE e.seq = (SELECT MAX(x.seq) FROM audit_event x WHERE x.project_id = e.project_id)")
    if op.get_bind().dialect.name == 'sqlite':
        for _t, _n, stmt in sqlite_trigger_ddl_v2():
            op.execute(stmt)


def downgrade() -> None:
    if op.get_bind().dialect.name == 'sqlite':
        op.execute("DROP TRIGGER IF EXISTS audit_anchor_no_regress")
        op.execute("DROP TRIGGER IF EXISTS audit_anchor_no_delete")
    op.drop_table('audit_anchor')
    op.drop_column('config_version', 'rollback_of')
    op.drop_table('studio_draft')
