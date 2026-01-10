"""Subscription SQLAlchemy model."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

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
        plan: Subscription plan - free, pro, or elite
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

    @property
    def is_active(self) -> bool:
        """Check if the subscription is currently active."""
        return self.status in ("trialing", "active")

    @property
    def has_pro_access(self) -> bool:
        """Check if user has Pro or Elite access."""
        return self.is_active and self.plan in ("pro", "elite")

    @property
    def has_elite_access(self) -> bool:
        """Check if user has Elite access."""
        return self.is_active and self.plan == "elite"
