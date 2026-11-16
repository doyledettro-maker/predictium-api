"""PushDevice: one row per phone that has turned on alerts in the mobile app.

The Expo push token is a credential for sending to that phone. It is stored
here and travels only in the POST /devices body. It never goes in a URL, a
log line or an error message; callers refer to a row by its id.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PushDevice(Base):
    __tablename__ = "push_devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Unique on its own: a token that moves to another account (a shared
    # phone) is reassigned, so one phone never gets two accounts' alerts.
    expo_push_token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    app_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    def __repr__(self) -> str:  # never include the token
        return f"<PushDevice {self.id} ({self.platform})>"
