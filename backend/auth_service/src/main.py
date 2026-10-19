from auth_service.src.api import router
from shared.auth.session import _key
from shared.middleware import AuthMiddleware, OriginCheckMiddleware, create_app

_key("jwt_private")  # как и публичный в create_app: без ключа сервис не стартует
app = create_app("auth_service", [router], middlewares=(OriginCheckMiddleware, AuthMiddleware))
