"""Справочники: журналы, классификаторы, направления подготовки.

python -m admin_service.src.refdata load journals|classifiers|directions <файл.csv>
python -m admin_service.src.refdata init — заполняет пустые справочники из fixtures/ (сервис migrations после alembic upgrade)

Upsert в одной транзакции; строки не удаляются, поэтому коды, на которые ссылаются работы, остаются.
Форматы csv (разделитель «;», первая строка — заголовок):
  journals:    issn;name;lists       — lists через запятую: white2,vak_k1,scopus_q2
  classifiers: scheme;code;name      — scheme: grnti | oecd | wos | asjc
  directions:  code;name[;subjects]  — subjects через запятую: grnti:50.41,oecd:1.02 — рубрики, которые импорт
               ставит ВКР и курсовым этого направления; без колонки рубрики направлений не меняются
"""

import argparse
import asyncio
import csv
import io
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from shared.db.base import Base
from shared.logging import get_logger, setup_logging
from shared.refdata import Classifier, Direction, Journal
from shared.uow import uow_write_factory

logger = get_logger("refdata")
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
TABLES: dict[str, tuple[type[Base], list[str]]] = {
    "journals": (Journal, ["issn", "name", "lists"]),
    "classifiers": (Classifier, ["scheme", "code", "name"]),
    "directions": (Direction, ["code", "name", "subjects"]),
}
OPTIONAL = {"subjects"}


def read_rows(kind: str, text: str) -> list[dict]:
    _, columns = TABLES[kind]
    rows = list(csv.DictReader(io.StringIO(text.removeprefix("\N{BYTE ORDER MARK}")), delimiter=";"))
    if rows and set(columns) - OPTIONAL - set(rows[0]):
        raise SystemExit(f"В {kind}.csv нужны колонки: {';'.join(c for c in columns if c not in OPTIONAL)}")
    out = []
    for row in rows:
        values = {c: (row.get(c) or "").strip() for c in columns if c in row}  # нет колонки — значение не трогаем
        for c in ("lists", "subjects"):
            if c in values:
                values[c] = [x.strip() for x in values[c].split(",") if x.strip()]
        out.append(values)
    return out


async def load(kind: str, text: str, *, only_if_empty: bool = False) -> int:
    model, _ = TABLES[kind]
    rows = read_rows(kind, text)
    keys = [c.name for c in model.__table__.primary_key.columns]
    async with uow_write_factory() as uow:
        if only_if_empty and await uow.session.scalar(select(func.count()).select_from(model)):
            return 0
        if kind == "directions":  # опечатка в коде рубрики уронила бы импорт ВКР на внешнем ключе
            known = {
                f"{s}:{c}" for s, c in (await uow.session.execute(select(Classifier.scheme, Classifier.code))).all()
            }
            if unknown := sorted({x for r in rows for x in r.get("subjects", [])} - known):
                raise SystemExit(f"Рубрик нет в классификаторах: {', '.join(unknown)}")
        for chunk in range(0, len(rows), 1000):
            stmt = insert(model).values(rows[chunk : chunk + 1000])
            stmt = stmt.on_conflict_do_update(
                index_elements=keys, set_={c: stmt.excluded[c] for c in rows[0] if c not in keys}
            )
            await uow.session.execute(stmt)
    logger.info("Справочник загружен", extra={"kind": kind, "rows": len(rows)})
    return len(rows)


async def init() -> None:
    for kind in TABLES:
        text = await asyncio.to_thread((FIXTURES / f"{kind}.csv").read_text, encoding="utf-8")
        await load(kind, text, only_if_empty=True)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(prog="python -m admin_service.src.refdata")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("load")
    cmd.add_argument("kind", choices=list(TABLES))
    cmd.add_argument("file", type=Path)
    sub.add_parser("init")
    args = parser.parse_args()
    if args.command == "init":
        asyncio.run(init())
    else:
        print(f"Загружено строк: {asyncio.run(load(args.kind, args.file.read_text(encoding='utf-8-sig')))}")


if __name__ == "__main__":
    main()
