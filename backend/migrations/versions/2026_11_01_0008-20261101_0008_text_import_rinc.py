"""Импорт списка работ текстом (import_items.text, imports.kind = text) и коды РИНЦ у человека (SPIN, AuthorID).

Revision ID: 20261101_0008
"""

import sqlalchemy as sa
from alembic import op

revision = "20261101_0008"
down_revision = "20261101_0007"


def upgrade() -> None:
    op.add_column("import_items", sa.Column("text", sa.Text(), nullable=True))
    op.drop_constraint(op.f("ck_imports_kind"), "imports", type_="check")
    op.create_check_constraint(op.f("ck_imports_kind"), "imports", "kind IN ('archive', 'table', 'text')")
    op.add_column("people", sa.Column("spin", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("elibrary_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("people", "elibrary_id")
    op.drop_column("people", "spin")
    op.execute("DELETE FROM imports WHERE kind = 'text'")
    op.drop_constraint(op.f("ck_imports_kind"), "imports", type_="check")
    op.create_check_constraint(op.f("ck_imports_kind"), "imports", "kind IN ('archive', 'table')")
    op.drop_column("import_items", "text")
