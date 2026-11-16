#!/bin/sh
# Запуск стенда на macOS и Linux: нужен Docker, для ИИ — Ollama. На Windows — start.cmd.
# Первый запуск собирает образы и загружает демо-данные (10–30 минут), следующие — около минуты; данные не стираются.
set -eu
cd "$(dirname "$0")/backend"
docker info > /dev/null 2>&1 || { echo "Docker не запущен: откройте Docker и запустите ещё раз."; exit 1; }
[ -f .env ] || cp .env.example .env
if [ ! -f secrets/jwt_private.pem ] || [ ! -f certs/fullchain.pem ]; then
  # Postgres, RabbitMQ и Grafana запоминают пароли при первом запуске: данные прошлой копии проекта с новыми не откроются
  if [ -n "$(docker compose volumes -q)" ]; then
    printf '\nНа компьютере есть данные стенда от прошлой копии проекта, с новыми паролями они не откроются.\nУдалить их и начать с демо-данных? (да/нет) '
    read -r answer
    case "$answer" in да*|Да*|ДА*|y*|Y*) docker compose down -v ;;
      *) echo "Ничего не удалено: запускайте стенд из папки прошлой копии проекта."; exit 1 ;; esac
  fi
  sh scripts/dev-secrets.sh
  chmod -R a+rX secrets certs   # сервисы в контейнерах читают секреты под своими пользователями (app, postgres, grafana)
fi
docker build -f Dockerfile.base -t project-base .   # с выводом: первая сборка идёт минуты, без него кажется зависшей
docker compose up -d --build --wait
# Grafana берёт пароль из секрета только при создании своей базы: на старом томе остаётся прежний
docker compose exec -T grafana sh -c 'grafana cli admin reset-admin-password "$(cat /run/secrets/grafana_admin)"' > /dev/null || echo 'Пароль Grafana не обновлён: остался прежний.'

if [ "$(docker compose exec -T postgres psql -U app -d app -Atc 'select count(*) from works')" = 0 ]; then
  person=$(docker compose exec -T admin_service python -m admin_service.src.seed demo | sed -n 's/.*--person //p')
  echo moderator123 | docker compose exec -T auth_service python -m auth_service.src.users add moderator --name Модератор --role moderator
  echo orlov12345 | docker compose exec -T auth_service python -m auth_service.src.users add orlov --name "Орлов Д. С." --role author --person "$person"
fi

# ИИ — Ollama на этом компьютере (на Linux ему нужен OLLAMA_HOST=0.0.0.0); модель из .env скачивается один раз
model=$(sed -n 's/^LLM_MODEL=//p' .env)
if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
  curl -sf http://localhost:11434/api/tags | grep -q "\"$model\"" \
    || curl -sf http://localhost:11434/api/pull -d "{\"model\":\"$model\",\"stream\":false}" > /dev/null
  ai="работает ($model)"
else
  ai="НЕ НАЙДЕН. Установите Ollama (https://ollama.com) и запустите ещё раз: без него карточки ВКР не заполнятся."
fi

opener=$(command -v xdg-open || command -v open || true)
if [ -n "$opener" ]; then
  "$opener" https://files.localhost/ > /dev/null 2>&1 || true
  "$opener" https://localhost/ > /dev/null 2>&1 || true
fi
cat << EOF

Готово. Сайт: https://localhost
  модератор: moderator / moderator123
  автор:     orlov / orlov12345
  ИИ:        $ai
  Grafana:   https://stats.localhost (admin / $(cat secrets/grafana_admin))

Браузер предупредит о сертификате: стенд локальный, сертификат самоподписанный.
Нажмите «Дополнительно» → «Перейти на сайт» в обеих открывшихся вкладках.
Что показывать — README, раздел «Показ на кафедре». Остановить — docker compose -f backend/compose.yml stop.
EOF
