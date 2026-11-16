"""AccountDeletion: the audit trail for DELETE /me.

It keeps only a SHA-256 hash of the Cognito sub and when the deletion
happened, nothing else about the person. The hash also stops a still-valid
token from silently re-creating a deleted user on another endpoint.
"""

import hashlib
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def hash_sub(sub: str) -> str:
    return hashlib.sha256(sub.encode("utf-8")).hexdigest()


class AccountDeletion(Base):
    __tablename__ = "account_deletions"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    sub_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
