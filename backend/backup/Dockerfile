# pg_dump из того же postgres:17, что и сервер: версия клиента не ниже сервера. mc — из образа MinIO, restic и curl — из Debian.
ARG MINIO_IMAGE=local/minio:dev
FROM ${MINIO_IMAGE} AS minio
FROM postgres:17
RUN apt-get update && apt-get install -y --no-install-recommends restic curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=minio /usr/local/bin/mc /usr/local/bin/mc
COPY backup.sh restore-check.sh /usr/local/bin/
RUN mkdir /work && chown postgres /work
# initdb в проверке восстановления не работает от root
USER postgres
ENTRYPOINT ["sh", "/usr/local/bin/backup.sh"]
