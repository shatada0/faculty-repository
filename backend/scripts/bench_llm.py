"""Бенчмарк локальных LLM на извлечении карточек ВКР: 30 сгенерированных PDF с известными полями.

    python scripts/bench_llm.py generate            # bench/truth.json, bench/pdf/*.pdf (нужны стенд и Edge)
    python scripts/bench_llm.py run qwen3.5:4b [--no-think]   # bench/results/<модель>.json
    python scripts/bench_llm.py report              # bench/summary.md

run вызывает тот же extract(), что воркер; LLM_URL по умолчанию http://localhost:11434, таймаут 300 с.
--no-think добавляет в запрос reasoning_effort=none: Ollama игнорирует chat_template_kwargs воркера.
--require-all помечает в JSON-схеме все поля обязательными: у ExtractedCard required нет, и грамматика Ollama
разрешает модели закончить JSON после первого поля (GigaChat так и делает).
Запускать из backend/: python -m extraction_worker... ищется от текущей папки.
"""

# ruff: noqa: S603, S607, S311, E501, RUF005 — dev-скрипт: docker, ollama и Edge из PATH, сид генератора, длинные строки шаблона

import argparse
import asyncio
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "bench"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
FIELDS = (
    "title", "authors", "supervisors", "department", "profile", "annotation",
    "year", "directionCode", "pages", "keywords", "level",
)  # fmt: skip
AI_FIELDS = ("title", "authors", "supervisors", "department", "profile", "annotation")

# --- generate -------------------------------------------------------------------------------------------------------

TOPICS = [
    ("Разработка системы мониторинга серверной инфраструктуры на основе Prometheus",
     "процесс мониторинга серверной инфраструктуры", "сократить время обнаружения отказов серверов",
     ["мониторинг", "prometheus", "метрики", "оповещения"]),
    ("Модель прогнозирования оттока клиентов телекоммуникационной компании",
     "поведение абонентов телекоммуникационной компании", "построить модель, предсказывающую уход клиента",
     ["машинное обучение", "отток клиентов", "градиентный бустинг", "классификация"]),
    ("Веб-приложение для учёта посещаемости занятий студентами",
     "процесс учёта посещаемости учебных занятий", "автоматизировать отметку присутствия студентов",
     ["веб-приложение", "посещаемость", "учебный процесс", "qr-код"]),
    ("Численное решение задачи теплопроводности методом конечных элементов",
     "нестационарная задача теплопроводности", "реализовать и исследовать метод конечных элементов для этой задачи",
     ["метод конечных элементов", "теплопроводность", "численные методы", "сходимость"]),
    ("Система распознавания автомобильных номеров на видеопотоке",
     "видеопоток с камер контрольно-пропускного пункта", "распознавать номера автомобилей в реальном времени",
     ["компьютерное зрение", "нейронные сети", "распознавание номеров", "видеопоток"]),
    ("Информационная система управления складом малого предприятия",
     "складской учёт малого торгового предприятия", "снизить ошибки инвентаризации",
     ["складской учёт", "информационная система", "инвентаризация", "postgresql"]),
    ("Анализ тональности отзывов покупателей на русском языке",
     "отзывы покупателей интернет-магазинов", "классифицировать тональность отзывов",
     ["анализ тональности", "обработка естественного языка", "трансформеры", "отзывы"]),
    ("Мобильное приложение для планирования личных финансов",
     "процесс планирования личного бюджета", "помочь пользователю контролировать расходы",
     ["мобильное приложение", "личные финансы", "kotlin", "бюджет"]),
    ("Исследование устойчивости разностных схем для уравнения переноса",
     "разностные схемы для линейного уравнения переноса", "сравнить условия устойчивости явных и неявных схем",
     ["разностные схемы", "устойчивость", "уравнение переноса", "численный эксперимент"]),
    ("Защита веб-приложений от атак межсайтового скриптинга",
     "уязвимости веб-приложений к межсайтовому скриптингу", "разработать средство автоматического поиска уязвимостей",
     ["информационная безопасность", "xss", "веб-приложения", "статический анализ"]),
    ("Чат-бот технической поддержки на основе языковой модели",
     "обращения пользователей в службу технической поддержки", "снизить нагрузку на операторов первой линии",
     ["чат-бот", "языковая модель", "техническая поддержка", "rag"]),
    ("Оптимизация маршрутов доставки с помощью генетического алгоритма",
     "задача маршрутизации транспорта с временными окнами", "сократить суммарный пробег курьеров",
     ["генетический алгоритм", "маршрутизация", "логистика", "оптимизация"]),
    ("Микросервисная платформа онлайн-бронирования переговорных комнат",
     "процесс бронирования переговорных комнат в офисе", "исключить пересечения бронирований",
     ["микросервисы", "бронирование", "docker", "rest api"]),
    ("Система обнаружения вторжений на основе анализа сетевого трафика",
     "сетевой трафик корпоративной сети", "выявлять аномальную сетевую активность",
     ["обнаружение вторжений", "сетевой трафик", "аномалии", "машинное обучение"]),
    ("Математическая модель распространения эпидемии с учётом вакцинации",
     "динамика распространения инфекционного заболевания", "оценить влияние вакцинации на пик заболеваемости",
     ["математическое моделирование", "seir", "вакцинация", "дифференциальные уравнения"]),
    ("Автоматизация тестирования пользовательского интерфейса веб-приложений",
     "процесс регрессионного тестирования интерфейса", "сократить время ручного тестирования",
     ["автоматизация тестирования", "selenium", "регрессионное тестирование", "интерфейс"]),
    ("Рекомендательная система учебных курсов для студентов",
     "выбор студентами дисциплин по выбору", "подбирать курсы с учётом успеваемости и интересов",
     ["рекомендательная система", "коллаборативная фильтрация", "образование", "персонализация"]),
    ("Электронный журнал успеваемости для колледжа",
     "ведение журнала успеваемости в колледже", "перевести журнал успеваемости в электронный вид",
     ["электронный журнал", "успеваемость", "django", "колледж"]),
    ("Сравнение алгоритмов сжатия изображений без потерь",
     "алгоритмы сжатия растровых изображений", "сравнить степень сжатия и скорость алгоритмов",
     ["сжатие изображений", "алгоритмы", "энтропийное кодирование", "png"]),
    ("Криптографический протокол электронного голосования",
     "процедура тайного электронного голосования", "обеспечить анонимность и проверяемость голосов",
     ["криптография", "электронное голосование", "гомоморфное шифрование", "протокол"]),
    ("Цифровой двойник учебной аудитории для управления микроклиматом",
     "микроклимат учебной аудитории", "поддерживать комфортную температуру при меньшем расходе энергии",
     ["цифровой двойник", "интернет вещей", "микроклимат", "датчики"]),
    ("Сервис автоматического заполнения карточек научных работ",
     "процесс наполнения институционального репозитория", "сократить ручной ввод метаданных выпускных работ",
     ["языковая модель", "извлечение данных", "репозиторий", "метаданные"]),
    ("Методы кластеризации временных рядов потребления электроэнергии",
     "временные ряды потребления электроэнергии", "выделить типовые профили потребления",
     ["кластеризация", "временные ряды", "электроэнергия", "k-means"]),
    ("Разработка игрового движка для двумерных платформеров",
     "архитектура игровых движков", "создать движок с редактором уровней",
     ["игровой движок", "компьютерная графика", "c++", "редактор уровней"]),
    ("Информационная система записи пациентов к врачу",
     "процесс записи пациентов в поликлинике", "сократить очереди в регистратуре",
     ["медицинская информационная система", "запись к врачу", "расписание", "веб-сервис"]),
    ("Обнаружение мошеннических транзакций в платёжной системе",
     "транзакции платёжной системы", "выявлять мошеннические операции до их проведения",
     ["мошенничество", "транзакции", "случайный лес", "дисбаланс классов"]),
    ("Распределённое хранилище файлов с репликацией данных",
     "распределённые системы хранения файлов", "обеспечить сохранность данных при отказе узлов",
     ["распределённые системы", "репликация", "хранилище", "отказоустойчивость"]),
    ("Голосовой помощник для управления умным домом",
     "управление устройствами умного дома", "обеспечить управление голосом без облачных сервисов",
     ["голосовой помощник", "умный дом", "распознавание речи", "интернет вещей"]),
    ("Статистический анализ успеваемости студентов первого курса",
     "успеваемость студентов первого курса", "выявить факторы, влияющие на отчисление",
     ["статистический анализ", "успеваемость", "регрессия", "отчисление"]),
    ("Система контроля версий проектной документации",
     "проектная документация инженерного отдела", "отслеживать изменения документов и их согласование",
     ["контроль версий", "документооборот", "согласование", "git"]),
]  # fmt: skip
METHODS = [
    "В работе проведён анализ существующих решений и сформулированы требования.",
    "Рассмотрены существующие подходы и выбраны средства реализации.",
    "Выполнен обзор литературы и сравнение аналогов.",
]
RESULTS = [
    "Разработанное решение проверено на реальных данных и показало свою эффективность.",
    "Результаты работы внедрены в учебный процесс кафедры.",
    "Проведённые эксперименты подтвердили работоспособность предложенного подхода.",
    "Получен прототип, готовый к опытной эксплуатации.",
]
DIRECTIONS = {  # кафедра → коды из fixtures/directions.csv
    "Кафедра информационных систем": ["09.03.02", "09.04.02", "09.03.01", "10.05.01"],
    "Кафедра прикладной математики": ["01.03.02", "01.04.02"],
    "Кафедра программной инженерии": ["09.03.04", "09.03.01", "09.04.01", "10.05.01"],
}
PROFILES = {
    "bachelor": ["Разработка программно-информационных систем", "Информационные системы и технологии в бизнесе",
                 "Математическое моделирование", "Программное обеспечение вычислительных систем"],
    "master": ["Искусственный интеллект и анализ данных", "Инженерия программного обеспечения",
               "Математические методы анализа данных"],
    "specialist": ["Безопасность распределённых компьютерных систем", "Математические методы защиты информации"],
}  # fmt: skip
LEVEL_BY_CODE = {"03": "bachelor", "04": "master", "05": "specialist"}
LEVEL_WORD = {"bachelor": "БАКАЛАВРА", "master": "МАГИСТРА", "specialist": "СПЕЦИАЛИСТА"}
DEGREES = {
    "доктор технических наук": "д.т.н.",
    "кандидат технических наук": "к.т.н.",
    "доктор физико-математических наук": "д.ф.-м.н.",
    "кандидат физико-математических наук": "к.ф.-м.н.",
}
MALE = (
    [
        "Смирнов",
        "Васильев",
        "Петров",
        "Соловьёв",
        "Зайцев",
        "Павленко",
        "Лысенко",
        "Голубев",
        "Шестаков",
        "Ефимов",
        "Тарасов",
        "Белоусов",
    ],
    ["Александр", "Максим", "Никита", "Артём", "Георгий", "Лев", "Марк", "Фёдор", "Тимур", "Ярослав"],
    ["Алексеевич", "Викторович", "Николаевич", "Олегович", "Петрович", "Ильич", "Юрьевич", "Сергеевич"],
)
FEMALE = (["Смирнова", "Васильева", "Петрова", "Соловьёва", "Зайцева", "Голубева", "Шестакова", "Ефимова",
           "Тарасова", "Белоусова", "Нестеренко", "Ким"],
          ["Анна", "Софья", "Вероника", "Ульяна", "Ксения", "Арина", "Милана", "Евгения", "Таисия", "Олеся"],
          ["Алексеевна", "Викторовна", "Николаевна", "Олеговна", "Петровна", "Ильинична", "Юрьевна", "Сергеевна"])  # fmt: skip


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "app", "-d", "app", "-Atc", sql],
        cwd=ROOT, capture_output=True, check=True, encoding="utf-8",
    ).stdout  # fmt: skip
    return [line.split("|") for line in out.splitlines() if line]


def short(full: str) -> str:
    s, *rest = full.split()
    return s + " " + " ".join(r[0] + "." for r in rest)


def initials_first(full: str) -> str:
    s, *rest = full.split()
    return " ".join(r[0] + "." for r in rest) + " " + s


def supervisor_text(p: dict, style: int) -> str:
    deg = DEGREES.get(p["degree"], "")
    pos = p["position"]
    return [
        p["name"],
        short(p["name"]),
        ", ".join(x for x in (deg, pos) if x) + " " + p["name"],
        f"{pos} {initials_first(p['name'])}",
    ][style]


def generate() -> None:
    rnd = random.Random(2026)
    staff = [dict(zip(("name", "department", "degree", "position"), r, strict=True)) for r in psql(
        "select full_name, department, coalesce(academic_degree,''), coalesce(position,'') from people "
        "where kind='staff' and merged_into is null order by full_name"
    )]  # fmt: skip
    db_students = [r[0] for r in psql(
        "select full_name from people where kind in ('student','graduate') and merged_into is null order by full_name"
    )]  # fmt: skip
    all_names = {n.replace("ё", "е") for n in db_students} | {p["name"] for p in staff}
    students = rnd.sample(db_students, 10)
    while len(students) < 30:
        sur, first, patr = FEMALE if rnd.random() < 0.5 else MALE
        n = f"{rnd.choice(sur)} {rnd.choice(first)} {rnd.choice(patr)}"
        if n.replace("ё", "е") not in all_names and n not in students:
            students.append(n)
    rnd.shuffle(students)
    (BENCH / "pdf").mkdir(parents=True, exist_ok=True)
    (BENCH / "staff.json").write_text(json.dumps(staff, ensure_ascii=False, indent=1), encoding="utf-8")
    truth = []
    two_sup = {4, 17, 25}
    for i, ((title, obj, goal, kws), student) in enumerate(zip(TOPICS, students, strict=True)):
        sup = rnd.choice(staff)
        sups = [sup] + ([rnd.choice([p for p in staff if p is not sup])] if i in two_sup else [])
        dept = sup["department"]
        code = DIRECTIONS[dept][i % len(DIRECTIONS[dept])]
        level = LEVEL_BY_CODE[code[3:5]]
        direction = next(line.split(";")[1] for line in (ROOT / "fixtures/directions.csv").read_text("utf-8").splitlines()
                         if line.startswith(code))  # fmt: skip
        profile = rnd.choice(PROFILES[level]) if i % 3 else None
        year = 2019 + i % 8
        pages = rnd.randint(38, 140)
        annotation = (
            f"Объектом исследования является {obj}. Цель работы — {goal}. {rnd.choice(METHODS)} {rnd.choice(RESULTS)}"
        )
        female = student.split()[-1].endswith("на")
        # --- титульник
        q = i % 3
        shown_title = title
        if i % 4 == 1:  # перенос строки внутри темы
            words = title.split()
            cut = len(words) // 2
            shown_title = " ".join(words[:cut]) + "<br>" + " ".join(words[cut:])
        quoted = ["«{}»", '"{}"', "{}"][q].format(shown_title)
        dept_line = dept.upper() if i % 5 == 2 else dept
        dir_line = rnd.choice([f"Направление подготовки {code} {direction}", f"{code} «{direction}»",
                               f"Направление: {code} — {direction}"])  # fmt: skip
        prof_line = None
        if profile:
            label = {"bachelor": rnd.choice(["Профиль:", "Направленность (профиль):"]),
                     "master": "Магистерская программа:", "specialist": "Специализация:"}[level]  # fmt: skip
            prof_line = f"{label} {profile}"
        kind = "ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА" + (f" {LEVEL_WORD[level]}" if i % 2 == 0 else "")
        title_line = rnd.choice(["на тему: ", "Тема: ", ""]) + quoted
        author_style = i % 3
        who = ("Выполнила: студентка" if female else "Выполнил: студент") + f" группы {rnd.choice(['ИС', 'ПМ', 'ПИ', 'КБ'])}-{rnd.randint(41, 62)}"  # fmt: skip
        author_line = [f"{who} {student}", f"Обучающийся: {student}", f"Автор работы: {short(student)}"][author_style]
        style = i % 4
        if len(sups) == 2:
            sup_lines = [rnd.choice(["Научные руководители:", "Руководители:"]) + " "
                         + "; ".join(supervisor_text(p, style) for p in sups)]  # fmt: skip
        else:
            sup_lines = [rnd.choice(["Научный руководитель:", "Руководитель:", "Руководитель ВКР:"]) + " "
                         + supervisor_text(sup, style)]  # fmt: skip
        city = rnd.choice([f"Москва {year}", f"Москва — {year}", f"{year} г."])
        head = ["МИНИСТЕРСТВО НАУКИ И ВЫСШЕГО ОБРАЗОВАНИЯ РОССИЙСКОЙ ФЕДЕРАЦИИ", "Факультет информационных технологий"]
        meta_block = [dept_line, dir_line] + ([prof_line] if prof_line else [])
        people = [author_line, *sup_lines] if i % 2 else [*sup_lines, author_line]
        order = i % 3
        if order == 0:
            body = head + meta_block + [f"<b>{kind}</b>", title_line] + people
        elif order == 1:
            body = (
                head + [dept_line, f"<b>{kind}</b>", title_line, dir_line] + ([prof_line] if prof_line else []) + people
            )
        else:
            body = head + [f"<b>{kind}</b>", title_line] + people + meta_block
        body.append(city)
        # --- реферат
        stats = rnd.choice([
            f"Выпускная квалификационная работа содержит {pages} с., {rnd.randint(3, 30)} рис., {rnd.randint(1, 12)} табл., {rnd.randint(15, 60)} источников.",
            f"Объём работы — {pages} страниц, {rnd.randint(3, 30)} рисунков, {rnd.randint(15, 60)} источников.",
            f"Работа содержит {pages} с., {rnd.randint(1, 4)} прил.",
        ])  # fmt: skip
        caps = i % 4 == 3
        kw_line = ", ".join(k.upper() for k in kws) if caps else "Ключевые слова: " + ", ".join(kws) + "."
        abstract = [stats, kw_line, annotation] if i % 2 == 0 else [stats, annotation, kw_line]
        heading = "АННОТАЦИЯ" if i % 5 == 4 else "РЕФЕРАТ"
        html = (
            '<!doctype html><html lang="ru"><meta charset="utf-8"><style>'
            'body{font-family:"Times New Roman";font-size:14pt;margin:2cm}'
            ".c{text-align:center}.p{page-break-after:always;min-height:24cm}</style>"
            '<div class="p">' + "".join(f'<p class="c">{x}</p>' for x in body) + "</div>"
            f'<div><p class="c"><b>{heading}</b></p>' + "".join(f"<p>{x}</p>" for x in abstract) + "</div></html>"
        )
        name = f"vkr-{i + 1:02d}"
        html_path = BENCH / "pdf" / f"{name}.html"
        pdf_path = BENCH / "pdf" / f"{name}.pdf"
        html_path.write_text(html, encoding="utf-8")
        subprocess.run([EDGE, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf_path}", html_path.as_uri()], check=True, capture_output=True)  # fmt: skip
        truth.append({
            "file": f"{name}.pdf", "title": title, "authors": [student], "supervisors": [p["name"] for p in sups],
            "department": dept, "profile": profile, "annotation": annotation, "year": year, "directionCode": code,
            "pages": pages, "keywords": kws, "level": level, "studentFromDb": student in db_students,
        })  # fmt: skip
        print(name, "ok", flush=True)
    (BENCH / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")


# --- run ------------------------------------------------------------------------------------------------------------


def run(model: str, no_think: bool, require_all: bool) -> None:
    os.environ.setdefault("LLM_URL", "http://localhost:11434")
    os.environ["LLM_MODEL"] = model
    os.environ.setdefault("LLM_TIMEOUT_SECONDS", "300")
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    import httpx

    from extraction_worker.src.service.extract import extract

    class Client(httpx.AsyncClient):
        async def post(self, url, **kw):  # type: ignore[override]
            if no_think and isinstance(kw.get("json"), dict):
                kw["json"] = kw["json"] | {"reasoning_effort": "none"}
            if require_all and "response_format" in kw.get("json", {}):
                schema = kw["json"]["response_format"]["json_schema"]["schema"]
                schema["required"] = list(schema["properties"])
            return await super().post(url, **kw)

    async def main() -> list[dict]:
        async with Client() as http:
            t = time.perf_counter()
            warm = await http.post(
                f"{os.environ['LLM_URL']}/v1/chat/completions",
                json={"model": model, "messages": [{"role": "user", "content": "Привет"}], "max_tokens": 5}
                | ({"reasoning_effort": "none"} if no_think else {}),
                timeout=600,
            )
            print("прогрев", warm.status_code, round(time.perf_counter() - t, 1), "с", flush=True)
            out = []
            for pdf in sorted((BENCH / "pdf").glob("*.pdf")):
                t = time.perf_counter()
                row: dict = {"file": pdf.name}
                try:
                    row["fields"], row["meta"] = await extract(pdf.read_bytes(), http, {"file": pdf.name})
                except Exception as e:
                    row["error"] = f"{type(e).__name__}: {e}"
                row["seconds"] = round(time.perf_counter() - t, 2)
                print(pdf.name, row["seconds"], row.get("error", ""), flush=True)
                out.append(row)
            return out

    rows = asyncio.run(main())
    ps = subprocess.run(["ollama", "ps"], capture_output=True, encoding="utf-8").stdout
    smi = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader"], capture_output=True, encoding="utf-8"
    ).stdout.strip()
    (BENCH / "results").mkdir(parents=True, exist_ok=True)
    key = model + (" no-think" if no_think else "") + (" required" if require_all else "")
    path = BENCH / "results" / (re.sub(r"[^\w.-]+", "_", key) + ".json")
    path.write_text(
        json.dumps({"model": model, "noThink": no_think, "requireAll": require_all, "ollamaPs": ps, "gpuUsed": smi, "rows": rows},
                   ensure_ascii=False, indent=1), encoding="utf-8",
    )  # fmt: skip
    print(ps)


# --- report ---------------------------------------------------------------------------------------------------------


def norm(s: object) -> str:
    s = str(s or "").lower().replace("ё", "е")
    s = re.sub(r"[«»\"“”„']", "", s)
    return " ".join(s.split()).strip(" .")


def correct(field: str, want: object, got: object) -> bool:
    from shared.people.names import short_key

    if field in ("authors", "supervisors"):
        return bool(got) and sorted(short_key(x) for x in want) == sorted(short_key(str(x)) for x in got)
    if field == "keywords":
        return sorted(norm(x) for x in want) == sorted(norm(x) for x in got or [])
    if field == "annotation":
        words = re.findall(r"\w+", norm(got))
        ref = set(re.findall(r"\w+", norm(want)))
        return bool(words) and sum(w in ref for w in words) >= 0.8 * len(words)
    if field == "profile" and want is None:
        return not got
    return norm(want) == norm(got)


def report() -> None:
    sys.path.insert(0, str(ROOT))
    from shared.people.names import short_key

    truth = {t["file"]: t for t in json.loads((BENCH / "truth.json").read_text("utf-8"))}
    staff = {short_key(p["name"]) for p in json.loads((BENCH / "staff.json").read_text("utf-8"))}
    lines = ["# Сводка бенчмарка", ""]
    hard: dict[str, list[str]] = {f: [] for f in truth}
    summary = []
    for path in sorted((BENCH / "results").glob("*.json")):
        res = json.loads(path.read_text("utf-8"))
        name = (
            res["model"].removeprefix("hf.co/ai-sage/")
            + (" (think off)" if res["noThink"] else "")
            + (" (required)" if res.get("requireAll") else "")
        )
        rows = res["rows"]
        ok = dict.fromkeys(FIELDS, 0)
        all_ok = danger = caught = auto = json_fail = timeouts = other = 0
        danger_list = []
        for r in rows:
            t = truth[r["file"]]
            err = r.get("error", "")
            if err:
                json_fail += "неверный ответ" in err
                timeouts += "не ответил" in err
                other += "неверный ответ" not in err and "не ответил" not in err
                hard[r["file"]] += [f"{name}: {err}"]
                continue
            fields, meta = r["fields"], r["meta"]
            card_ok = True
            for f in FIELDS:
                good = correct(f, t[f], fields.get(f))
                ok[f] += good
                card_ok &= good
                if not good:
                    conf = meta.get(f, {}).get("confidence", 0)
                    hard[r["file"]].append(f"{name}: {f}={fields.get(f)!r} ({conf})")
                    if conf >= 0.85:
                        danger += 1
                        danger_list.append(f"{r['file']} {f}={fields.get(f)!r}")
                    elif conf < 0.7:
                        caught += 1
            all_ok += card_ok
            sups = fields.get("supervisors") or []
            auto += (
                bool(sups)
                and all(short_key(s) in staff for s in sups)
                and all(m.get("confidence", 0) >= 0.7 for m in meta.values())
            )
        secs = sorted(r["seconds"] for r in rows)
        p95 = secs[min(len(secs) - 1, int(0.95 * len(secs)))]
        ps_line = next((x for x in res["ollamaPs"].splitlines() if x.startswith(res["model"] + " ")), "")
        vram = re.search(r"(\d+(?:\.\d+)?\s*[GM]B)\s+(.+?)\s{2,}", ps_line)
        n = len(rows)
        summary.append({"name": name, "ok": ok, "allOk": all_ok, "danger": danger, "caught": caught, "auto": auto,
                        "json": json_fail, "timeouts": timeouts, "other": other, "mean": statistics.mean(secs),
                        "p95": p95, "vram": vram[1] if vram else "?", "proc": vram[2] if vram else "?", "n": n,
                        "dangerList": danger_list})  # fmt: skip
    head = "| Поле | " + " | ".join(s["name"] for s in summary) + " |"
    lines += [head, "|" + "---|" * (len(summary) + 1)]
    for f in FIELDS:
        lines.append(f"| {f} | " + " | ".join(f"{s['ok'][f]}/{s['n']}" for s in summary) + " |")
    for label, key in (("Все поля верны", "allOk"), ("Опасные ошибки (conf ≥ 0.85)", "danger"),
                       ("Пойманные ошибки (conf < 0.7)", "caught"), ("Прошли бы автопубликацию", "auto"),
                       ("Сбои JSON", "json"), ("Таймауты", "timeouts"), ("Прочие ошибки", "other")):  # fmt: skip
        lines.append(f"| {label} | " + " | ".join(str(s[key]) for s in summary) + " |")
    lines.append("| Среднее время, с | " + " | ".join(f"{s['mean']:.1f}" for s in summary) + " |")
    lines.append("| p95 времени, с | " + " | ".join(f"{s['p95']:.1f}" for s in summary) + " |")
    lines.append("| Память (ollama ps) | " + " | ".join(f"{s['vram']}, {s['proc']}" for s in summary) + " |")
    lines += ["", "## Опасные ошибки", ""]
    for s in summary:
        lines += [f"**{s['name']}**", ""] + [f"- {d}" for d in s["dangerList"]] + [""]
    lines += ["## Ошибки по файлам (больше всего — первые)", ""]
    for f, errs in sorted(hard.items(), key=lambda kv: -len(kv[1])):
        lines += [f"### {f} — {len(errs)}", ""] + [f"- {e}" for e in errs] + [""]
    (BENCH / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[: 6 + len(FIELDS) + 12]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate")
    r = sub.add_parser("run")
    r.add_argument("model")
    r.add_argument("--no-think", action="store_true")
    r.add_argument("--require-all", action="store_true")
    sub.add_parser("report")
    a = ap.parse_args()
    {"generate": generate, "report": report}.get(a.cmd, lambda: run(a.model, a.no_think, a.require_all))()
