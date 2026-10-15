"""capability cpu core count

Revision ID: 9b4e6d2a8f13
Revises: c7b2f8a1d4e6
Create Date: 2026-09-08 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9b4e6d2a8f13'
down_revision: Union[str, None] = 'c7b2f8a1d4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('database_capabilities') as batch_op:
        batch_op.add_column(sa.Column('cpu_count', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('database_capabilities') as batch_op:
        batch_op.drop_column('cpu_count')
