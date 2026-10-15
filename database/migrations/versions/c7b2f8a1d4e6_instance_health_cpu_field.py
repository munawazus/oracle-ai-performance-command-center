"""instance health cpu field

Revision ID: c7b2f8a1d4e6
Revises: a1c4e9f2b6d3
Create Date: 2026-09-08 00:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7b2f8a1d4e6'
down_revision: Union[str, None] = 'a1c4e9f2b6d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('instance_health_snapshots') as batch_op:
        batch_op.add_column(sa.Column('cpu_centisecs_used', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('instance_health_snapshots') as batch_op:
        batch_op.drop_column('cpu_centisecs_used')
