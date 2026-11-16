"""
Application configuration using Pydantic Settings.
All settings are loaded from environment variables.
"""

from functools import lru_cache
from typing import List
from urllib.parse import quote, urlsplit, urlunsplit

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
    database_password: str = ""

    # AWS Cognito
    cognito_user_pool_id: str
    cognito_client_id: str
    cognito_region: str = "us-east-1"
    # Comma-separated app clients whose tokens the API accepts (the website,
    # then the mobile app's "predictium-mobile"). Empty means the single
    # COGNITO_CLIENT_ID, exactly as before.
    cognito_client_ids: str = ""

    # Stripe
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_premium_price_id: str = ""  # backwards compat → maps to monthly
    stripe_weekly_price_id: str = ""
    stripe_monthly_price_id: str = ""
    stripe_season_price_id: str = ""
    stripe_data_api_price_id: str = ""

    # RevenueCat (mobile in-app purchases). DELETE /me uses the secret key to
    # delete the subscriber keyed on the Cognito sub. Empty skips that step.
    revenuecat_secret_key: str = ""

    # AWS S3
    s3_predictions_bucket: str = "predictium-predictions"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"

    # Analytics (first-party traffic tracking)
    analytics_ingest_key: str = ""  # shared secret; frontend server sends X-Analytics-Key
    report_email_to: str = ""       # comma-separated recipients for the daily traffic report
    report_email_from: str = ""     # SES-verified sender address
    report_hour_utc: int = 11       # daily report send hour (11 UTC ≈ 6-7am US Eastern)
    ses_region: str = ""            # defaults to aws_region when empty
    slack_webhook_url: str = ""     # Slack incoming webhook; posts the daily report to a channel

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
    def cognito_client_id_allowlist(self) -> frozenset:
        """The app clients whose tokens are accepted."""
        listed = [c.strip() for c in (self.cognito_client_ids or "").split(",") if c.strip()]
        return frozenset(listed or ([self.cognito_client_id] if self.cognito_client_id else []))

    @property
    def cognito_jwks_url(self) -> str:
        """Construct the Cognito JWKS URL."""
        return f"{self.cognito_issuer}/.well-known/jwks.json"

    def cors_origins_warning(self) -> str | None:
        """A startup warning when a non-development server would refuse the
        site's own browser calls: ALLOWED_ORIGINS unset, or every origin a
        localhost one. Logged only; behaviour is unchanged (2026-11-16: the
        production API ran on the localhost default and every call from
        www.predictium.ai failed its CORS preflight)."""
        if self.app_env.lower() == "development":
            return None
        origins = self.cors_origins
        local = all(
            (urlsplit(o).hostname or "") in {"localhost", "127.0.0.1"} for o in origins
        )
        if "allowed_origins" in self.model_fields_set and not local:
            return None
        why = "ALLOWED_ORIGINS is not set" if "allowed_origins" not in self.model_fields_set else "ALLOWED_ORIGINS lists only localhost origins"
        return (
            f"{why} (APP_ENV={self.app_env}); CORS allows only {origins}, so browser "
            "calls from the site will fail. Set ALLOWED_ORIGINS=https://www.predictium.ai,https://predictium.ai."
        )

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.app_env.lower() == "production"

    @property
    def effective_database_url(self) -> str:
        """Return the database URL, overlaying a managed secret password if present."""
        if not self.database_password:
            return self.database_url
        parsed = urlsplit(self.database_url)
        if parsed.username is None or parsed.hostname is None:
            return self.database_url
        username = quote(parsed.username, safe="")
        password = quote(self.database_password, safe="")
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = f"{username}:{password}@{host}"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunsplit(
            (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
        )


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.
    Uses lru_cache to ensure settings are only loaded once.
    """
    return Settings()
