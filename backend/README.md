# Репозиторий работ факультета — бэкенд

Пять сервисов на Python 3.15, FastAPI, pydantic и SQLAlchemy поверх одной PostgreSQL; события — RabbitMQ, файлы — MinIO, карточки ВКР заполняет локальная LLM. Спек — «Спек бэкенда» (в `../спек-бэкенда.md`), контракт с фронтом — README фронтенда, эталон поведения — моки `src/mocks`.

| Сервис | Пути | Что делает |
| --- | --- | --- |
| `auth_service` | `/api/auth/*` | вход, выход, `/auth/me`; единственный знает пароли и приватный ключ JWT |
| `catalog_service` | `/api/works*`, `/api/people*`, `/api/departments`, `/api/stats`, `/api/upload-limits`, `/api/client-errors` | публичный каталог, только чтение |
| `admin_service` | `/api/admin/*` кроме импорта, `/api/my/*` | редактор, проверки, публикация, bulk, DOI, люди, выгрузки, кабинет автора |
| `import_service` | `/api/admin/imports*`, `/api/admin/templates*` | таблицы синхронно, архивы — через шину |
| `extraction_worker` | без API | PDF → регулярки и LLM → поля с confidence |

## Запуск

Нужны Docker и `openssl` (для сертификата удобнее `mkcert`).

```bash
cp .env.example .env
sh scripts/dev-secrets.sh
docker build -f Dockerfile.base -t project-base .
docker compose up -d --build
docker compose exec admin_service python -m admin_service.src.seed demo
```

`seed demo` напечатает две команды для входов как в моках (`moderator`, `orlov`); пароль читается из stdin:

```bash
docker compose exec -it auth_service python -m auth_service.src.users add moderator --name Модератор --role moderator
```

Сайт — `https://localhost`, файлы — `https://files.localhost`, Grafana — `https://stats.localhost` (логин `admin`, пароль в `secrets/grafana_admin`). Фронт в режиме разработки: `VITE_USE_MOCKS=false` и `VITE_API_PROXY=https://localhost` в `../.env.development.local`.

LLM: на машине с NVIDIA — `docker compose --profile gpu up -d` (vLLM). Без GPU — Ollama или llama.cpp на хосте: `LLM_URL=http://host.docker.internal:11434` и `LLM_MODEL` в `.env`.

## Команды на сервере

| Команда | Что делает |
| --- | --- |
| `python -m auth_service.src.users add\|role\|block\|password <логин>` | учётки; пароль из stdin, `role`/`block`/`password` гасят сессии |
| `python -m admin_service.src.refdata load journals\|classifiers\|directions <файл.csv>` | справочники upsert'ом; форматы — в докстринге модуля |
| `python -m admin_service.src.seed people staff.csv` | сотрудники кафедр: «ФИО», «Кафедра», «Должность», «Степень», «Звание», «ORCID», «SPIN», «AuthorID» |
| `python -m admin_service.src.seed demo` | демо-данные моков; в базе с работами не запускается |
| `docker compose run --rm backup` / `… backup restore-check` | ночной бэкап и еженедельная проверка восстановления |
| `python -m admin_service.src.sync` | ночью таймером хоста: поля работ по Crossref (кроме правленных руками) и новые статьи людей с ORCID из ORCID и OpenAlex |
| `python -m admin_service.src.citations` | ночью таймером хоста: цитирования по DOI из Crossref и OpenAlex в `work_citations`, по ним — индекс Хирша |

## Раскладка

Как в aurora-assistant: модели и репозитории — в `shared`, в сервисе только `api/`, `service/`, `dependencies.py`, `main.py`. Направление зависимостей — роутер → сервис → репозиторий → модели, обратных импортов нет.

```
shared/
  auth/          models.py (User), session.py (JWT, SessionUser, require_*)
  works/         models.py (Work, WorkPerson, …), schemas.py (запись и work_dict), checks.py, labels.py
  people/        models.py (Person, PersonName), names.py (ключи ФИО)
  imports/       models.py (Import, ImportItem, ExtractionResult), tables.py (csv/xlsx)
  refdata/       models.py (кафедры, журналы, классификаторы, направления)
  db/            base.py (Base, one_of, created), registry.py (все модели в одной metadata)
  repository/    people_repository.py, works_repository.py
  uow/  config/  exceptions/  logging/  context/  middleware/ (create_app и мидлвари)  storage/  bus/
<сервис>/
  Dockerfile
  src/
    main.py          create_app и lifespan
    dependencies.py  сборка сервисов, псевдонимы Depends
    dto.py           тип на границе слоёв (только admin_service: PersonIn)
    api/             router.py (+ <раздел>_router.py), schemas.py
    service/         <имя>_service.py и его помощники
    <команда>.py     CLI: admin_service.src.refdata, .seed; auth_service.src.users
migrations/versions/ ГГГГ_ММ_ДД_NNNN-<ревизия>_<описание>.py
tests/               conftest.py (окружение и фикстура demo с PostgreSQL), fakes.py, test_<сервис>.py, test_shared.py
ruff.toml, pytest.ini
```

Сервисы с HTTP подключаются в uvicorn как `<сервис>.src.main:app`, воркер — так же (у него только `/healthcheck`).

## Разработка

```bash
uv venv --python 3.15 .venv && uv pip install -r requirements.txt -r requirements-dev.txt
ruff format . && ruff check . && pytest -q
```

Интеграционные тесты сами поднимают PostgreSQL 17 через testcontainers (нужен Docker). Живой стек проверяет `python scripts/smoke.py [--archive] [--grafana]` через nginx: каталог, выгрузки, кабинет автора, архив ВКР через LLM (`fixtures/vkr-title.pdf`, исходник — `vkr-title.html`) и запрос каждой панели Grafana. Тесты ходят в базу владельцем, поэтому права ролей, MinIO и nginx ловит только смоук.

Все ручки API — коллекция Bruno в [bruno/](bruno/): 102 запроса с проверками, от публичного каталога до прав ролей и ошибок. Папки идут по порядку и передают id друг другу, в конце коллекция отклоняет свои черновики. Запуск из корня репозитория — `npm run test:api` (Bruno CLI, отчёт в `bruno-report.html`); ту же папку открывает приложение Bruno, окружение `local`. Вход ограничен 5 попытками в минуту, а прогон входит 4 раза: между прогонами ждать минуту, иначе вход получит 429. Зависимости — `requirements.in`; `requirements.txt` с хэшами собирается `uv pip compile requirements.in --python-version 3.15 --python-platform x86_64-manylinux_2_28 --generate-hashes -o requirements.txt` (тот же формат, что у pip-compile).

## Отличия от спека

- Счётчик повторов LLM — свой заголовок `retries`, а не `x-death`: RabbitMQ 4 не доверяет x-заголовкам, которые клиент переопубликовал сам.
- У роли `writer` кроме чтения сессионных колонок `users` есть `SELECT (name)` (имя загрузившего материал) и `UPDATE (person_id)` (слияние людей переносит учётки).
- У `import_items` есть колонка `meta` — строка таблицы сведений к файлу архива; её значения ложатся поверх ответа ИИ.
- Пользователей `seed demo` не создаёт: у роли `writer` нет записи в `users`, учётки — только через `auth_service.src.users`.
- Загрузки фото людей нет: во фронте нет такого вызова. `photoUrl` отдаётся, если `photo_key` заполнен.
- Демо-DOI `10.12345/…` из моков в Crossref не существуют: «Заполнить по DOI» на настоящем бэке работает с настоящими DOI.
- У `app-ro` кроме `s3:GetObject` есть `s3:ListBucket` на префиксы `works` и `people`: без листинга `mc mirror` в бэкапе получает Access Denied.
- Бэкап сам отправляет строки `backup ok` и `restore check ok` в Loki (`curl` в push API). Контейнер с `--rm` удаляется раньше, чем Alloy дочитывает его вывод, и алерты на отсутствие этих строк горели бы всегда.
- restic бэкапит `/work/app.dump` и `/work/files`, а не весь `/work`: иначе локальный репозиторий для dev (`/work/restic-local`) попадал бы в собственный снимок.
- nginx резолвит сервисы через `upstream … resolve` и DNS Docker (`resolver 127.0.0.11`). С именем прямо в `proxy_pass` адрес запоминается при старте, и после пересоздания сервиса деплоем запросы уходят на старый IP, то есть в чужой контейнер.
- `limit_req_status 429` и JSON-ответ `rate_limited` вместо стандартного 503 с HTML. Стандартный `access_log` образа nginx убран: каждый запрос писался бы второй, не-JSON строкой.
- Выгрузки работ принимают ещё `importId`: это фильтр «Всех работ», а выгрузка повторяет текущие фильтры.
- `STATS_ALLOW` для локального стенда — `172.16.0.0/12`: запрос с хоста приходит в nginx с адреса шлюза Docker, а не с 127.0.0.1.
- ВКР и курсовые загружаются в один шаг: PDF и zip вперемешку в одной загрузке, без выбора типа. Тип и у курсовой «работа или проект» воркер узнаёт по титульнику, уровень без слова на титульнике — по коду направления (NN.03 — бакалавриат, .04 — магистратура, .05 — специалитет). Автору, которого нет в базе, сервис сам заводит профиль выпускника (ВКР) или студента (курсовая), если ФИО дословно есть в тексте PDF; выдуманное моделью ФИО остаётся нераспознанным. Рубрики ставятся по справочнику направлений: колонка `subjects` в `directions.csv` (`grnti:50.41,oecd:1.02`), в `fixtures/` — черновик, сверить с кафедрами. Карточка без ошибок и замечаний (все поля ИИ уверены не ниже 0.7, руководитель найден, не дубль) публикуется без модератора с доступом «только карточка», в журнал пишется `audit`-строка «Опубликовано без модератора»; остальное ждёт в очереди проверки. Проверки публикации переехали из `admin_service/checks.py` в `shared/works/checks.py`: ими пользуется и импорт.
- Поиск (`q`) — по началу слова, а не по целому слову: лексемы `websearch_to_tsquery` после стемминга становятся префиксами (`'нейр':*`), кавычки, `or` и минус работают как раньше. Опечатки — `pg_trgm` (миграция `20261019_0003`): триграммные GIN-индексы по названию работы и `person_names`, порог `word_similarity` снижен до 0.5 на уровне базы. Похожие добавляются, только если точных совпадений меньше пяти, и стоят ниже точных. Одна функция `matches()`/`relevance()` в `works_repository` — для каталога, фасетов и рабочего места; у людей то же в `name_search()`. Новая ручка `GET /api/works/suggest?q=` — подсказки строки поиска (до 6 работ с подсветкой и до 4 преподавателей).
- Раскладка файлов приведена к aurora-assistant (раздел «Раскладка»), поэтому пути из спека устарели: `shared/auth.py` → `shared/auth/session.py`, `shared/names.py` → `shared/people/names.py`, `shared/models.py` → `shared/<домен>/models.py`, `python -m admin_service.refdata` → `python -m admin_service.src.refdata` (и так же `seed`, `auth_service.src.users`), настройки ruff и pytest — в `ruff.toml` и `pytest.ini` вместо `pyproject.toml`. Ревизии миграций переименованы по дате (`20261013_0001`, `20261018_0002`); на стенде со старыми номерами — `UPDATE alembic_version SET version_num = '20261018_0002'` перед `docker compose up`.
- Кафедра — только из справочника `departments`. Редактор работы и форма человека с неизвестной кафедрой получают 400 `bad_department`. Импорт неизвестную кафедру (опечатку ИИ или таблицы) не заводит, а оставляет поле пустым для модератора. Новую кафедру заводит только `seed people` из CSV сотрудников. Во фронте кафедра выбирается из списка, а не вводится текстом.
- События `import_item.created` идут через transactional outbox (миграция `20261101_0007`, таблица `outbox`): они пишутся в одной транзакции с элементами импорта, а `OutboxRelay` в import_service публикует их после коммита и удаляет строку, когда RabbitMQ подтвердил приём (`SKIP LOCKED` — для нескольких экземпляров). Если шина недоступна, элементы остаются «в очереди» и уходят, когда она вернётся; раньше они сразу получали ошибку «Файл не отправлен на разбор». Доставка «хотя бы раз»: потребители идемпотентны. Воркер базы не имеет и по-прежнему подтверждает сообщение только после публикации результата.
- У журнала в справочнике два ISSN: печатный (`issn`) и электронный (`eissn`, миграция `20261101_0006`, необязательная колонка `eissn` в `journals.csv`). Списки журнала находятся по любому из них. Crossref отдаёт оба; при заполнении по DOI в работу пишется тот, который знает справочник, а если не знает ни одного — печатный.
- Скан без текстового слоя распознаётся OCR: PDFium (`pypdfium2`) рисует первые три страницы в 300 dpi, Tesseract (`rus+eng`, ставится в образ воркера) читает их отдельным процессом с таймаутом, стадия в логе — `ocr`. Уверенность полей со скана не выше 0.6: OCR путает буквы, поэтому такая карточка без модератора не публикуется. Без Tesseract (локальные тесты) скан, как раньше, получает ошибку «Скан без текстового слоя».
- Импорт из ORCID: `POST /api/admin/people/{id}/orcid-import` берёт DOI из `pub.orcid.org/v3.0/{orcid}/works` (только `relationship: self`), спрашивает Crossref пачками по 50 и заводит черновики публикаций; авторов сопоставляет как «Заполнить по DOI» и затем `link_known`. В nginx у этой ручки `proxy_read_timeout 300s`.
- Импорт работ без ручного ввода, кроме таблицы и архива:
  - пачка DOI текстом — `POST /api/admin/works/doi-import`, как импорт из ORCID (до 1000 DOI, в nginx `proxy_read_timeout 300s`);
  - BibTeX и RIS тем же импортом таблицы публикаций (`shared/imports/bibliography.py`, свой парсер): выгрузки Scopus и Web of Science получают отметку индексации. Так закрыт «импорт из Scopus и WoS»: их API платные и по ключу организации, а выгрузку в RIS/BibTeX умеют оба;
  - список текстом — `kind=text`: каждую запись (из eLIBRARY, отчёта, программы конференции) разбирает LLM в строку шаблона (`extract_record` в воркере, схема ответа со всеми полями обязательными), дальше — как строка таблицы. Черновики не публикуются сами; вид публикации и уровень конференции LLM берёт из подписей, их уверенность всегда 0.4. Миграция `20261101_0008`: `import_items.text`, вид импорта `text`;
  - ISBN — `POST /api/admin/works/{id}/isbn`: Crossref (книги с DOI), иначе Open Library — издание по `/isbn/{isbn}.json` (издательство, год, страницы этого издания) и авторы из поиска. Российские издания без DOI находятся редко: у Книжной палаты и РГБ открытого API нет.
- Импорт из ORCID добавляет DOI, которые OpenAlex приписал автору по тому же ORCID (у Оселедца: 117 в ORCID, 419 в OpenAlex). OpenAlex без API-ключа не даёт курсор — идём номерами страниц по 200. Владелец профиля привязывается к написанию в статье (фамилия по транслиту и инициалы), даже когда однофамилец с теми же инициалами есть.
- На 429 от Crossref, OpenAlex и Open Library `_get` ждёт `Retry-After` (не больше 30 с) и повторяет до трёх раз: пачки подряд упирались в лимит.
- Все вызовы внешних API идут через охранника `shared/resilience`: ограничитель частоты (Crossref 3, ORCID 8, OpenAlex 5, Open Library 1 запрос в секунду) и автомат — после 5 неудач подряд (сеть, таймаут, 5xx, 429) цепь размыкается на 60 с (LLM — на 30), запросы сразу получают `CircuitOpenError` (502 «временно недоступен»), потом одна проба. У LLM разомкнутая цепь — `LLMUnavailable`: сообщение уходит в очередь повторов. 4xx не считаются. Алерт Grafana «Внешний сервис недоступен» — на строку «Цепь разомкнута» в логах `admin_service` и `extraction_worker` за 15 минут. Состояние в памяти процесса: у каждой реплики и ночной команды своё. Джиттер на всех повторах: ожидание на 429 — `Retry-After` +0–30 %, пауза автомата +0–20 %, переотправка в очередь повторов RabbitMQ — после случайной паузы 0–½ TTL (у очереди TTL общий, и пачка, упавшая разом, иначе вернулась бы разом), опрос outbox ±20 %.
- Коды РИНЦ у человека: `people.spin` и `people.elibrary_id` (миграция `0008`), в форме и CSV сотрудников (колонки «SPIN», «AuthorID»), ссылка на страницу автора в eLIBRARY — по AuthorID. API eLIBRARY платный (лицензия организации от 30 000 ₽ в год, документация по договору, api@elibrary.ru), поэтому не подключён: с AuthorID он подключится без новых полей.
- Ночная команда `python -m admin_service.src.sync`: поля работ с DOI, пришедшие из Crossref и не правленные руками (источник `doi`), обновляются, пустые заполняются; у людей с ORCID новые статьи становятся черновиками. Неизменившаяся работа не сохраняется, `updated_at` не трогается.
- Списки трудов по ГОСТ в docx (открытый вопрос спека): `GET /api/people/{id}/works.docx` и `GET /api/departments/works.docx?department=` в catalog_service, необязательный `year`. Публикации и выступления, видимые в каталоге, по годам и названию; docx собирается стандартной библиотекой (`catalog_service/src/service/docx.py`), Times New Roman 14.
- Выдача каталога с `q` отдаёт `titleParts` — название с отмеченными совпадениями. Слова, найденные по триграммам, отмечаются в Python тем же порогом 0.5: `ts_headline` их не видит.
- Профиль ВКР берётся регуляркой по подписи на титульнике («Профиль», «Направленность (профиль)», «Магистерская программа», «Специализация»), а не моделью: без подписи LLM подставляла туда направление с уверенностью 0,85. В схему ответа LLM все поля внесены как обязательные (`required`), а в запрос добавлен `reasoning_effort: "none"`: Ollama не понимает `chat_template_kwargs`, и без этого Qwen3.5 тратила все токены на рассуждения. Модель на стенде — `qwen3.5:4b` (бенчмарк — `bench/REPORT.md`).
