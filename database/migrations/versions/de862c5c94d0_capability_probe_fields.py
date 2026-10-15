"""capability probe fields

Revision ID: de862c5c94d0
Revises: ebfa48c7d02b
Create Date: 2026-09-01 03:29:57.694803

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'de862c5c94d0'
down_revision: Union[str, None] = 'ebfa48c7d02b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch mode: SQLite has no ALTER COLUMN, so this must go through a batch/recreate.
    # No-op difference on Postgres - it still just runs ALTER TABLE ... ALTER COLUMN.
    with op.batch_alter_table('database_capabilities') as batch_op:
        batch_op.add_column(sa.Column('privilege_report', sa.JSON(), nullable=True))
        batch_op.alter_column(
            'detected_version',
            existing_type=sa.VARCHAR(length=50),
            type_=sa.String(length=100),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table('database_capabilities') as batch_op:
        batch_op.alter_column(
            'detected_version',
            existing_type=sa.String(length=100),
            type_=sa.VARCHAR(length=50),
            existing_nullable=True,
        )
        batch_op.drop_column('privilege_report')
