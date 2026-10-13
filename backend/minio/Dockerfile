# Community-образы MinIO больше не публикуются: сервер и mc собираются из исходников по тегу релиза из .env.
# Один образ служит minio, minio-init и бэкапу.
FROM golang:1.25 AS build
ARG MINIO_REF
ARG MC_REF
RUN go install github.com/minio/minio@${MINIO_REF}
RUN go install github.com/minio/mc@${MC_REF}

FROM debian:stable-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /go/bin/minio /go/bin/mc /usr/local/bin/
