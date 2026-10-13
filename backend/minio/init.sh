#!/bin/sh
# Запускается при каждом up, поэтому всё идемпотентно. Root-ключ сервисам не выдаётся.
set -eu
mc alias set s3 http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb --ignore-existing s3/app
mc version enable s3/app
mc ilm import s3/app < /init/lifecycle.json            # imports/ и старые версии — 30 дней
mc anonymous set-json /init/people-read.json s3/app    # только чтение people/*, без листинга
for name in rw ro; do
  mc admin policy create s3 "app-$name" "/init/app-$name.json"
  mc admin user add s3 "app-$name" "$(tr -d '\r\n' < /run/secrets/s3_$name)"
  mc admin policy attach s3 "app-$name" --user "app-$name" || true   # уже прикреплена
done
