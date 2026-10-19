"""Таблицы: шаблоны импорта (их же колонки у выгрузок), чтение csv и xlsx, проверка колонок и строк."""

import csv
import io
from collections.abc import Iterable, Sequence
from datetime import date, datetime

from openpyxl import load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.worksheet.worksheet import Worksheet

Column = tuple[str, bool]  # (название, обязательная)


def _student(coursework: bool) -> list[Column]:
    return [
        ("Имя файла", True),
        ("ФИО автора", True),
        ("Тема", True),
        ("ФИО руководителя", True),
        ("Степень руководителя", False),
        ("Год", True),
        ("Уровень", True),
        ("Код направления", True),
        ("Название направления", False),
        ("Профиль", False),
        *([("Дисциплина", False), ("Работа или проект", False)] if coursework else []),
        ("Кафедра", True),
        ("Согласие на публикацию текста", False),
    ]


TEMPLATES: dict[str, list[Column]] = {
    "vkr": _student(False),
    "coursework": _student(True),
    "publication": [
        ("Авторы", True),
        ("Название", True),
        ("Вид", True),
        ("Журнал/сборник", True),
        ("Год", True),
        ("Том", False),
        ("Номер", False),
        ("Страницы", False),
        ("Издательство", False),
        ("DOI", False),
        ("ISSN/ISBN", False),
        ("Индексация", False),
        ("Ссылка", False),
        ("Кафедра", False),
    ],
    "talk": [
        ("Докладчики", True),
        ("Соавторы", False),
        ("Название доклада", True),
        ("Конференция", True),
        ("Уровень", True),
        ("Город", False),
        ("Страна", False),
        ("Дата начала", True),
        ("Дата окончания", False),
        ("Вид доклада", False),
        ("Формат", False),
        ("Награда", False),
        ("Ссылка", False),
        ("Кафедра", False),
    ],
}


MAX_ROWS = 10000
Row = tuple[int, list[str]]  # (номер строки, как его показывает Excel; ячейки)


class TableError(Exception):
    """Таблицу не прочитать: не csv/xlsx или битый файл."""


class TooManyRows(TableError):
    pass


def _norm(s: str) -> str:
    return " ".join(s.replace("*", "").split()).lower()


def parse_csv(text: str) -> list[list[str]]:
    """Разделитель угадывается по первой строке: русский Excel пишет «;». Пустые строки остаются: по ним считается номер."""
    text = text.removeprefix("\ufeff")
    first = text.split("\n", 1)[0]
    delimiter = max((";", "\t", ","), key=first.count) if any(d in first for d in ";\t,") else ","
    return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))


def _cell(v: object) -> str:
    if isinstance(v, datetime | date):
        return v.isoformat()[:10]
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return "" if v is None else str(v)


def _numbered(rows: Iterable[Sequence[object]], max_rows: int) -> list[Row]:
    """Непустые строки с номером: первая — заголовок, данных не больше max_rows, иначе TooManyRows."""
    out: list[Row] = []
    for n, row in enumerate(rows, start=1):
        cells = [_cell(c) for c in row]
        if any(c.strip() for c in cells):
            if len(out) > max_rows:
                raise TooManyRows(f"В таблице больше {max_rows} строк")
            out.append((n, cells))
    return out


def read_table(name: str, data: bytes, max_rows: int = MAX_ROWS) -> list[Row]:
    """csv или xlsx → непустые строки с номерами. xlsx — openpyxl read_only, рядом defusedxml: XXE не пройдёт."""
    try:
        if name.lower().endswith(".csv"):
            for encoding in ("utf-8-sig", "cp1251"):
                try:
                    text = data.decode(encoding)
                except UnicodeDecodeError:
                    continue
                return _numbered(parse_csv(text), max_rows)
            raise TableError("Кодировка не распознана")
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            return _numbered(wb.worksheets[0].iter_rows(values_only=True), max_rows)
        finally:
            wb.close()
    except TableError:
        raise
    except Exception as e:  # битый zip, не та книга — для пользователя это одно и то же
        raise TableError(str(e)) from e


def missing_columns(header: list[str], work_type: str) -> list[str]:
    have = {_norm(h) for h in header}
    return [name for name, required in TEMPLATES[work_type] if required and _norm(name) not in have]


def to_records(header: list[str], rows: list[list[str]], work_type: str) -> list[dict[str, str]]:
    """Строки данных → словари по названиям колонок шаблона; лишние колонки игнорируются."""
    keys = [_norm(h) for h in header]
    idx = [(name, keys.index(_norm(name)) if _norm(name) in keys else -1) for name, _ in TEMPLATES[work_type]]
    return [{name: (r[i].strip() if 0 <= i < len(r) else "") for name, i in idx} for r in rows]


def missing_in_row(record: dict[str, str], work_type: str) -> list[str]:
    return [name for name, required in TEMPLATES[work_type] if required and not record.get(name)]


# ---------- запись: «=HYPERLINK(…)» из импорта не должен стать живой формулой в Excel модератора ----------

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_value(v: object) -> object:
    return "'" + v if isinstance(v, str) and v.startswith(FORMULA_START) else v


def xlsx_cell(ws: Worksheet, v: object) -> WriteOnlyCell:
    cell = WriteOnlyCell(ws, v)
    if cell.data_type == "f":
        cell.data_type = "s"  # «=…» из данных — текст, а не формула
    return cell


def csv_line(row: list[object]) -> str:
    """Строка csv для Excel в русской локали: разделитель «;»."""
    buf = io.StringIO()
    csv.writer(buf, delimiter=";", lineterminator="\r\n").writerow([csv_value(v) for v in row])
    return buf.getvalue()


def template_csv(work_type: str) -> bytes:
    return ("\ufeff" + csv_line([name for name, _ in TEMPLATES[work_type]])).encode("utf-8")
