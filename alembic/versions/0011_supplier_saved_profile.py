"""Supplier saved profile (declared figures for prefilled answers)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-06 09:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'supplier_saved_profile',
        sa.Column('org_id', sa.Uuid(), sa.ForeignKey('organization.id'), primary_key=True),
        sa.Column('values', sa.JSON(), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_by', sa.Uuid(), sa.ForeignKey('app_user.id'), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('supplier_saved_profile')
