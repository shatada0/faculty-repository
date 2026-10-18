"""ВКР в один шаг: элемент импорта может сразу опубликоваться, у направления — рубрики по умолчанию.

Revision ID: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"

OLD = "status IN ('pending', 'extracted', 'filled', 'duplicate', 'error')"
NEW = "status IN ('pending', 'extracted', 'filled', 'published', 'duplicate', 'error')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_import_items_status"), "import_items", type_="check")
    op.create_check_constraint(op.f("ck_import_items_status"), "import_items", NEW)
    # «grnti:50.41» и т. п.: импорт ставит эти рубрики работе с таким кодом направления
    op.add_column(
        "directions", sa.Column("subjects", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("directions", "subjects")
    op.execute("UPDATE import_items SET status = 'filled' WHERE status = 'published'")
    op.drop_constraint(op.f("ck_import_items_status"), "import_items", type_="check")
    op.create_check_constraint(op.f("ck_import_items_status"), "import_items", OLD)
