from admin_service.src.api.exports_router import router as exports_router
from admin_service.src.api.my_router import router as my_router
from admin_service.src.api.people_router import router as people_router
from admin_service.src.api.router import router

__all__ = ["exports_router", "my_router", "people_router", "router"]
