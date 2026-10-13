from admin_service.api import admin, my
from shared.auth import AuthMiddleware, OriginCheckMiddleware
from shared.http import create_app
from shared.storage import storage_lifespan

app = create_app(
    "admin_service", [admin, my], middlewares=(OriginCheckMiddleware, AuthMiddleware), lifespan=storage_lifespan
)
