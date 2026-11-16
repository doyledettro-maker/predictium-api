"""API routers for Predictium."""

from app.routers.analytics import router as analytics_router
from app.routers.auth import router as auth_router
from app.routers.billing import router as billing_router
from app.routers.devices import router as devices_router
from app.routers.health import router as health_router
from app.routers.me import router as me_router
from app.routers.meta import router as meta_router
from app.routers.predictions import router as predictions_router
from app.routers.webhooks import router as webhooks_router

__all__ = [
    "analytics_router",
    "auth_router",
    "billing_router",
    "devices_router",
    "me_router",
    "health_router",
    "meta_router",
    "predictions_router",
    "webhooks_router",
]
