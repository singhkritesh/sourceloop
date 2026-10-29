"""persistent access-token revocation list

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21 18:00:00
"""
from alembic import op
import sqlalchemy as sa
import sourceloop.data.models

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('revoked_token',
    sa.Column('jti', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sourceloop.data.models.UTCDateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sourceloop.data.models.UTCDateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('jti')
    )
    op.create_index(op.f('ix_revoked_token_expires_at'), 'revoked_token', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_revoked_token_expires_at'), table_name='revoked_token')
    op.drop_table('revoked_token')
