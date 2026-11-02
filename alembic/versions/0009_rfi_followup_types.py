"""RFI, RFI_REPLY and FOLLOW_UP message types

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-01 09:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

OLD = ('BRIEF', 'INVITE', 'INTEREST_REPLY', 'DOC_REQUEST', 'DOC_SUBMISSION', 'QUAL_RESULT', 'RFQ', 'OFFER', 'COUNTER', 'ACCEPT', 'REJECT',
       'CONTRACT_DRAFT', 'REDLINE', 'SIGN_REQUEST', 'ONBOARD_DATA', 'ESCALATE')
NEW = OLD[:5] + ('RFI', 'RFI_REPLY', 'FOLLOW_UP') + OLD[5:]


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


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM message WHERE type IN ('RFI', 'RFI_REPLY', 'FOLLOW_UP')"))
    _swap(NEW, OLD)
