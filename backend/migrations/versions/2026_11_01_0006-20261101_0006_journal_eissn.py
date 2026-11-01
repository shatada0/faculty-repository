"""Электронный ISSN журнала: Crossref отдаёт печатный и электронный, справочник может знать любой из них.

Revision ID: 20261101_0006
"""

import sqlalchemy as sa
from alembic import op

revision = "20261101_0006"
down_revision = "20261020_0005"


def upgrade() -> None:
    op.add_column("journals", sa.Column("eissn", sa.Text(), nullable=True))
    op.create_unique_constraint(op.f("uq_journals_eissn"), "journals", ["eissn"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_journals_eissn"), "journals", type_="unique")
    op.drop_column("journals", "eissn")
