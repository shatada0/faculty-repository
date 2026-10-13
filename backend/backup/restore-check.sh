#!/bin/sh
# Подключается из backup.sh (оттуда report). Раз в неделю: последний снимок восстанавливается в одноразовый Postgres, число работ с файлом сверяется с числом PDF.
set -eu
export RESTIC_PASSWORD_FILE=/run/secrets/restic_password RESTIC_REPOSITORY="$(cat /run/secrets/restic_repository)"
tmp=$(mktemp -d)
trap 'pg_ctl -D "$tmp/pg" stop -m immediate > /dev/null 2>&1 || true; rm -rf "$tmp"' EXIT
restic restore latest --target "$tmp/restore"
initdb -D "$tmp/pg" -U app --auth=trust > /dev/null
pg_ctl -D "$tmp/pg" -o "-k $tmp -c listen_addresses=''" -w start > /dev/null
createdb -h "$tmp" -U app app
for role in auth writer catalog grafana_ro exporter backup; do psql -h "$tmp" -U app -qc "CREATE ROLE $role" app; done
pg_restore -h "$tmp" -U app -d app --no-owner "$tmp/restore/work/app.dump"
works=$(psql -h "$tmp" -U app -tAc "SELECT count(DISTINCT file_key) FROM works WHERE file_key LIKE 'works/%'" app)
files=$(find "$tmp/restore/work/files/works" -type f ! -path '*/materials/*' | wc -l)
if [ "$works" -gt "$files" ]; then
  report error "restore check failed" "\"works\": $works, \"files\": $files"
  exit 1
fi
report info "restore check ok" "\"works\": $works, \"files\": $files"
