"""
Application configuration using Pydantic Settings.
All settings are loaded from environment variables.
"""

from datetime import datetime
from functools import lru_cache
from typing import List
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import field_validator, model_validator
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

    # Stripe
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_premium_price_id: str = ""  # backwards compat → maps to monthly
    stripe_weekly_price_id: str = ""
    stripe_monthly_price_id: str = ""
    stripe_season_price_id: str = ""
    stripe_data_api_price_id: str = ""
    # Paid launch catalog (2026-11-13, Doyle: Pro for every sport except
    # tennis, a standalone Tennis tier, and a Pro + Tennis bundle; monthly and
    # annual; no day or season pass). TEST-mode price ids until launch.
    stripe_pro_monthly_price_id: str = ""
    stripe_pro_annual_price_id: str = ""
    stripe_tennis_monthly_price_id: str = ""
    stripe_tennis_annual_price_id: str = ""
    stripe_bundle_monthly_price_id: str = ""
    stripe_bundle_annual_price_id: str = ""
    # FOUNDER50: percent_off 50, duration forever. Applied server-side at
    # checkout to every founder; empty disables it.
    stripe_founder_coupon_id: str = ""
    # A founder is any user created before this instant (ISO 8601, UTC).
    # Empty means paid launch has not happened, so every user is a founder.
    founder_cutoff_utc: str = ""
    # Where Stripe sends the buyer back. Checkout and portal URLs are built
    # here from this, never taken from the client.
    frontend_url: str = "https://www.predictium.ai"
    # BETA: every new user is created with plan "premium". Set False at paid
    # launch so new users start on "free".
    beta_auto_premium: bool = True
    # A paid subscription stays active this long past current_period_end, so
    # a late renewal webhook does not lock a paying user out.
    subscription_grace_hours: int = 72
    # A past_due subscription (Stripe is retrying the card) grants nothing by
    # default: a deliberate product call, flip to keep access during retries.
    past_due_grants_access: bool = False
    # True from paid launch. Startup then refuses a configuration that would
    # fail open: no founder cutoff (everyone a founder), no founder coupon
    # (founders get promo codes instead of 50%), or the beta auto-grant.
    paid_launch: bool = False

    @field_validator("founder_cutoff_utc")
    @classmethod
    def _founder_cutoff_is_iso(cls, v: str) -> str:
        """A malformed cutoff fails at startup, never inside a billing read."""
        raw = (v or "").strip()
        if raw:
            datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return raw

    @model_validator(mode="after")
    def _paid_launch_is_closed(self) -> "Settings":
        if self.paid_launch:
            missing = [
                name
                for name, ok in (
                    ("FOUNDER_CUTOFF_UTC", bool(self.founder_cutoff_utc)),
                    ("STRIPE_FOUNDER_COUPON_ID", bool(self.stripe_founder_coupon_id)),
                    ("BETA_AUTO_PREMIUM=false", not self.beta_auto_premium),
                )
                if not ok
            ]
            if missing:
                raise ValueError(f"PAID_LAUNCH requires: {', '.join(missing)}")
        return self

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
    def cognito_jwks_url(self) -> str:
        """Construct the Cognito JWKS URL."""
        return f"{self.cognito_issuer}/.well-known/jwks.json"

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
