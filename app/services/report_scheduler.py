"""
Daily traffic report scheduler.

A lightweight asyncio loop (no extra dependencies) started from the app
lifespan. Once a day at REPORT_HOUR_UTC it builds yesterday's traffic
report and emails it via SES when REPORT_EMAIL_TO/FROM are configured.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db.database import async_session_maker
from app.services import analytics_service

logger = logging.getLogger(__name__)


def _seconds_until_next_run(hour_utc: int) -> float:
    now = datetime.now(timezone.utc)
    next_run = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return (next_run - now).total_seconds()


async def _run_once() -> None:
    async with async_session_maker() as session:
        report = await analytics_service.send_daily_report(session)
        logger.info(
            "Daily traffic report generated for %s (emailed=%s)",
            report.get("date"),
            report.get("emailed"),
        )


async def daily_report_loop() -> None:
    settings = get_settings()
    if not settings.analytics_ingest_key:
        logger.info("Daily report scheduler disabled: analytics not configured")
        return
    logger.info(
        "Daily report scheduler started (send hour %02d:00 UTC, email %s)",
        settings.report_hour_utc,
        "on" if settings.report_email_to else "off — set REPORT_EMAIL_TO/FROM",
    )
    while True:
        try:
            await asyncio.sleep(_seconds_until_next_run(settings.report_hour_utc))
            await _run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Daily report run failed; retrying at next scheduled hour")
            # Avoid a tight failure loop if the failure was instantaneous
            await asyncio.sleep(60)
