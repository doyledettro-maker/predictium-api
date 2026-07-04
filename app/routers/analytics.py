"""
Analytics router — first-party traffic ingestion and reporting.

Auth model: all endpoints are server-to-server, guarded by the shared
secret header `X-Analytics-Key` (ANALYTICS_INGEST_KEY). The Next.js app
holds the key server-side; browsers never see it.
"""
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.database import get_db
from app.models.page_view import PageView
from app.services import analytics_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["Analytics"])

MAX_BATCH = 50


def require_analytics_key(x_analytics_key: Optional[str] = Header(None)) -> None:
    settings = get_settings()
    if not settings.analytics_ingest_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analytics is not configured (ANALYTICS_INGEST_KEY unset)",
        )
    if x_analytics_key != settings.analytics_ingest_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid analytics key")


def _trunc(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value[:limit] if value else None


class PageViewEvent(BaseModel):
    path: str = Field(..., min_length=1, max_length=2048)
    referrer: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    visitor_id: Optional[str] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    device: Optional[str] = None
    country: Optional[str] = None
    domain: Optional[str] = None
    is_bot: bool = False
    bot_name: Optional[str] = None
    user_agent: Optional[str] = None


class IngestRequest(BaseModel):
    events: list[PageViewEvent] = Field(..., min_length=1, max_length=MAX_BATCH)


@router.post("/pageviews", status_code=status.HTTP_202_ACCEPTED)
async def ingest_pageviews(
    body: IngestRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_analytics_key),
):
    rows = [
        {
            "path": _trunc(e.path, 512) or "/",
            "referrer": _trunc(e.referrer, 1024),
            "utm_source": _trunc(e.utm_source, 128),
            "utm_medium": _trunc(e.utm_medium, 128),
            "utm_campaign": _trunc(e.utm_campaign, 128),
            "visitor_id": _trunc(e.visitor_id, 64),
            "session_id": _trunc(e.session_id, 64),
            "user_id": _trunc(e.user_id, 64),
            "device": _trunc(e.device, 16),
            "country": _trunc(e.country, 8),
            "domain": _trunc(e.domain, 128),
            "is_bot": e.is_bot,
            "bot_name": _trunc(e.bot_name, 64),
            "user_agent": _trunc(e.user_agent, 512),
        }
        for e in body.events
    ]
    await db.execute(insert(PageView), rows)
    return {"accepted": len(rows)}


@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_analytics_key),
):
    return await analytics_service.get_stats(db)


@router.get("/report")
async def get_report(
    report_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_analytics_key),
):
    """Daily report JSON. Defaults to yesterday (US Eastern)."""
    return await analytics_service.build_daily_report(db, report_date)


@router.post("/report/send")
async def send_report_now(
    report_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_analytics_key),
):
    """Build and email the daily report immediately (manual trigger)."""
    return await analytics_service.send_daily_report(db, report_date)
