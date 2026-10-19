"""Роль catalog без таблиц импорта: там исходные файлы и сырые ответы ИИ, публичному каталогу они не нужны.

Черновики в works по-прежнему видны роли — от них закрывает только published() в коде.

Revision ID: 20261020_0005
"""

from alembic import op

revision = "20261020_0005"
down_revision = "20261019_0004"

TABLES = "imports, import_items, extraction_results"


def upgrade() -> None:
    op.execute(f"REVOKE ALL ON {TABLES} FROM catalog")


def downgrade() -> None:
    op.execute(f"GRANT SELECT ON {TABLES} TO catalog")
