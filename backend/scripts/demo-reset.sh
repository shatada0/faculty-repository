#!/bin/sh
# Чистый стенд перед показом: база с нуля, демо-данные и входы moderator / orlov (дальше — как start.sh).
# Старая база не удаляется, а копируется в том <проект>_pgdata_<дата>. Вернуть её:
#   docker compose down
#   docker run --rm -v <копия>:/from:ro -v backend_pgdata:/to postgres:17 sh -c 'find /to -mindepth 1 -delete && cp -a /from/. /to/'
#   docker compose up -d
# Файлы в MinIO не трогаются: ключи у новых работ свои, старые файлы ни на что не мешают.
set -eu
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1   # Git Bash на Windows иначе превращает /from в C:/Program Files/Git/from
project=$(basename "$PWD")
copy="${project}_pgdata_$(date +%Y%m%d_%H%M%S)"

docker compose down
if docker volume inspect "${project}_pgdata" > /dev/null 2>&1; then
  docker volume create "$copy" > /dev/null
  docker run --rm -v "${project}_pgdata:/from:ro" -v "$copy:/to" postgres:17 cp -a /from/. /to/
  docker volume rm "${project}_pgdata" > /dev/null
  echo "Старая база — в томе $copy"
fi
sh ../start.sh   # пустая база: start.sh загрузит демо-данные и заведёт входы
