"""LEVERAGE message type and negotiation memory

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-06 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None

OLD = ('BRIEF', 'INVITE', 'INTEREST_REPLY', 'DOC_REQUEST', 'DOC_SUBMISSION', 'RFI', 'RFI_REPLY', 'FOLLOW_UP', 'QUAL_RESULT', 'RFQ', 'OFFER', 'COUNTER',
       'ACCEPT', 'REJECT', 'CONTRACT_DRAFT', 'REDLINE', 'SIGN_REQUEST', 'ONBOARD_DATA', 'ESCALATE')
NEW = OLD[:8] + ('LEVERAGE',) + OLD[8:]


def _enum(values):
    return sa.Enum(*values, name='ck_messagetypeenum', native_enum=False, create_constraint=True, length=32)


def _swap(values_from, values_to):
    if op.get_bind().dialect.name == 'sqlite':
        with op.batch_alter_table('message', recreate='always') as b:
            b.alter_column('type', existing_type=_enum(values_from), type_=_enum(values_to), existing_nullable=False)
    else:
        op.execute(sa.text('ALTER TABLE message DROP CONSTRAINT IF EXISTS ck_messagetypeenum'))
        quoted = ", ".join("'" + v + "'" for v in values_to)
        op.execute(sa.text(f'ALTER TABLE message ADD CONSTRAINT ck_messagetypeenum CHECK (type IN ({quoted}))'))


def upgrade() -> None:
    _swap(OLD, NEW)
    op.create_table(
        'negotiation_memory',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('owner_org_id', sa.Uuid(), sa.ForeignKey('organization.id'), nullable=False),
        sa.Column('owner_side', sa.String(8), nullable=False),
        sa.Column('project_id', sa.Uuid(), sa.ForeignKey('project.id'), nullable=False),
        sa.Column('counterparty', sa.String(64), nullable=False),
        sa.Column('category', sa.String(80), nullable=False),
        sa.Column('rounds', sa.Integer(), nullable=False),
        sa.Column('opening_price', sa.Numeric(14, 4), nullable=False),
        sa.Column('final_price', sa.Numeric(14, 4), nullable=False),
        sa.Column('index_price', sa.Numeric(14, 4), nullable=True),
        sa.Column('outcome', sa.String(10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('owner_org_id', 'project_id', 'counterparty', name='uq_negotiation_memory'),
    )
    op.create_index('ix_negotiation_memory_owner_org_id', 'negotiation_memory', ['owner_org_id'])
    op.create_index('ix_negotiation_memory_project_id', 'negotiation_memory', ['project_id'])


def downgrade() -> None:
    op.drop_table('negotiation_memory')
    op.execute(sa.text("DELETE FROM message WHERE type = 'LEVERAGE'"))
    _swap(NEW, OLD)
