# локально — project-base, в CI — образ из registry
ARG BASE_IMAGE=project-base
FROM ${BASE_IMAGE}
COPY shared shared
COPY admin_service admin_service
# sample.pdf для seed demo
COPY fixtures fixtures
CMD ["uvicorn", "admin_service.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--loop", "uvloop", "--http", "httptools"]
