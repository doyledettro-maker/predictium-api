"""API routers for Predictium."""

from app.routers.admin import router as admin_router
from app.routers.auth import router as auth_router
from app.routers.billing import router as billing_router
from app.routers.health import router as health_router
from app.routers.meta import router as meta_router
from app.routers.predictions import router as predictions_router
from app.routers.webhooks import router as webhooks_router

__all__ = [
    "admin_router",
    "auth_router",
    "billing_router",
    "health_router",
    "meta_router",
    "predictions_router",
    "webhooks_router",
]
