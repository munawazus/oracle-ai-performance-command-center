"""capacity alert pct basis

Revision ID: e7d3b2f9a1c5
Revises: c4f1a9d6e8b2
Create Date: 2026-09-09 02:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7d3b2f9a1c5'
down_revision: Union[str, None] = 'c4f1a9d6e8b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('capacity_alert_events') as batch_op:
        batch_op.add_column(sa.Column('pct_basis', sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('capacity_alert_events') as batch_op:
        batch_op.drop_column('pct_basis')
