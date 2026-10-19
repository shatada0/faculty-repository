"""Таблицы: шаблоны импорта (их же колонки у выгрузок), чтение csv и xlsx, проверка колонок и строк."""

import csv
import io
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


class TableError(Exception):
    """Таблицу не прочитать: не csv/xlsx или битый файл."""


def _norm(s: str) -> str:
    return " ".join(s.replace("*", "").split()).lower()


def parse_csv(text: str) -> list[list[str]]:
    """Разделитель угадывается по первой строке: русский Excel пишет «;»."""
    text = text.removeprefix("\ufeff")
    first = text.split("\n", 1)[0]
    delimiter = max((";", "\t", ","), key=first.count) if any(d in first for d in ";\t,") else ","
    rows = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    return [r for r in rows if any(c.strip() for c in r)]


def _cell(v: object) -> str:
    if isinstance(v, datetime | date):
        return v.isoformat()[:10]
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return "" if v is None else str(v)


def read_table(name: str, data: bytes, max_rows: int = 10000) -> list[list[str]]:
    """csv или xlsx → строки ячеек. xlsx — openpyxl read_only, рядом defusedxml: XXE не пройдёт."""
    try:
        if name.lower().endswith(".csv"):
            for encoding in ("utf-8-sig", "cp1251"):
                try:
                    return parse_csv(data.decode(encoding))[: max_rows + 1]
                except UnicodeDecodeError:
                    continue
            raise TableError("Кодировка не распознана")
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows = []
        for row in wb.worksheets[0].iter_rows(values_only=True):
            cells = [_cell(c) for c in row]
            if any(c.strip() for c in cells):
                rows.append(cells)
            if len(rows) > max_rows:
                break
        wb.close()
        return rows
    except TableError:
        raise
    except Exception as e:  # битый zip, не та книга — для пользователя это одно и то же
        raise TableError(str(e)) from e


def missing_columns(header: list[str], work_type: str) -> list[str]:
    have = {_norm(h) for h in header}
    return [name for name, required in TEMPLATES[work_type] if required and _norm(name) not in have]


def to_records(rows: list[list[str]], work_type: str) -> list[dict[str, str]]:
    """Строки → словари по названиям колонок шаблона; лишние колонки игнорируются."""
    if not rows:
        return []
    header = [_norm(h) for h in rows[0]]
    idx = [(name, header.index(_norm(name)) if _norm(name) in header else -1) for name, _ in TEMPLATES[work_type]]
    return [{name: (r[i].strip() if 0 <= i < len(r) else "") for name, i in idx} for r in rows[1:]]


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
