"""Ключи «фамилия:инициал» сводят варианты латиницы: Philippovich = Филиппович, Arkhangelsky = Архангельский.

Пересчёт short_key у всех вариантов имён. Нераспознанные авторы, которые теперь совпадают, привяжутся при первом
открытии «Нераспознанных авторов» (там link_known).

Revision ID: 20261108_0009
"""

from collections.abc import Callable

import sqlalchemy as sa
from alembic import op

from shared.people.names import parse_name, short_key, translit

revision = "20261108_0009"
down_revision = "20261101_0008"


def _rekey(key: Callable[[str], str]) -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT person_id, name FROM person_names")).all()
    if rows:
        conn.execute(
            sa.text("UPDATE person_names SET short_key = :key WHERE person_id = :person_id AND name = :name"),
            [{"key": key(name), "person_id": pid, "name": name} for pid, name in rows],
        )


def _old_key(display: str) -> str:
    surname, initials = parse_name(display)
    initial = translit(initials[0]).lower()[:1] if initials else ""
    return f"{translit(surname).lower().replace('kh', 'h')}:{initial}"


def upgrade() -> None:
    _rekey(short_key)


def downgrade() -> None:
    _rekey(_old_key)
