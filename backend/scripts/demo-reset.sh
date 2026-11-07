#!/bin/sh
# Чистый стенд перед показом: база с нуля, демо-сид и входы moderator / orlov.
# Старая база не удаляется, а копируется в том <проект>_pgdata_<дата>. Вернуть её:
#   docker compose down
#   docker run --rm -v <копия>:/from:ro -v backend_pgdata:/to postgres:17 sh -c 'find /to -mindepth 1 -delete && cp -a /from/. /to/'
#   docker compose up -d
# Файлы в MinIO не трогаются: ключи у новых работ свои, старые файлы ни на что не мешают.
# Пароли входов — аргументами: sh scripts/demo-reset.sh [пароль модератора] [пароль Орлова]
set -eu
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1   # Git Bash на Windows иначе превращает /from в C:/Program Files/Git/from
project=$(basename "$PWD")
copy="${project}_pgdata_$(date +%Y%m%d_%H%M%S)"

docker compose down
docker volume create "$copy" > /dev/null
docker run --rm -v "${project}_pgdata:/from:ro" -v "$copy:/to" postgres:17 cp -a /from/. /to/
docker volume rm "${project}_pgdata" > /dev/null
echo "Старая база — в томе $copy"

docker compose up -d --wait
person=$(docker compose exec -T admin_service python -m admin_service.src.seed demo | sed -n 's/.*--person //p')
echo "${1:-moderator123}" | docker compose exec -T auth_service python -m auth_service.src.users add moderator --name Модератор --role moderator
echo "${2:-orlov12345}" | docker compose exec -T auth_service python -m auth_service.src.users add orlov --name "Орлов Д. С." --role author --person "$person"
echo "Готово: https://localhost, входы moderator и orlov"
