from admin_service.src.api import exports_router, my_router, people_router, router
from shared.middleware import AuthMiddleware, OriginCheckMiddleware, create_app
from shared.storage import storage_lifespan

app = create_app(
    "admin_service",
    [router, people_router, exports_router, my_router],
    middlewares=(OriginCheckMiddleware, AuthMiddleware),
    lifespan=storage_lifespan,
)
