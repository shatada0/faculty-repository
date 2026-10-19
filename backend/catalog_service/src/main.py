from catalog_service.src.api import router
from shared.middleware import create_app
from shared.storage import storage_lifespan

# catalog только читает: пользователь ему не нужен, мидлварь — одна RequestContextMiddleware.
app = create_app("catalog_service", [router], lifespan=storage_lifespan)
