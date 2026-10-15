"""session last_call_et field

Revision ID: b3f7a2c9d1e4
Revises: e7d3b2f9a1c5
Create Date: 2026-09-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3f7a2c9d1e4'
down_revision: Union[str, None] = 'e7d3b2f9a1c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('session_samples') as batch_op:
        batch_op.add_column(sa.Column('last_call_et', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('session_samples') as batch_op:
        batch_op.drop_column('last_call_et')
