from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

import aio_pika
from fastapi import APIRouter, Depends, FastAPI, File, Form, Request, Response, UploadFile

from import_service.service import ImportService
from shared.auth import AuthMiddleware, OriginCheckMiddleware, SessionUser, require_moderator
from shared.bus.consumer import MessageBusConsumer
from shared.bus.publisher import MessageBusPublisher
from shared.config import rabbit_config
from shared.contracts import ImportTopic, ItemExtractedDTO
from shared.db.uow import uow_write_factory
from shared.exceptions import NotFoundError
from shared.http import create_app
from shared.logging import get_logger
from shared.repository.people import PersonRepository
from shared.repository.works import WorkRepository
from shared.storage import content_disposition, storage_lifespan
from shared.tables import TEMPLATES, template_csv

logger = get_logger(__name__)
router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_moderator)])

WorkType = Literal["vkr", "coursework", "publication", "talk"]
Moderator = Annotated[SessionUser, Depends(require_moderator)]


def build_service(app: FastAPI) -> ImportService:
    return ImportService(
        uow_write_factory, WorkRepository(), PersonRepository(), app.state.storage, app.state.publisher
    )


def get_service(request: Request) -> ImportService:
    return build_service(request.app)


Service = Annotated[ImportService, Depends(get_service)]


@router.post("/imports/preview")
async def preview(service: Service, workType: Annotated[WorkType, Form()], file: Annotated[UploadFile, File()]) -> dict:
    return await service.preview(workType, file)


@router.post("/imports", status_code=201)
async def create_import(
    service: Service,
    user: Moderator,
    kind: Annotated[Literal["archive", "table"], Form()],
    workType: Annotated[WorkType, Form()],
    file: Annotated[list[UploadFile], File()],  # архив: PDF и zip вперемешку, поле file повторяется
    metaTable: Annotated[UploadFile | None, File()] = None,
) -> dict:
    return await service.create(kind, workType, file, metaTable, user)


@router.get("/imports")
async def imports(service: Service) -> list[dict]:
    return await service.list()


@router.get("/imports/{import_id:uuid}")
async def import_detail(
    service: Service,
    import_id: UUID,
    status: Literal["pending", "extracted", "filled", "duplicate", "error"] | None = None,
) -> dict:
    return await service.get(import_id, status)


@router.get("/templates/{work_type}/columns")
async def template_columns(work_type: str) -> list[dict]:
    if work_type not in TEMPLATES:
        raise NotFoundError("Нет такого шаблона")
    return [{"name": name, "required": required} for name, required in TEMPLATES[work_type]]


@router.get("/templates/{work_type}")
async def template(work_type: str) -> Response:
    if work_type not in TEMPLATES:
        raise NotFoundError("Нет такого шаблона")
    return Response(
        template_csv(work_type),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition("attachment", f"шаблон-{work_type}.csv")},
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with storage_lifespan(app):
        try:
            connection = await aio_pika.connect_robust(rabbit_config.url)
        except Exception:
            logger.exception("Сервис не стартовал: нет RabbitMQ")
            raise
        async with connection:
            channel = await connection.channel()
            app.state.publisher = MessageBusPublisher(channel)
            await app.state.publisher.start()
            service = build_service(app)
            consumer = MessageBusConsumer(
                await connection.channel(),
                "import_service",
                {ImportTopic.ITEM_EXTRACTED: (ItemExtractedDTO, service.on_extracted)},
            )
            await consumer.start()
            yield


app = create_app("import_service", [router], middlewares=(OriginCheckMiddleware, AuthMiddleware), lifespan=lifespan)
