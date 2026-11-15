# Запуск стенда на Windows: нужен Docker Desktop, для ИИ — Ollama. Вызывается из start.cmd в корне проекта.
# Первый запуск собирает образы и загружает демо-данные (10–30 минут), следующие — около минуты; данные не стираются.
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location (Split-Path $PSScriptRoot)   # backend

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`n$text" -ForegroundColor Red; exit 1 }
function Check($what) { if ($LASTEXITCODE) { Fail "Не получилось $what. Причина — в сообщениях выше; частые случаи — README, «Если не запускается»." } }

Step 'Проверяю Docker'
docker info *> $null
if ($LASTEXITCODE) { Fail 'Docker не запущен: откройте Docker Desktop, дождитесь «Engine running» и запустите start.cmd ещё раз.' }

if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not ((Test-Path secrets/jwt_private.pem) -and (Test-Path certs/fullchain.pem))) {
    # Postgres, RabbitMQ и Grafana запоминают пароли при первом запуске: данные прошлой копии проекта
    # с новыми паролями не откроются (миграции падают с «password authentication failed»)
    if (docker compose volumes -q) {
        $answer = Read-Host "`nНа компьютере есть данные стенда от прошлой копии проекта, с новыми паролями они не откроются.`nУдалить их и начать с демо-данных? (да/нет)"
        if ($answer -notmatch '^\s*(да|y)') { Fail 'Ничего не удалено: запускайте стенд из папки прошлой копии проекта.' }
        docker compose down -v
        Check 'удалить данные прошлой копии'
    }
    Step 'Создаю пароли сервисов и сертификат'
    # openssl и sh — в контейнере: на компьютере кроме Docker ничего не нужно. Файлы из контейнера получают права
    # root 600, а сервисы читают их под своими пользователями (app, postgres, grafana) — отсюда chmod
    docker run --rm -v "${PWD}:/w" -w /w alpine:3.20 sh -c 'apk add -q --no-cache openssl && sh scripts/dev-secrets.sh && chmod -R a+rX secrets certs'
    Check 'создать пароли и сертификат'
}

Step 'Собираю и запускаю сервисы (первый раз — 10–30 минут)'
docker build -f Dockerfile.base -t project-base .   # с выводом: первая сборка идёт минуты, без него кажется зависшей
Check 'собрать базовый образ'
docker compose up -d --build --wait
Check 'запустить сервисы'

$works = docker compose exec -T postgres psql -U app -d app -Atc 'select count(*) from works'
if ("$works".Trim() -eq '0') {
    Step 'Загружаю демо-данные'
    $seed = docker compose exec -T admin_service python -m admin_service.src.seed demo
    Check 'загрузить демо-данные'
    $person = ([regex]'--person (\S+)').Match("$seed").Groups[1].Value
    'moderator123' | docker compose exec -T auth_service python -m auth_service.src.users add moderator --name 'Модератор' --role moderator
    'orlov12345' | docker compose exec -T auth_service python -m auth_service.src.users add orlov --name 'Орлов Д. С.' --role author --person $person
}

# ИИ — Ollama на этом компьютере; модель берётся из .env и скачивается один раз
$settings = Get-Content .env
$llm = ($settings | Where-Object { $_ -like 'LLM_URL=*' }) -replace '^LLM_URL=', ''
$model = ($settings | Where-Object { $_ -like 'LLM_MODEL=*' }) -replace '^LLM_MODEL=', ''
$ai = "$model на $llm"
if ($llm -like '*:11434*') {
    try {
        $tags = Invoke-RestMethod http://localhost:11434/api/tags -TimeoutSec 5
        if (-not ($tags.models.name -contains $model)) {
            Step "Скачиваю модель ИИ $model (несколько гигабайт, один раз)"
            $body = @{ model = $model; stream = $false } | ConvertTo-Json
            Invoke-RestMethod http://localhost:11434/api/pull -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 7200 | Out-Null
        }
        $ai = "работает ($model)"
    } catch {
        $ai = 'НЕ НАЙДЕН. Установите Ollama (https://ollama.com) и запустите start.cmd ещё раз: без него карточки ВКР не заполнятся, остальное работает.'
    }
}

$grafana = (Get-Content secrets/grafana_admin -Raw).Trim()
Start-Process 'https://files.localhost/'
Start-Process 'https://localhost/'
Write-Host @"

Готово. Сайт: https://localhost
  модератор: moderator / moderator123
  автор:     orlov / orlov12345
  ИИ:        $ai
  Grafana:   https://stats.localhost (admin / $grafana)

Браузер предупредит о сертификате: стенд локальный, сертификат самоподписанный.
Нажмите «Дополнительно» → «Перейти на сайт» в обеих открывшихся вкладках.
Что показывать — README, раздел «Показ на кафедре».
Остановить — Docker Desktop → Containers → backend → Stop. Запустить снова — start.cmd.
"@ -ForegroundColor Green
