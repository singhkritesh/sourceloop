"""SUPER_ADMIN role: ADMIN becomes an organization-scoped admin, the existing platform admins become SUPER_ADMIN

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25 09:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

OLD = ('BUYER', 'SUPPLIER', 'COMPLIANCE', 'QUALITY', 'LEGAL', 'FINANCE', 'EVALUATOR', 'DESIGNER', 'ADMIN')
NEW = OLD + ('SUPER_ADMIN',)
COLS = (('app_user', 'role'), ('approval', 'target_role'))


def _enum(values):
    return sa.Enum(*values, name='ck_roleenum', native_enum=False, create_constraint=True, length=32)


def _swap(values_from, values_to):
    bind = op.get_bind()
    for table, col in COLS:
        if bind.dialect.name == 'sqlite':
            # SQLite cannot alter a CHECK: rebuild the table with the new constraint (the old CHECK is dropped with the old type)
            with op.batch_alter_table(table, recreate='always') as b:
                b.alter_column(col, existing_type=_enum(values_from), type_=_enum(values_to), existing_nullable=False)
        else:
            op.execute(sa.text(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_roleenum'))
            quoted = ", ".join("'" + v + "'" for v in values_to)
            op.execute(sa.text(f'ALTER TABLE {table} ADD CONSTRAINT ck_roleenum CHECK ({col} IN ({quoted}))'))


def upgrade() -> None:
    _swap(OLD, NEW)
    # every admin that exists today is a platform operator: they keep all their access as SUPER_ADMIN
    op.execute(sa.text("UPDATE app_user SET role = 'SUPER_ADMIN' WHERE role = 'ADMIN'"))


def downgrade() -> None:
    op.execute(sa.text("UPDATE app_user SET role = 'ADMIN' WHERE role = 'SUPER_ADMIN'"))
    op.execute(sa.text("UPDATE approval SET target_role = 'ADMIN' WHERE target_role = 'SUPER_ADMIN'"))
    _swap(NEW, OLD)
