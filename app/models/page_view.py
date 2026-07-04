"""
PageView model — first-party traffic analytics.

One row per page view. Human views arrive from the frontend beacon
(/api/track → POST /analytics/pageviews); AI/search crawler hits arrive
from the Next.js middleware (crawlers don't run JS, so they are logged
server-side and flagged with is_bot/bot_name).
"""
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PageView(Base):
    __tablename__ = "page_views"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    path: Mapped[str] = mapped_column(String(512), nullable=False)
    referrer: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    utm_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(128), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Anonymous first-party visitor cookie (not PII); session groups a visit
    visitor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Cognito sub when the viewer is signed in
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    device: Mapped[str | None] = mapped_column(String(16), nullable=True)  # mobile|tablet|desktop
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(128), nullable=True)  # predictium.ai vs 40pfrom3.com

    is_bot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    bot_name: Mapped[str | None] = mapped_column(String(64), nullable=True)  # GPTBot, ClaudeBot, ...
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_page_views_ts", "ts"),
        Index("idx_page_views_path_ts", "path", "ts"),
        Index("idx_page_views_bot_ts", "is_bot", "ts"),
    )
