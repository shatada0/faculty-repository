# локально — project-base, в CI — образ из registry
ARG BASE_IMAGE=project-base
FROM ${BASE_IMAGE}
COPY shared shared
COPY auth_service auth_service
CMD ["uvicorn", "auth_service.src.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--loop", "uvloop", "--http", "httptools"]
