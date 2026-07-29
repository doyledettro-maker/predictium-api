"""
TwitterFollowerSnapshot model - daily @PredictiumAI follower history.

Snapshots are recorded by the Predictium X daily engagement run and surfaced
alongside first-party traffic analytics.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class TwitterFollowerSnapshot(Base):
    __tablename__ = "twitter_follower_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    account: Mapped[str] = mapped_column(String(32), nullable=False, default="PredictiumAI")
    followers: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_twitter_follower_snapshots_account_ts", "account", "observed_at"),
    )
