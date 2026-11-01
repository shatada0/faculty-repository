"""Transactional outbox для событий шины: import_item.created пишется в одной транзакции с элементом импорта.

writer получает права по ALTER DEFAULT PRIVILEGES из 0001; каталогу таблица не нужна.

Revision ID: 20261101_0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261101_0007"
down_revision = "20261101_0006"


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
    )
    op.execute("REVOKE ALL ON outbox FROM catalog")


def downgrade() -> None:
    op.drop_table("outbox")
