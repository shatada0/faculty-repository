#!/bin/sh
# Секреты для локального запуска: ключи JWT, пароли ролей базы, MinIO и RabbitMQ. Существующие файлы не трогает.
# Сертификат на localhost, files.localhost и stats.localhost — mkcert (браузеры сами шлют *.localhost на 127.0.0.1).
set -eu
cd "$(dirname "$0")/.."
mkdir -p secrets certs
umask 077
# без перевода строки: $(cat …) в shell срезает LF, но не CR от openssl под Windows
pw() { [ -f "secrets/$1" ] || openssl rand -hex 24 | tr -d '\r\n' > "secrets/$1"; }
[ -f secrets/jwt_private.pem ] || openssl genpkey -algorithm ed25519 -out secrets/jwt_private.pem
[ -f secrets/jwt_public.pem ] || openssl pkey -in secrets/jwt_private.pem -pubout -out secrets/jwt_public.pem
for name in db_owner db_auth db_writer db_catalog db_grafana db_exporter db_backup s3_rw s3_ro amqp grafana_admin restic_password; do
  pw "$name"
done
# репозиторий restic должен быть вне сервера: в проде — sftp:backup@host:/repo или s3:…
[ -f secrets/restic_repository ] || echo /work/restic-local > secrets/restic_repository
[ -f secrets/rabbitmq.env ] || printf 'RABBITMQ_DEFAULT_USER=app\nRABBITMQ_DEFAULT_PASS=%s\n' "$(cat secrets/amqp)" > secrets/rabbitmq.env
[ -f secrets/minio.env ] || printf 'MINIO_ROOT_USER=root\nMINIO_ROOT_PASSWORD=%s\n' "$(openssl rand -hex 24)" > secrets/minio.env
if [ ! -f certs/fullchain.pem ]; then
  if command -v mkcert > /dev/null; then
    mkcert -cert-file certs/fullchain.pem -key-file certs/privkey.pem localhost files.localhost stats.localhost
  else  # самоподписанный: браузер предупредит, mkcert -install доверяет сертификату
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj /CN=localhost -keyout certs/privkey.pem       -out certs/fullchain.pem -addext "subjectAltName=DNS:localhost,DNS:files.localhost,DNS:stats.localhost" 2> /dev/null
  fi
fi
echo "Секреты в secrets/, сертификат в certs/"
