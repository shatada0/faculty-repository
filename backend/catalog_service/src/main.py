from catalog_service.src.api import router
from shared.middleware import OriginCheckMiddleware, create_app
from shared.storage import storage_lifespan

# catalog только читает: пользователь ему не нужен. Origin — ради POST /client-errors: чужой сайт не пишет в наш лог.
app = create_app("catalog_service", [router], middlewares=(OriginCheckMiddleware,), lifespan=storage_lifespan)
