"""Coupon and CouponRedemption SQLAlchemy models."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class Coupon(Base):
    """
    Beta coupon codes for invite-only access.
    
    Coupons are validated server-side only (never trust frontend).
    
    Attributes:
        code: Unique coupon code (primary key)
        description: Optional description of the coupon
        plan: Plan granted by the coupon - free, pro, or elite
        trial_days: Number of trial days granted
        max_uses: Maximum redemptions allowed, NULL for unlimited
        current_uses: Current number of redemptions
        is_active: Whether the coupon is active
        expires_at: When the coupon expires
        created_at: When the coupon was created
        created_by: User who created the coupon
    """

    __tablename__ = "coupons"

    code: Mapped[str] = mapped_column(
        String,
        primary_key=True,
    )
    description: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
    )
    plan: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="pro",
    )
    trial_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=14,
    )
    max_uses: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    current_uses: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )

    # Relationships
    redemptions: Mapped[List["CouponRedemption"]] = relationship(
        "CouponRedemption",
        back_populates="coupon",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Coupon {self.code} ({self.plan})>"

    @property
    def is_valid(self) -> bool:
        """Check if the coupon is currently valid for redemption."""
        if not self.is_active:
            return False
        if self.expires_at and datetime.now(self.expires_at.tzinfo) > self.expires_at:
            return False
        if self.max_uses and self.current_uses >= self.max_uses:
            return False
        return True


class CouponRedemption(Base):
    """
    Track which users have redeemed which coupons.
    
    Prevents double-redemption of same coupon by same user.
    
    Attributes:
        id: UUID primary key
        user_id: User who redeemed the coupon
        coupon_code: Code of the redeemed coupon
        redeemed_at: When the coupon was redeemed
    """

    __tablename__ = "coupon_redemptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    coupon_code: Mapped[str] = mapped_column(
        String,
        ForeignKey("coupons.code", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="coupon_redemptions",
    )
    coupon: Mapped["Coupon"] = relationship(
        "Coupon",
        back_populates="redemptions",
    )

    def __repr__(self) -> str:
        return f"<CouponRedemption {self.coupon_code} by {self.user_id}>"
