"""per database capacity thresholds

Revision ID: c4f1a9d6e8b2
Revises: 9b4e6d2a8f13
Create Date: 2026-09-09 01:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4f1a9d6e8b2'
down_revision: Union[str, None] = '9b4e6d2a8f13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('monitored_databases') as batch_op:
        batch_op.add_column(sa.Column('capacity_warn_pct_used', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('capacity_critical_pct_used', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('capacity_warn_days_to_full', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('capacity_critical_days_to_full', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('monitored_databases') as batch_op:
        batch_op.drop_column('capacity_critical_days_to_full')
        batch_op.drop_column('capacity_warn_days_to_full')
        batch_op.drop_column('capacity_critical_pct_used')
        batch_op.drop_column('capacity_warn_pct_used')
