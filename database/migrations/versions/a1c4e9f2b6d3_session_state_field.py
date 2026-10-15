"""session state field

Revision ID: a1c4e9f2b6d3
Revises: f066c8bd9b6a
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c4e9f2b6d3'
down_revision: Union[str, None] = 'f066c8bd9b6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('session_samples') as batch_op:
        batch_op.add_column(sa.Column('state', sa.String(length=30), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('session_samples') as batch_op:
        batch_op.drop_column('state')
