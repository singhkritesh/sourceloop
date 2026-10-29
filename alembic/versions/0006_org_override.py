"""org_override: versioned, immutable own-side settings per organization (+ the active-version pointer on organization)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-26 09:00:00
"""
from alembic import op
import sqlalchemy as sa
import sourceloop.data.models
from sourceloop.data.models import postgres_trigger_ddl, sqlite_trigger_ddl_v3

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('org_override',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('note', sa.String(length=300), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('created_at', sourceloop.data.models.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], ),
    sa.ForeignKeyConstraint(['org_id'], ['organization.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'version', name='uq_org_override_version')
    )
    op.create_index(op.f('ix_org_override_org_id'), 'org_override', ['org_id'], unique=False)
    op.add_column('organization', sa.Column('override_version', sa.Integer(), nullable=True))
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        for _t, _n, stmt in sqlite_trigger_ddl_v3():
            op.execute(stmt)
    elif bind.dialect.name == 'postgresql':
        for t, _n, stmt in postgres_trigger_ddl():
            if t == 'org_override' or not t:
                op.execute(stmt)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite':
        op.execute('DROP TRIGGER IF EXISTS org_override_no_update')
        op.execute('DROP TRIGGER IF EXISTS org_override_no_delete')
    elif bind.dialect.name == 'postgresql':
        op.execute('DROP TRIGGER IF EXISTS org_override_no_update ON org_override')
        op.execute('DROP TRIGGER IF EXISTS org_override_no_delete ON org_override')
    with op.batch_alter_table('organization') as b:
        b.drop_column('override_version')
    op.drop_index(op.f('ix_org_override_org_id'), table_name='org_override')
    op.drop_table('org_override')
