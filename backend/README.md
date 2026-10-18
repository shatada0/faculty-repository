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
docker compose exec admin_service python -m admin_service.seed demo
```

`seed demo` напечатает две команды для входов как в моках (`moderator`, `orlov`); пароль читается из stdin:

```bash
docker compose exec -it auth_service python -m auth_service.users add moderator --name Модератор --role moderator
```

Сайт — `https://localhost`, файлы — `https://files.localhost`, Grafana — `https://stats.localhost` (логин `admin`, пароль в `secrets/grafana_admin`). Фронт в режиме разработки: `VITE_USE_MOCKS=false` и `VITE_API_PROXY=https://localhost` в `../.env.development.local`.

LLM: на машине с NVIDIA — `docker compose --profile gpu up -d` (vLLM). Без GPU — Ollama или llama.cpp на хосте: `LLM_URL=http://host.docker.internal:11434` и `LLM_MODEL` в `.env`.

## Команды на сервере

| Команда | Что делает |
| --- | --- |
| `python -m auth_service.users add\|role\|block\|password <логин>` | учётки; пароль из stdin, `role`/`block`/`password` гасят сессии |
| `python -m admin_service.refdata load journals\|classifiers\|directions <файл.csv>` | справочники upsert'ом; форматы — в докстринге модуля |
| `python -m admin_service.seed people staff.csv` | сотрудники кафедр: «ФИО», «Кафедра», «Должность», «Степень», «Звание», «ORCID» |
| `python -m admin_service.seed demo` | демо-данные моков; в базе с работами не запускается |
| `docker compose run --rm backup` / `… backup restore-check` | ночной бэкап и еженедельная проверка восстановления |

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
- Пользователей `seed demo` не создаёт: у роли `writer` нет записи в `users`, учётки — только через `auth_service.users`.
- Загрузки фото людей нет: во фронте нет такого вызова. `photoUrl` отдаётся, если `photo_key` заполнен.
- Демо-DOI `10.12345/…` из моков в Crossref не существуют: «Заполнить по DOI» на настоящем бэке работает с настоящими DOI.
- У `app-ro` кроме `s3:GetObject` есть `s3:ListBucket` на префиксы `works` и `people`: без листинга `mc mirror` в бэкапе получает Access Denied.
- Бэкап сам отправляет строки `backup ok` и `restore check ok` в Loki (`curl` в push API). Контейнер с `--rm` удаляется раньше, чем Alloy дочитывает его вывод, и алерты на отсутствие этих строк горели бы всегда.
- restic бэкапит `/work/app.dump` и `/work/files`, а не весь `/work`: иначе локальный репозиторий для dev (`/work/restic-local`) попадал бы в собственный снимок.
- nginx резолвит сервисы через `upstream … resolve` и DNS Docker (`resolver 127.0.0.11`). С именем прямо в `proxy_pass` адрес запоминается при старте, и после пересоздания сервиса деплоем запросы уходят на старый IP, то есть в чужой контейнер.
- `limit_req_status 429` и JSON-ответ `rate_limited` вместо стандартного 503 с HTML. Стандартный `access_log` образа nginx убран: каждый запрос писался бы второй, не-JSON строкой.
- Выгрузки работ принимают ещё `importId`: это фильтр «Всех работ», а выгрузка повторяет текущие фильтры.
- `STATS_ALLOW` для локального стенда — `172.16.0.0/12`: запрос с хоста приходит в nginx с адреса шлюза Docker, а не с 127.0.0.1.
- ВКР и курсовые загружаются в один шаг: PDF и zip вперемешку в одной загрузке, без выбора типа. Тип и у курсовой «работа или проект» воркер узнаёт по титульнику, уровень без слова на титульнике — по коду направления (NN.03 — бакалавриат, .04 — магистратура, .05 — специалитет). Автору, которого нет в базе, сервис сам заводит профиль выпускника (ВКР) или студента (курсовая), если ФИО дословно есть в тексте PDF; выдуманное моделью ФИО остаётся нераспознанным. Рубрики ставятся по справочнику направлений: колонка `subjects` в `directions.csv` (`grnti:50.41,oecd:1.02`), в `fixtures/` — черновик, сверить с кафедрами. Карточка без ошибок и замечаний (все поля ИИ уверены не ниже 0.7, руководитель найден, не дубль) публикуется без модератора с доступом «только карточка», в журнал пишется `audit`-строка «Опубликовано без модератора»; остальное ждёт в очереди проверки. Проверки публикации переехали из `admin_service/checks.py` в `shared/checks.py`: ими пользуется и импорт.
