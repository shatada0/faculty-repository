from auth_service.src.api import router
from shared.middleware import AuthMiddleware, OriginCheckMiddleware, create_app

app = create_app("auth_service", [router], middlewares=(OriginCheckMiddleware, AuthMiddleware))
