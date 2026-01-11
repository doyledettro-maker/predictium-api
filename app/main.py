"""
Predictium API - FastAPI Application Entry Point

NBA predictions platform backend with:
- Cognito JWT authentication
- Stripe subscription management
- S3-based prediction storage
- PostgreSQL user/subscription database
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import (
    admin_router,
    auth_router,
    billing_router,
    health_router,
    meta_router,
    predictions_router,
    webhooks_router,
)
from app.services.cognito import cognito_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.
    
    Handles startup and shutdown tasks:
    - Startup: Log configuration, verify connections
    - Shutdown: Clean up resources
    """
    settings = get_settings()
    
    # Startup
    logger.info(f"Starting Predictium API ({settings.app_env})")
    logger.info(f"CORS origins: {settings.cors_origins}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Predictium API")
    await cognito_service.close()


# Create FastAPI app
settings = get_settings()

app = FastAPI(
    title="Predictium API",
    description="NBA predictions platform backend",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health_router)
app.include_router(meta_router)
app.include_router(auth_router)
app.include_router(predictions_router)
app.include_router(billing_router)
app.include_router(webhooks_router)
app.include_router(admin_router)  # TEMPORARY - Remove after updating beta users


@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "name": "Predictium API",
        "version": "1.0.0",
        "status": "operational",
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=not settings.is_production,
    )
