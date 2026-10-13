"""Начальные данные.

python -m admin_service.seed people staff.csv — сотрудники и кафедры: «ФИО», «Кафедра», «Должность», «Степень», «Звание», «ORCID».
    Повторный запуск не создаёт дублей: человек ищется по exact_key.
python -m admin_service.seed demo — демо-данные, перенос src/mocks/seed.ts с тем же генератором: фронт проверяется
    на настоящем бэке с теми же данными. Отказывается работать, если в базе уже есть работы, — прод не затрёт.
"""

import argparse
import asyncio
import math
import re
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from admin_service import demo_data as D
from shared.db.uow import uow_write_factory
from shared.logging import get_logger, setup_logging
from shared.models import Classifier, Department, Import, ImportItem, Material, Person, Work, WorkCitation
from shared.names import latin_name, parse_name, short_name, translit
from shared.repository.people import PersonRepository, add_name
from shared.repository.works import WorkRepository
from shared.storage import Storage
from shared.tables import read_table
from shared.works import apply_write, parse_write

logger = get_logger("seed")
SAMPLE_PDF = Path(__file__).resolve().parent.parent / "fixtures" / "sample.pdf"
SAMPLE_KEY = "demo/sample.pdf"


# ---------- генератор: mulberry32 как в моках, чтобы данные совпали ----------


def _i32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x & 0x80000000 else x


def _imul(a: int, b: int) -> int:
    return _i32((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF))


class Rng:
    def __init__(self, seed: int) -> None:
        self.s = seed

    def rand(self) -> float:
        self.s = _i32(self.s + 0x6D2B79F5)
        s = self.s
        t = _imul(s ^ ((s & 0xFFFFFFFF) >> 15), 1 | s)
        t = _i32(_i32(t + _imul(t ^ ((t & 0xFFFFFFFF) >> 7), 61 | t)) ^ t)
        return ((t ^ ((t & 0xFFFFFFFF) >> 14)) & 0xFFFFFFFF) / 4294967296

    def int(self, a: int, b: int) -> int:
        return a + math.floor(self.rand() * (b - a + 1))

    def pick(self, items: list) -> Any:
        return items[math.floor(self.rand() * len(items))]

    def chance(self, p: float) -> bool:
        return self.rand() < p

    def conf(self, lo: float = 0.45, hi: float = 0.98) -> float:
        return math.floor((lo + self.rand() * (hi - lo)) * 100 + 0.5) / 100


GENERIC_KW = [
    ["машинное обучение", "обработка естественного языка", "анализ данных"],
    ["математическое моделирование", "оптимизация", "численные методы"],
    ["разработка программного обеспечения", "качество ПО", "архитектура ПО"],
]


def _pad(n: int, w: int = 2) -> str:
    return str(n).zfill(w)


def _cap2026(iso: str) -> str:
    return "2026-10-05" if iso > "2026-10-05" else iso


def _add_days(iso: str, n: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=n)).isoformat()


def build_demo() -> dict[str, list[dict]]:
    """Люди, работы и импорты в форме моков (id вида p-s1, vkr-3) в том же порядке и с теми же случайными числами."""
    r = Rng(20260601)
    people: list[dict] = []
    works: list[dict] = []
    imports: list[dict] = []
    staff = [
        {
            "id": f"p-s{i + 1}",
            "kind": "staff",
            "fullName": n,
            "department": D.DEPTS[d].name,
            "position": pos,
            "academicDegree": deg,
            "academicTitle": title,
            "orcid": orcid,
            "aliases": [short_name(n), latin_name(n)],
        }
        for i, (n, d, pos, deg, title, orcid) in enumerate(D.STAFF)
    ]
    external = [
        {
            "id": f"p-e{i + 1}",
            "kind": "external",
            "fullName": n,
            "affiliation": aff,
            "aliases": [short_name(n), latin_name(n)],
        }
        for i, (n, aff) in enumerate(D.EXTERNAL)
    ]
    people += staff + external
    staff_dept = lambda d: [p for p in staff if p["department"] == D.DEPTS[d].name]  # noqa: E731
    used = {p["fullName"] for p in people}
    student_n = 0

    def new_student(d: int, kind: str) -> dict:
        nonlocal student_n
        while True:
            female = r.chance(0.45)
            sur = r.pick(D.SURNAMES)
            pat = r.pick(D.PATRONYMICS)
            full = (
                f"{sur}а {r.pick(D.FEMALE)} {re.sub('ич$', 'на', pat)}" if female else f"{sur} {r.pick(D.MALE)} {pat}"
            )
            if full not in used:
                break
        used.add(full)
        student_n += 1
        p = {
            "id": f"p-st{student_n}",
            "kind": kind,
            "fullName": full,
            "department": D.DEPTS[d].name,
            "aliases": [short_name(full)],
        }
        people.append(p)
        return p

    def ref(p: dict, en: bool = False) -> dict:
        return {
            "personId": p["id"],
            "displayName": latin_name(p["fullName"]) if en else short_name(p["fullName"]),
            "kind": p["kind"],
        }

    titles: set[str] = set()

    def unique(make: Any) -> dict:
        res = make()
        for _ in range(30):
            if res["title"] not in titles:
                break
            res = make()
        titles.add(res["title"])
        return res

    def topic(d: D.Dept) -> tuple[dict, dict]:
        return r.pick(d.methods), r.pick(d.tasks)

    def ru_title(d: D.Dept) -> dict:
        def make() -> dict:
            m, t = topic(d)
            return {
                "m": m,
                "t": t,
                "title": r.pick(
                    [
                        f"{m['nom']} в задаче {t['gen']}",
                        f"Применение {m['gen']} для {t['gen']}",
                        f"Метод {t['gen']} на основе {m['gen']}",
                        f"Сравнительный анализ {m['gen']} для {t['gen']}",
                        f"Об эффективности {m['gen']} в задачах {t['gen']}",
                        f"Модель {t['gen']} с использованием {m['gen']}",
                    ]
                ),
            }

        return unique(make)

    def en_title(d: D.Dept) -> dict:
        def make() -> dict:
            m, t = topic(d)
            cap = m["en"][:1].upper() + m["en"][1:]
            return {
                "m": m,
                "t": t,
                "title": r.pick(
                    [
                        f"{cap} for {t['en']}",
                        f"Applying {m['en']} to {t['en']}",
                        f"An empirical study of {m['en']} for {t['en']}",
                    ]
                ),
            }

        return unique(make)

    def student_title(d: D.Dept) -> dict:
        def make() -> dict:
            m, t = topic(d)
            sys_ = r.pick(D.SYSTEMS)
            title = r.pick(
                [
                    f"Разработка {sys_} для {t['gen']}",
                    f"Разработка {sys_} {t['gen']} с использованием {m['gen']}",
                    f"Исследование {m['gen']} в задаче {t['gen']}",
                    f"Применение {m['gen']} для {t['gen']}",
                ]
            )
            annotation = (
                f"Работа посвящена разработке {sys_} для {t['gen']}. Выполнен анализ предметной области и существующих решений, "
                f"спроектирована архитектура, реализован прототип с применением {m['gen']}. Проведено тестирование и оценка "
                "качества работы системы."
                if title.startswith("Разработка")
                else f"В работе исследуется применимость {m['gen']} для {t['gen']}. Разработан программный прототип, выполнено сравнение "
                "с альтернативными решениями по точности и времени работы."
            )
            return {"m": m, "t": t, "title": title, "annotation": annotation}

        return unique(make)

    def ann_ru(m: dict, t: dict) -> str:
        return r.pick(
            [
                f"Рассматривается задача {t['gen']}. Предложен подход на основе {m['gen']}. "
                "Проведены вычислительные эксперименты на данных "
                "факультета; результаты показывают преимущество предложенного подхода перед базовыми методами.",
                f"Статья посвящена вопросам {t['gen']}. Описаны существующие подходы и их ограничения, предложена модификация {m['gen']}. "
                "Приведены результаты экспериментальной проверки и рекомендации по практическому применению.",
                f"Исследуется применимость {m['gen']} для {t['gen']}. Разработан программный прототип, выполнено сравнение с "
                "альтернативными решениями по точности и времени работы.",
            ]
        )

    def ann_en(m: dict, t: dict) -> str:
        return (
            f"The paper addresses {t['en']}. We propose an approach based on {m['en']} and evaluate it on real data. "
            "The results show that the approach outperforms the baselines."
        )

    def ann_talk(m: dict, t: dict) -> str:
        return (
            f"В докладе представлены результаты исследования {m['gen']} для {t['gen']}. "
            "Обсуждаются ограничения подхода и направления дальнейшей работы."
        )

    def kw(d: int, m: dict, t: dict, en: bool = False) -> list[str]:
        return [m["en"], t["en"]] if en else [m["kw"], t["kw"], r.pick(GENERIC_KW[d])]

    def meta(published_at: str, **extra: Any) -> dict:
        return {
            "status": "published",
            "fieldMeta": {},
            "createdAt": published_at,
            "updatedAt": published_at,
            "publishedAt": published_at,
            **extra,
        }

    def pick_supervisor(d: int) -> dict:
        return r.pick([p for p in staff_dept(d) if not (p.get("position") or "").startswith("ассистент")])

    def student_work(kind: str, d: int, year: int, n: int) -> dict:
        dept = D.DEPTS[d]
        dirs = [x for x in dept.directions if x["level"] == "bachelor"] if kind == "coursework" else dept.directions
        direction = r.pick(dirs) if dirs else dept.directions[0]
        sup = pick_supervisor(d)
        sups = [ref(sup)]
        if kind == "vkr" and r.chance(0.1):
            other = next((p for p in staff_dept(d) if p is not sup), None)
            if other:
                sups.append(ref(other))
        author = new_student(d, "graduate" if kind == "vkr" or year < 2023 else "student")
        st = student_title(dept)
        consent = kind == "vkr" and r.chance(0.7)
        month = "07" if kind == "vkr" else r.pick(["06", "12"])
        published_at = f"{year}-{month}-{_pad(r.int(3, 25))}"
        w = {
            "id": f"{'vkr' if kind == 'vkr' else 'cw'}-{n}",
            "type": kind,
            "title": st["title"],
            "authors": [ref(author)],
            "year": year,
            "department": dept.name,
            "annotation": st["annotation"],
        }
        w["keywords"] = kw(d, st["m"], st["t"])
        w |= {
            "language": "ru",
            "accessLevel": "full" if consent else "card_only",
            "hasFile": True,
            "supervisors": sups,
            "level": direction["level"],
            "direction": {"code": direction["code"], "name": direction["name"]},
            "profile": direction["profile"],
        }
        w["discipline"] = r.pick(dept.disciplines) if kind == "coursework" else None
        w["pages"] = r.int(45, 110) if kind == "vkr" else r.int(24, 45)
        w["consent"] = consent
        surname = parse_name(author["fullName"])[0]
        return (
            w
            | meta(_cap2026(published_at))
            | {"originalFileName": f"{surname}_{'ВКР' if kind == 'vkr' else 'КР'}_{year}.pdf"}
        )

    vn = 0
    for yi, count in enumerate([6, 7, 7, 8, 8, 8, 8, 8]):
        for _ in range(count):
            d, vn = vn % 3, vn + 1
            works.append(student_work("vkr", d, 2019 + yi, vn))
    cn = 0
    for yi, count in enumerate([3, 3, 4, 4, 4, 4, 4, 4]):
        for _ in range(count):
            d, cn = cn % 3, cn + 1
            works.append(student_work("coursework", d, 2019 + yi, cn))
    by_id = lambda wid: next(w for w in works if w["id"] == wid)  # noqa: E731
    by_id("vkr-14")["accessLevel"] = "hidden"
    by_id("vkr-42")["accessLevel"] = "hidden"
    by_id("cw-7").update(status="rejected", rejectReason="Работа загружена повторно с ошибкой в теме")

    coauthor_pool: list[dict] = []

    def co_student(d: int) -> dict:
        pool = [p for p in coauthor_pool if p["department"] == D.DEPTS[d].name]
        if pool and (len(coauthor_pool) >= 30 or r.chance(0.4)):
            return r.pick(pool)
        p = new_student(d, "student")
        coauthor_pool.append(p)
        return p

    def authors_for(d: int, lead: dict, max_n: int = 3) -> list[dict]:
        out = [lead]
        for _ in range(r.int(0, max_n)):
            x = r.rand()
            c = (
                r.pick(staff_dept(d))
                if x < 0.4
                else r.pick(staff)
                if x < 0.55
                else co_student(d)
                if x < 0.8
                else r.pick(external)
            )
            if c not in out:
                out.append(c)
        return out

    events: dict[str, dict] = {}

    def event_for(c: D.Conference, year: int) -> dict:
        key = f"{c.short}-{year}"
        if key not in events:
            start = f"{year}-{_pad(c.month)}-{_pad(r.int(10, 24))}"
            days = 0 if c.short.startswith("Искусственный") else r.int(1, 2)
            events[key] = {
                "name": c.name(year - c.base + 1 if c.base else 1, year),
                "level": c.level,
                "city": c.city,
                "country": c.country,
                "dateStart": start,
                "dateEnd": _add_days(start, days) if days else None,
                "url": c.url,
            }
        return events[key]

    pn = 0
    doi_abbr: dict[str, str] = {}

    def doi_for(venue: str, year: int) -> str:
        if venue not in doi_abbr:
            parts = [x for x in re.split(r"[\s.:]+", venue) if len(x) > 3]
            doi_abbr[venue] = "".join(translit(x[0]).lower() for x in parts)[:5]
        return f"10.12345/{doi_abbr[venue]}.{year}.{_pad(r.int(1, 999), 4)}"

    talk_kinds = ["section", "section", "section", "section", "plenary", "invited", "poster", "poster"]
    for i in range(45):
        c = D.CONFERENCES[i % len(D.CONFERENCES)]
        year = r.int(2019, 2026)
        if year == 2026 and c.month > 9:
            year = 2025
        ev = event_for(c, year)
        d = r.pick(c.depts)
        student = c.level == "university"
        lead = co_student(d) if student else r.pick(staff_dept(d))
        people_ = [lead, pick_supervisor(d)] if student else authors_for(d, lead, 2)
        en = c.en
        tt = en_title(D.DEPTS[d]) if en else ru_title(D.DEPTS[d])
        presenters = [ref(lead, en)]
        if not student and len(people_) > 1 and people_[1]["kind"] == "staff" and r.chance(0.2):
            presenters.append(ref(people_[1], en))
        fmt = "online" if year in (2020, 2021) else "offline" if r.chance(0.8) else r.pick(["hybrid", "online"])
        award = (r.pick(D.AWARDS) if r.chance(0.5) else None) if student else D.AWARDS[3] if r.chance(0.05) else None
        talk = {
            "id": f"talk-{i + 1}",
            "type": "talk",
            "title": tt["title"],
            "authors": [ref(p, en) for p in people_],
            "year": year,
            "department": D.DEPTS[d].name,
            "annotation": ann_en(tt["m"], tt["t"]) if en else ann_talk(tt["m"], tt["t"]),
        }
        talk["keywords"] = kw(d, tt["m"], tt["t"], en)
        talk |= {"language": "en" if en else "ru", "accessLevel": "full"}
        talk["hasFile"] = r.chance(0.6)
        talk["event"] = ev
        talk["kind"] = "section" if student else r.pick(talk_kinds)
        talk |= {"format": fmt, "presenters": presenters, "award": award}
        works.append(talk | meta(_cap2026(_add_days(ev["dateStart"], r.int(10, 40)))))
        talk = works[-1]
        if i < 28:
            abstract = i < 22
            pn += 1
            pub = {
                "id": f"pub-{pn}",
                "type": "publication",
                "title": tt["title"],
                "authors": talk["authors"],
                "year": year,
                "department": talk["department"],
                "annotation": talk["annotation"],
                "keywords": talk["keywords"],
                "language": talk["language"],
            }
            pub["accessLevel"] = "full" if abstract and r.chance(0.5) else "card_only"
            pub |= {
                "hasFile": False,
                "kind": "abstract" if abstract else "proceedings_paper",
                "venue": f"{ev['name']} : {'proceedings' if en else 'тезисы докладов' if abstract else 'сборник трудов'}",
            }
            if abstract:
                a = r.int(20, 300)
                pub["pages"] = f"{a}–{a + 1}"
            else:
                a = r.int(10, 400)
                pub["pages"] = f"{a}–{a + r.int(6, 11)}"
            pub |= {"publisher": "Springer" if en else D.PUBLISHER, "indexing": ["scopus"] if en else ["rinc"]}
            pub["doi"] = doi_for("DAKE", year) if en else None
            pub["isbn"] = f"978-5-{r.int(1000, 9999)}-{r.int(1000, 9999)}-{r.int(0, 9)}"
            pub["relatedTalkId"] = talk["id"]
            pub["hasFile"] = pub["accessLevel"] == "full"
            talk["relatedPublicationId"] = pub["id"]
            works.append(pub | meta(_cap2026(_add_days(ev["dateEnd"] or ev["dateStart"], r.int(30, 90)))))

    def publication(kind: str, d: int) -> dict:
        nonlocal pn
        year = r.int(2019, 2026)
        lead = r.pick(staff_dept(d))
        authors = authors_for(d, lead, 3 if kind == "journal_article" else 2)
        venue, indexing, en = "", ["rinc"], False
        volume = issue = pages = issn = isbn = publisher = None
        if kind == "journal_article":
            j = r.pick([x for x in D.JOURNALS if d in x.depts])
            venue, indexing, en, issn = j.name, j.indexing, j.en, j.issn
            volume = str(r.int(5, 30)) if r.chance(0.7) else None
            issue = str(r.int(1, 6))
            a = r.int(10, 180)
            pages = f"{a}–{a + r.int(8, 16)}"
            tt = en_title(D.DEPTS[d]) if en else ru_title(D.DEPTS[d])
        elif kind == "proceedings_paper":
            c = r.pick([x for x in D.CONFERENCES if d in x.depts and not x.en and x.base])
            venue = f"{event_for(c, year)['name']} : сборник трудов"
            publisher = D.PUBLISHER
            a = r.int(10, 300)
            pages = f"{a}–{a + r.int(5, 9)}"
            tt = ru_title(D.DEPTS[d])
        elif kind == "book_chapter":
            venue, publisher = D.COLLECTIVE, D.PUBLISHER
            a = r.int(10, 200)
            pages = f"{a}–{a + r.int(15, 30)}"
            isbn = f"978-5-{r.int(1000, 9999)}-{r.int(1000, 9999)}-{r.int(0, 9)}"
            tt = ru_title(D.DEPTS[d])
        else:
            tt = ru_title(D.DEPTS[d])
            tt["title"] = (
                f"Методические указания к лабораторным работам по дисциплине «{r.pick(D.DEPTS[d].disciplines)}»"
            )
            titles.add(tt["title"])
            venue, publisher, pages = "Методические указания", D.PUBLISHER, str(r.int(32, 64))
        doi = doi_for(venue, year) if kind in ("journal_article", "proceedings_paper") and r.chance(0.55) else None
        is_open = r.chance(0.2)
        pn += 1
        p = {
            "id": f"pub-{pn}",
            "type": "publication",
            "title": tt["title"],
            "authors": [ref(a, en) for a in authors],
            "year": year,
            "department": D.DEPTS[d].name,
        }
        p["annotation"] = ann_en(tt["m"], tt["t"]) if en else ann_ru(tt["m"], tt["t"])
        p["keywords"] = kw(d, tt["m"], tt["t"], en)
        p |= {
            "language": "en" if en else "ru",
            "accessLevel": "full" if is_open else "card_only",
            "hasFile": is_open,
            "kind": kind,
            "venue": venue,
            "volume": volume,
            "issue": issue,
            "pages": pages,
            "publisher": publisher,
            "doi": doi,
            "issn": issn,
            "isbn": isbn,
            "indexing": indexing,
        }
        url = None
        if not doi and kind == "journal_article" and r.chance(0.6):
            url = f"https://journal.example.ru/articles/{year}/{r.int(100, 999)}"
        p["externalUrl"] = url
        return p | meta(_cap2026(f"{year}-{_pad(r.int(1, 12))}-{_pad(r.int(1, 28))}"))

    for i in range(40):
        works.append(publication("journal_article", i % 3))
    for i in range(12):
        works.append(publication("proceedings_paper", i % 3))
    for i in range(3):
        works.append(publication("book_chapter", i % 3))
    for i in range(2):
        works.append(publication("other", i))
    for books, kind in ((D.MONOGRAPHS, "monograph"), (D.TEXTBOOKS, "textbook")):
        for title, dept, pages in books:
            year = r.int(2019, 2025)
            lead = r.pick(staff_dept(dept))
            authors = [lead, *[p for p in staff_dept(dept) if p is not lead][: r.int(0, 2)]]
            m, t = topic(D.DEPTS[dept])
            pn += 1
            annotation = (
                f"В монографии систематизированы результаты исследований авторов в области {t['gen']}. Рассмотрены "
                f"подходы на основе {m['gen']}, приведены примеры практического применения."
                if kind == "monograph"
                else "Учебное пособие содержит теоретический материал, примеры и задания для самостоятельной работы. "
                "Предназначено для студентов направлений кафедры."
            )
            w = {
                "id": f"pub-{pn}",
                "type": "publication",
                "title": title,
                "authors": [ref(a) for a in authors],
                "year": year,
                "department": D.DEPTS[dept].name,
                "annotation": annotation,
            }
            w["keywords"] = kw(dept, m, t)
            w |= {
                "language": "ru",
                "accessLevel": "full" if kind == "textbook" else "card_only",
                "hasFile": kind == "textbook",
                "kind": kind,
                "venue": "",
                "pages": pages,
                "publisher": D.PUBLISHER,
            }
            w["isbn"] = f"978-5-{r.int(1000, 9999)}-{r.int(1000, 9999)}-{r.int(0, 9)}"
            w["indexing"] = ["rinc"]
            works.append(w | meta(f"{year}-{_pad(r.int(2, 11))}-{_pad(r.int(1, 28))}"))
    by_id("pub-60")["accessLevel"] = "hidden"

    # ---- импорты и черновики ----
    def archive_meta(low: bool) -> dict:
        out = {
            k: {"source": "table"}
            for k in ("title", "authors", "supervisors", "year", "level", "direction", "department", "profile")
        }
        out["annotation"] = {"source": "ai", "confidence": 0.48 if low else r.conf(0.72, 0.97)}
        out["keywords"] = {"source": "ai", "confidence": 0.62 if low else r.conf(0.7, 0.95)}
        return out | {"pages": {"source": "ai", "confidence": 0.99}, "language": {"source": "ai", "confidence": 0.99}}

    def all_ai() -> dict:
        keys = [
            "title",
            "authors",
            "supervisors",
            "year",
            "level",
            "direction",
            "department",
            "discipline",
            "annotation",
            "keywords",
            "pages",
            "language",
        ]
        return {k: {"source": "ai", "confidence": r.conf(0.5, 0.97)} for k in keys}

    def text(name: str, kind: str = "staff") -> dict:
        return {"personId": None, "displayName": name, "kind": kind}

    def draft(import_id: str, created_at: str, field_meta: dict) -> dict:
        return {
            "status": "draft",
            "importId": import_id,
            "createdAt": created_at,
            "updatedAt": created_at,
            "fieldMeta": field_meta,
            "publishedAt": None,
        }

    for w in works:
        if w["type"] == "vkr" and w["year"] == 2025:
            w["importId"] = "imp-0"
    items0 = [
        {"ref": w["originalFileName"], "title": w["title"], "status": "filled", "workId": w["id"]}
        for w in works
        if w.get("importId") == "imp-0"
    ]
    imports.append(
        {
            "id": "imp-0",
            "kind": "archive",
            "workType": "vkr",
            "fileName": "ВКР_2025_все_кафедры.zip",
            "metaTableName": "сведения_ВКР_2025.xlsx",
            "createdAt": "2025-07-01T09:30:00Z",
            "items": items0,
        }
    )

    t1 = "2026-06-28T10:15:00Z"
    items1 = []
    for k in range(8):
        if k == 2:
            items1.append({"ref": "Скан_Тарасов_ВКР_2026.pdf", "status": "error", "error": "Скан без текстового слоя"})
            continue
        vn += 1
        w = student_work("vkr", 0, 2026, vn)
        sup_text = "Белова Н." if k == 0 else "Кузнецов А." if k == 3 else None
        if sup_text:
            w["supervisors"] = [text(sup_text)]
        w |= draft("imp-1", t1, archive_meta(k == 4))
        if k == 6:  # строка таблицы сведений была пустой: обязательных полей нет, пока модератор их не заполнит
            w |= {"direction": {"code": "", "name": ""}, "department": ""}
            w["fieldMeta"]["direction"] = {"source": "ai", "confidence": 0.31}
        item = {
            "ref": w["originalFileName"],
            "title": w["title"],
            "status": "filled",
            "workId": w["id"],
            "unmatchedAuthors": [sup_text] if sup_text else [],
        }
        if k == 5:
            orig = next(
                x
                for x in works
                if x["type"] == "vkr"
                and x["status"] == "published"
                and x["department"] == D.DEPTS[0].name
                and x["year"] == 2026
            )
            w |= {"title": orig["title"], "annotation": orig["annotation"], "duplicateOf": orig["id"]}
            item |= {"status": "duplicate", "title": orig["title"], "duplicateOf": orig["id"]}
        works.append(w)
        items1.append(item)
    imports.append(
        {
            "id": "imp-1",
            "kind": "archive",
            "workType": "vkr",
            "fileName": "ВКР_2026_кафедра_ИС.zip",
            "metaTableName": "сведения_ВКР_2026_ИС.xlsx",
            "createdAt": t1,
            "items": items1,
        }
    )

    t2 = "2026-01-20T08:40:00Z"
    pub_keys = [
        "title",
        "authors",
        "kind",
        "venue",
        "year",
        "volume",
        "issue",
        "pages",
        "doi",
        "issn",
        "indexing",
        "department",
    ]
    rows2 = [
        {
            "authors": [ref(staff[5]), text("Иванов П. С.", "external"), text("Миронов К.", "external")],
            "d": 1,
            "kind": "journal_article",
        },
        {
            "authors": [text("N. V. Belova"), text("Zakharov I."), ref(staff[6], True)],
            "d": 1,
            "kind": "journal_article",
            "en": True,
        },
        {"authors": [ref(staff[7])], "d": 1, "kind": "journal_article", "error": "Не указан год"},
        {"authors": [ref(staff[7]), text("Шмидт К.", "external")], "d": 1, "kind": "proceedings_paper"},
        {"authors": [], "d": 1, "kind": "journal_article", "dup": True},
        {"authors": [ref(staff[8]), text("Белова Н.")], "d": 1, "kind": "journal_article"},
    ]
    items2 = []
    for i, row in enumerate(rows2):
        row_ref = f"Строка {i + 2}"
        w = publication(row["kind"], row["d"])
        if row.get("en"):
            j = next(x for x in D.JOURNALS if x.en and 1 in x.depts)
            tt = en_title(D.DEPTS[1])
            w |= {
                "title": tt["title"],
                "venue": j.name,
                "indexing": j.indexing,
                "issn": j.issn,
                "language": "en",
                "annotation": ann_en(tt["m"], tt["t"]),
                "keywords": [tt["m"]["en"], tt["t"]["en"]],
            }
        if row.get("error"):
            items2.append({"ref": row_ref, "title": w["title"], "status": "error", "error": row["error"]})
            continue
        w |= draft("imp-2", t2, {k: {"source": "table"} for k in pub_keys})
        w |= {"annotation": None, "keywords": [], "year": 2025, "hasFile": False, "accessLevel": "card_only"}
        if row.get("dup"):
            orig = next(
                x
                for x in works
                if x["type"] == "publication"
                and x["status"] == "published"
                and x["kind"] == "journal_article"
                and x["department"] == D.DEPTS[1].name
            )
            w |= {
                "title": orig["title"],
                "authors": orig["authors"],
                "venue": orig["venue"],
                "year": orig["year"],
                "duplicateOf": orig["id"],
            }
            works.append(w)
            items2.append(
                {
                    "ref": row_ref,
                    "title": orig["title"],
                    "status": "duplicate",
                    "workId": w["id"],
                    "duplicateOf": orig["id"],
                }
            )
            continue
        w["authors"] = row["authors"]
        works.append(w)
        items2.append(
            {
                "ref": row_ref,
                "title": w["title"],
                "status": "filled",
                "workId": w["id"],
                "unmatchedAuthors": [a["displayName"] for a in row["authors"] if not a["personId"]],
            }
        )
    imports.append(
        {
            "id": "imp-2",
            "kind": "table",
            "workType": "publication",
            "fileName": "публикации_2025_кафедра_ПМ.xlsx",
            "createdAt": t2,
            "items": items2,
        }
    )

    t3 = "2026-06-15T12:00:00Z"
    items3 = []
    for k in range(4):
        if k == 1:
            items3.append(
                {"ref": "Курсовая_Жуков_ПИ.docx", "status": "error", "error": "Файл повреждён, текст не извлекается"}
            )
            continue
        cn += 1
        w = student_work("coursework", 2, 2026, cn)
        fm = all_ai()
        if k == 2:
            fm["title"] = {"source": "ai", "confidence": 0.58}
            fm["discipline"] = {"source": "ai", "confidence": 0.52}
        w |= draft("imp-3", t3, fm)
        item = {"ref": w["originalFileName"], "title": w["title"], "status": "filled", "workId": w["id"]}
        if k == 0:
            w["supervisors"] = [text("Сидоренко Е. А.")]
            item["unmatchedAuthors"] = ["Сидоренко Е. А."]
        if k == 3:
            orig = next(
                x
                for x in works
                if x["type"] == "coursework" and x["status"] == "published" and x["department"] == D.DEPTS[2].name
            )
            w |= {"title": orig["title"], "duplicateOf": orig["id"]}
            item |= {"status": "duplicate", "title": orig["title"], "duplicateOf": orig["id"]}
        works.append(w)
        items3.append(item)
    imports.append(
        {
            "id": "imp-3",
            "kind": "archive",
            "workType": "coursework",
            "fileName": "Курсовые_2026_ПИ.zip",
            "createdAt": t3,
            "items": items3,
        }
    )

    # Рубрики по кафедре, каждая третья курсовая — проект, цитирования растут с возрастом работы.
    current_year = datetime.now(UTC).year
    dept_index = {x.name: i for i, x in enumerate(D.DEPTS)}
    names = {(s, c): n for s, rows in D.CLASSIFIERS.items() for c, n in rows}
    for i, w in enumerate(works):
        grnti, oecd, wos, asjc = D.DEPT_SUBJECTS[dept_index.get(w.get("department") or "", 0)]
        pairs = (
            [("grnti", grnti), ("oecd", oecd), ("wos", wos), ("asjc", asjc)]
            if w["type"] == "publication"
            else [("grnti", grnti)]
        )
        w["subjects"] = [{"scheme": s, "code": c, "name": names[(s, c)]} for s, c in pairs]
        if w["type"] == "coursework" and i % 3 == 0:
            w["kind"] = "project"
        if w["type"] == "publication":
            w["citations"] = ((i * 7) % 11) * max(1, current_year - w["year"])
        if w["type"] == "talk" and i % 4 == 0:
            w["materials"] = [
                {
                    "name": "Презентация.pdf",
                    "size": 2_400_000,
                    "kind": "slides",
                    "status": "approved",
                    "uploadedAt": w["createdAt"],
                }
            ]
    orlov = next(
        (
            w
            for w in works
            if w["type"] == "publication"
            and w["status"] == "published"
            and any(a["personId"] == "p-s1" for a in w["authors"])
        ),
        None,
    )
    if orlov:
        orlov["materials"] = [
            {
                "name": "Авторская версия статьи.pdf",
                "size": 1_150_000,
                "kind": "text",
                "status": "pending",
                "uploadedAt": "2026-10-01T10:00:00Z",
            }
        ]
    return {"people": people, "works": works, "imports": imports}


# ---------- запись в базу ----------


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        if "T" in value
        else datetime.fromisoformat(value).replace(tzinfo=UTC)
    )


async def load_demo() -> None:
    storage = Storage()
    await storage.start()
    try:
        await storage.put(SAMPLE_KEY, await asyncio.to_thread(SAMPLE_PDF.read_bytes), "application/pdf")
    finally:
        await storage.close()
    data, ids = await write_demo()
    logger.info("Демо-данные загружены", extra={"people": len(data["people"]), "works": len(data["works"])})
    print(f"Готово: {len(data['people'])} человек, {len(data['works'])} работ. Входы, как в моках:")
    print("  python -m auth_service.users add moderator --name Модератор --role moderator")
    print(f"  python -m auth_service.users add orlov --name 'Орлов Д. С.' --role author --person {ids['p-s1']}")


async def write_demo() -> tuple[dict[str, list[dict]], dict[str, uuid.UUID]]:
    """Демо в базу; файлы работ ссылаются на один образец demo/sample.pdf. → (данные моков, id моков → uuid)."""
    data = build_demo()
    ids: dict[str, uuid.UUID] = {}
    new_id = lambda key: ids.setdefault(key, uuid.uuid7())  # noqa: E731
    works_repo = WorkRepository()
    async with uow_write_factory() as uow:
        if await uow.session.scalar(select(func.count()).select_from(Work)):
            sys.exit("В базе уже есть работы — демо-данные не загружаются")
        if not await uow.session.scalar(select(func.count()).select_from(Classifier)):
            sys.exit("Справочники пусты: сначала python -m admin_service.refdata init")
        for d in D.DEPTS:
            uow.session.add(Department(name=d.name))
        await uow.session.flush()
        for p in data["people"]:
            person = Person(
                id=new_id(p["id"]),
                kind=p["kind"],
                full_name=p["fullName"],
                department=p.get("department"),
                position=p.get("position"),
                academic_degree=p.get("academicDegree"),
                academic_title=p.get("academicTitle"),
                orcid=p.get("orcid"),
                affiliation=p.get("affiliation"),
            )
            for name in [p["fullName"], *p["aliases"]]:
                add_name(person, name)
            uow.session.add(person)
        for imp in data["imports"]:
            uow.session.add(
                Import(
                    id=new_id(imp["id"]),
                    kind=imp["kind"],
                    work_type=imp["workType"],
                    file_name=imp["fileName"],
                    meta_table_name=imp.get("metaTableName"),
                    status="done",
                    created_at=_dt(imp["createdAt"]),
                    created_by="Модератор",
                )
            )
        await uow.session.flush()
        links = []
        for m in data["works"]:
            body = {k: v for k, v in m.items() if k not in ("relatedTalkId", "relatedPublicationId", "materials")}
            for field in ("authors", "supervisors", "presenters"):
                if field in body:
                    body[field] = [
                        a | {"personId": str(ids[a["personId"]]) if a["personId"] else None} for a in body[field]
                    ]
            w = Work(
                id=new_id(m["id"]),
                type=m["type"],
                status=m["status"],
                details={},
                keywords=[],
                field_meta=m["fieldMeta"],
                file_key=SAMPLE_KEY if m.get("hasFile") else None,
                original_file_name=m.get("originalFileName"),
                import_id=ids[m["importId"]] if m.get("importId") else None,
                reject_reason=m.get("rejectReason"),
                published_at=_dt(m.get("publishedAt")),
                created_at=_dt(m["createdAt"]),
                updated_at=_dt(m["updatedAt"]),
            )
            apply_write(w, parse_write(m["type"], body))
            uow.session.add(w)
            if m.get("citations"):
                w.citations.append(WorkCitation(source="crossref", count=m["citations"]))
            for mat in m.get("materials", []):
                uow.session.add(
                    Material(
                        work_id=w.id,
                        name=mat["name"],
                        size=mat["size"],
                        kind=mat["kind"],
                        status=mat["status"],
                        file_key=SAMPLE_KEY,
                        uploaded_at=_dt(mat["uploadedAt"]),
                    )
                )
            related = m.get("relatedTalkId") or m.get("relatedPublicationId")
            links.append((w, related, m.get("duplicateOf")))
        await uow.session.flush()
        for w, related, duplicate in links:
            w.related_id = ids[related] if related else None
            w.duplicate_of = ids[duplicate] if duplicate else None
        for imp in data["imports"]:
            for n, it in enumerate(imp["items"], start=1):
                uow.session.add(
                    ImportItem(
                        import_id=ids[imp["id"]],
                        n=n,
                        ref=it["ref"],
                        title=it.get("title"),
                        status=it["status"],
                        error=it.get("error"),
                        work_id=ids.get(it.get("workId", "")),
                        duplicate_of=ids.get(it.get("duplicateOf", "")),
                        unmatched_authors=it.get("unmatchedAuthors") or [],
                    )
                )
        await works_repo.refresh_search(uow, [w.id for w, _, _ in links])
    return data, ids


async def load_people(path: Path) -> None:
    """Сотрудники из таблицы кафедры; повторный запуск обновляет, а не дублирует."""
    rows = read_table(path.name, await asyncio.to_thread(path.read_bytes))
    header = [h.strip() for h in rows[0]] if rows else []
    people = PersonRepository()
    works = WorkRepository()
    created = updated = 0
    async with uow_write_factory() as uow:
        for row in rows[1:]:
            rec = {h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(header)}
            full = rec.get("ФИО", "")
            if not full:
                continue
            person = await people.match_exact(uow, full)
            if person is None:
                person = Person(kind="staff", full_name=full)
                uow.session.add(person)
                created += 1
            else:
                updated += 1
            await works.ensure_department(uow, rec.get("Кафедра") or None)
            person.department = rec.get("Кафедра") or person.department
            person.position = rec.get("Должность") or person.position
            person.academic_degree = rec.get("Степень") or person.academic_degree
            person.academic_title = rec.get("Звание") or person.academic_title
            person.orcid = rec.get("ORCID") or person.orcid
            add_name(person, full)
            add_name(person, short_name(full))
    print(f"Создано: {created}, обновлено: {updated}")


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(prog="python -m admin_service.seed")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo")
    sub.add_parser("people").add_argument("file", type=Path)
    args = parser.parse_args()
    asyncio.run(load_demo() if args.command == "demo" else load_people(args.file))


if __name__ == "__main__":
    main()
