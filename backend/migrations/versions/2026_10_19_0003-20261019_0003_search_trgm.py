"""Живой поиск: pg_trgm и триграммные индексы по названию работы и вариантам ФИО — для опечаток.

Revision ID: 20261019_0003
"""

from alembic import op

revision = "20261019_0003"
down_revision = "20261018_0002"


def upgrade() -> None:
    # pg_trgm — доверенное расширение: владельцу базы суперпользователь не нужен
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX ix_works_title_trgm ON works USING gin (replace(lower(title), 'ё', 'е') gin_trgm_ops)")
    op.execute(
        "CREATE INDEX ix_person_names_trgm ON person_names USING gin (replace(lower(name), 'ё', 'е') gin_trgm_ops)"
    )
    # порог оператора %>: при 0.6 по умолчанию одна опечатка в середине слова («нецронных») уже не находится,
    # её word_similarity ≈ 0.54. Новые подключения сервисов подхватят значение сами.
    op.execute(
        "DO $$ BEGIN EXECUTE format('ALTER DATABASE %I SET pg_trgm.word_similarity_threshold = 0.5', "
        "current_database()); END $$"
    )


def downgrade() -> None:
    op.execute("DO $$ BEGIN EXECUTE format('ALTER DATABASE %I RESET pg_trgm.word_similarity_threshold', "
               "current_database()); END $$")
    op.drop_index("ix_person_names_trgm", "person_names")
    op.drop_index("ix_works_title_trgm", "works")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
