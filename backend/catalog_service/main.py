from catalog_service.api import router
from shared.http import create_app
from shared.storage import storage_lifespan

# catalog только читает: пользователь ему не нужен, мидлварь — одна RequestContextMiddleware.
app = create_app("catalog_service", [router], lifespan=storage_lifespan)
