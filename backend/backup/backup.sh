#!/bin/sh
# Ночной бэкап: база и файлы → зашифрованный репозиторий restic вне сервера. Любой упавший шаг останавливает скрипт.
set -eu

# Итог шлём в Loki сами: контейнер с --rm удаляется раньше, чем Alloy дочитает его вывод, и алерт горел бы всегда.
report() {  # report <level> <message> [ключи JSON без скобок]
  line="{\"level\": \"$1\", \"service\": \"backup\", \"message\": \"$2\"${3:+, $3}}"
  echo "$line"
  escaped=$(printf '%s' "$line" | sed 's/["\\]/\\&/g')
  printf '{"streams": [{"stream": {"service": "backup", "level": "%s"}, "values": [["%s", "%s"]]}]}' \
    "$1" "$(date +%s%N)" "$escaped" | curl -fsS -H 'Content-Type: application/json' --data-binary @- http://loki:3100/loki/api/v1/push
}

if [ "${1:-}" = "restore-check" ]; then
  . /usr/local/bin/restore-check.sh
  exit
fi
export PGPASSWORD="$(tr -d '\r\n' < /run/secrets/db_backup)" RESTIC_PASSWORD_FILE=/run/secrets/restic_password
export RESTIC_REPOSITORY="$(cat /run/secrets/restic_repository)"
mc alias set minio http://minio:9000 app-ro "$(tr -d '\r\n' < /run/secrets/s3_ro)" > /dev/null
pg_dump -Fc -h postgres -U backup app > /work/app.dump
for prefix in works people; do
  # Пустой префикс mc mirror считает ошибкой, а на свежей установке фото ещё нет. Сбой mc ls в присваивании остановит скрипт.
  listing=$(mc ls "minio/app/$prefix/")
  if [ -n "$listing" ]; then
    mc mirror --overwrite --remove "minio/app/$prefix" "/work/files/$prefix"
  else
    rm -rf "/work/files/$prefix" && mkdir -p "/work/files/$prefix"
  fi
done
restic snapshots > /dev/null 2>&1 || restic init
restic backup /work/app.dump /work/files --tag nightly   # не /work: локальный репозиторий для dev лежит там же
restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune
report info "backup ok"
