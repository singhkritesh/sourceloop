"""app_setting.kill_switch: operators can pause every run platform-wide

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-26 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('app_setting') as b:
        b.add_column(sa.Column('kill_switch', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('app_setting') as b:
        b.drop_column('kill_switch')
