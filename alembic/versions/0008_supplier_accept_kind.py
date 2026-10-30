"""SUPPLIER_ACCEPT approval kind: the awarded supplier's user confirms the final terms

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-30 09:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

OLD = ('REQUIREMENT', 'COMPLIANCE_FLAG', 'TECHNICAL_QUAL', 'AWARD', 'CLAUSE', 'SIGNATURE', 'BANK_DETAILS', 'HOLD_RELEASE',
       'MANDATE_BREACH', 'LOW_CONFIDENCE', 'LLM_UNAVAILABLE', 'STALLED', 'BUDGET', 'RULE')
NEW = OLD[:6] + ('SUPPLIER_ACCEPT',) + OLD[6:]


def _enum(values):
    return sa.Enum(*values, name='ck_approvalkindenum', native_enum=False, create_constraint=True, length=32)


def _swap(values_from, values_to):
    if op.get_bind().dialect.name == 'sqlite':
        with op.batch_alter_table('approval', recreate='always') as b:
            b.alter_column('kind', existing_type=_enum(values_from), type_=_enum(values_to), existing_nullable=False)
    else:
        op.execute(sa.text('ALTER TABLE approval DROP CONSTRAINT IF EXISTS ck_approvalkindenum'))
        quoted = ", ".join("'" + v + "'" for v in values_to)
        op.execute(sa.text(f'ALTER TABLE approval ADD CONSTRAINT ck_approvalkindenum CHECK (kind IN ({quoted}))'))


def upgrade() -> None:
    _swap(OLD, NEW)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM approval WHERE kind = 'SUPPLIER_ACCEPT'"))
    _swap(NEW, OLD)
