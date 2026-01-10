"""
Application configuration using Pydantic Settings.
All settings are loaded from environment variables.
"""

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/predictium"

    # AWS Cognito
    cognito_user_pool_id: str
    cognito_client_id: str
    cognito_region: str = "us-east-1"

    # Stripe
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_pro_price_id: str = ""
    stripe_elite_price_id: str = ""

    # AWS S3
    s3_predictions_bucket: str = "predictium-predictions"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"

    # CORS
    allowed_origins: str = "http://localhost:3000"

    # App
    app_env: str = "development"
    log_level: str = "INFO"

    @property
    def cors_origins(self) -> List[str]:
        """Parse comma-separated CORS origins into a list."""
        return [origin.strip() for origin in self.allowed_origins.split(",")]

    @property
    def cognito_issuer(self) -> str:
        """Construct the Cognito issuer URL."""
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"

    @property
    def cognito_jwks_url(self) -> str:
        """Construct the Cognito JWKS URL."""
        return f"{self.cognito_issuer}/.well-known/jwks.json"

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.app_env.lower() == "production"


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.
    Uses lru_cache to ensure settings are only loaded once.
    """
    return Settings()
