"""User SQLAlchemy model."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.coupon import CouponRedemption
    from app.models.subscription import Subscription


class User(Base):
    """
    User model linked to Cognito authentication.
    
    Attributes:
        id: UUID primary key
        cognito_id: Cognito User Pool sub (subject) claim
        email: User email address
        role: Access level - admin, tester, or subscriber
        created_at: When the user was created
        updated_at: When the user was last updated
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    cognito_id: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(
        String,
        unique=True,
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="subscriber",
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
    subscription: Mapped["Subscription"] = relationship(
        "Subscription",
        back_populates="user",
        uselist=False,
        lazy="selectin",
    )
    coupon_redemptions: Mapped[List["CouponRedemption"]] = relationship(
        "CouponRedemption",
        back_populates="user",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"
