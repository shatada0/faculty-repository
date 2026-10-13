# Схема и пустые справочники: alembic upgrade head, затем refdata init. Запускается раньше сервисов.
ARG BASE_IMAGE=project-base
FROM ${BASE_IMAGE}
COPY shared shared
COPY admin_service admin_service
COPY migrations migrations
COPY fixtures fixtures
COPY alembic.ini .
CMD ["sh", "-c", "alembic upgrade head && python -m admin_service.refdata init"]
