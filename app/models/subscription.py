"""Subscription SQLAlchemy model."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, FrozenSet, Optional

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db.database import Base
from app.billing_catalog import PRODUCT_PRO, plan_products

if TYPE_CHECKING:
    from app.models.user import User


class Subscription(Base):
    """
    Subscription model synced with Stripe.
    
    One subscription per user (for now).
    
    Attributes:
        id: UUID primary key
        user_id: Foreign key to users table
        stripe_customer_id: Stripe customer ID
        stripe_subscription_id: Stripe subscription ID
        plan: Subscription plan - free or premium
        status: Subscription status - trialing, active, past_due, canceled, expired
        trial_ends_at: When the trial period ends
        current_period_end: When the current billing period ends
        created_at: When the subscription was created
        updated_at: When the subscription was last updated
    """

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
        index=True,
    )
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
    )
    plan: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="free",
    )
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="trialing",
        index=True,
    )
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    current_period_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="subscription",
    )

    def __repr__(self) -> str:
        return f"<Subscription {self.plan} ({self.status})>"

    def is_active_at(self, now: Optional[datetime] = None) -> bool:
        """Whether the subscription grants access at `now`.

        A status alone is not enough: a trial ends at `trial_ends_at` (coupon
        trials used to run forever), and a Stripe subscription ends at
        `current_period_end` plus a grace window, so a late renewal webhook
        does not lock out a paying user while a lapsed one stops reading as
        active. A row with no Stripe subscription and no period end (the beta
        auto-grant) keeps its status as written.
        """
        now = now or datetime.now(timezone.utc)
        if self.status == "trialing":
            return self.trial_ends_at is None or _aware(self.trial_ends_at) > now
        if self.status != "active":
            return False
        if self.current_period_end is None:
            return True
        grace = timedelta(hours=get_settings().subscription_grace_hours)
        return _aware(self.current_period_end) + grace > now

    @property
    def is_active(self) -> bool:
        """Check if the subscription is currently active."""
        return self.is_active_at()

    @property
    def entitlements(self) -> FrozenSet[str]:
        """The paid products this subscription unlocks right now."""
        return plan_products(self.plan) if self.is_active else frozenset()

    @property
    def has_premium_access(self) -> bool:
        """Check if user has Premium access (any pro plan or legacy premium)."""
        return self.is_active and (
            self.plan in ("premium", "pro_weekly", "pro_monthly", "pro_season")
            or PRODUCT_PRO in plan_products(self.plan)
        )

    @property
    def has_data_api_access(self) -> bool:
        """Check if user has Data API access."""
        return self.is_active and self.plan in ("data_api",)


def _aware(dt: datetime) -> datetime:
    """Read a naive timestamp as UTC (SQLite in tests drops the zone)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

