"""Причина отклонения материала: автор видит её в «Моих работах».

Revision ID: 20261019_0004
"""

import sqlalchemy as sa
from alembic import op

revision = "20261019_0004"
down_revision = "20261019_0003"


def upgrade() -> None:
    op.add_column("materials", sa.Column("reject_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("materials", "reject_reason")
