"""In-app mailbox (email and text windows)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-02 09:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'mailbox',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('owner_org_id', sa.Uuid(), sa.ForeignKey('organization.id'), nullable=False),
        sa.Column('owner_role', sa.String(12), nullable=False),
        sa.Column('channel', sa.String(8), nullable=False),
        sa.Column('direction', sa.String(4), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('subject', sa.String(200), nullable=False, server_default=''),
        sa.Column('body', sa.String(1000), nullable=False, server_default=''),
        sa.Column('project_id', sa.Uuid(), sa.ForeignKey('project.id'), nullable=True),
        sa.Column('approval_id', sa.Uuid(), sa.ForeignKey('approval.id'), nullable=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_mailbox_owner_org_id', 'mailbox', ['owner_org_id'])
    op.create_index('ix_mailbox_project_id', 'mailbox', ['project_id'])
    op.create_index('ix_mailbox_approval_id', 'mailbox', ['approval_id'])


def downgrade() -> None:
    op.drop_table('mailbox')
