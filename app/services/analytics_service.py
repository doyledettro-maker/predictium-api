"""
Analytics service — first-party traffic aggregation + daily report.

All date bucketing uses US Eastern time (site audience is US sports),
computed in SQL via `ts AT TIME ZONE :tz`.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import boto3
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

logger = logging.getLogger(__name__)

TZ = "America/New_York"

# Referrer hosts that are ourselves (excluded from top-referrer lists)
SELF_HOSTS = ("predictium.ai", "40pfrom3.com", "localhost")


def _local_date_expr(col: str = "ts") -> str:
    return f"({col} AT TIME ZONE :tz)::date"


async def _count_range(
    db: AsyncSession, start: date, end: date, extra: str = "AND NOT is_bot"
) -> dict[str, int]:
    """Pageviews + unique visitors for local dates in [start, end]."""
    q = text(
        f"""
        SELECT COUNT(*) AS pageviews,
               COUNT(DISTINCT visitor_id) AS visitors
        FROM page_views
        WHERE {_local_date_expr()} BETWEEN :start AND :end {extra}
        """
    )
    row = (await db.execute(q, {"tz": TZ, "start": start, "end": end})).one()
    return {"pageviews": int(row.pageviews or 0), "visitors": int(row.visitors or 0)}


async def _signup_count(db: AsyncSession, start: date, end: date) -> int:
    q = text(
        f"""
        SELECT COUNT(*) FROM users
        WHERE {_local_date_expr('created_at')} BETWEEN :start AND :end
        """
    )
    return int((await db.execute(q, {"tz": TZ, "start": start, "end": end})).scalar() or 0)


async def _top_list(
    db: AsyncSession,
    select_expr: str,
    alias: str,
    start: date,
    end: date,
    where_extra: str = "AND NOT is_bot",
    limit: int = 15,
) -> list[dict[str, Any]]:
    q = text(
        f"""
        SELECT {select_expr} AS {alias},
               COUNT(*) AS views,
               COUNT(DISTINCT visitor_id) AS visitors
        FROM page_views
        WHERE {_local_date_expr()} BETWEEN :start AND :end {where_extra}
        GROUP BY 1
        ORDER BY views DESC
        LIMIT :limit
        """
    )
    rows = (await db.execute(q, {"tz": TZ, "start": start, "end": end, "limit": limit})).all()
    return [
        {alias: getattr(r, alias), "views": int(r.views), "visitors": int(r.visitors)}
        for r in rows
    ]


# Normalize a referrer URL to its host in SQL (strip scheme, path, www.)
REFERRER_HOST_EXPR = (
    "regexp_replace(regexp_replace(regexp_replace(referrer, '^https?://', ''), '/.*$', ''), '^www\\.', '')"
)


async def _top_referrers(
    db: AsyncSession, start: date, end: date, limit: int = 15
) -> list[dict[str, Any]]:
    # Exclude self-referrals; regex operator avoids LIKE's %-escaping pitfalls
    self_pattern = "|".join(h.replace(".", "\\.") for h in SELF_HOSTS)
    self_filter = f"{REFERRER_HOST_EXPR} !~ '{self_pattern}'"
    q = text(
        f"""
        SELECT {REFERRER_HOST_EXPR} AS referrer,
               COUNT(*) AS views,
               COUNT(DISTINCT visitor_id) AS visitors
        FROM page_views
        WHERE {_local_date_expr()} BETWEEN :start AND :end
          AND NOT is_bot
          AND referrer IS NOT NULL AND referrer <> ''
          AND {self_filter}
        GROUP BY 1
        ORDER BY views DESC
        LIMIT :limit
        """
    )
    rows = (await db.execute(q, {"tz": TZ, "start": start, "end": end, "limit": limit})).all()
    return [
        {"referrer": r.referrer, "views": int(r.views), "visitors": int(r.visitors)}
        for r in rows
    ]


async def _bot_hits(
    db: AsyncSession, start: date, end: date, limit: int = 20
) -> list[dict[str, Any]]:
    q = text(
        f"""
        SELECT COALESCE(bot_name, 'Other bot') AS bot, COUNT(*) AS hits
        FROM page_views
        WHERE {_local_date_expr()} BETWEEN :start AND :end AND is_bot
        GROUP BY 1 ORDER BY hits DESC LIMIT :limit
        """
    )
    rows = (await db.execute(q, {"tz": TZ, "start": start, "end": end, "limit": limit})).all()
    return [{"bot": r.bot, "hits": int(r.hits)} for r in rows]


async def _daily_series(db: AsyncSession, start: date, end: date) -> list[dict[str, Any]]:
    q = text(
        f"""
        SELECT {_local_date_expr()} AS day,
               COUNT(*) FILTER (WHERE NOT is_bot) AS pageviews,
               COUNT(DISTINCT visitor_id) FILTER (WHERE NOT is_bot) AS visitors,
               COUNT(*) FILTER (WHERE is_bot) AS bot_hits
        FROM page_views
        WHERE {_local_date_expr()} BETWEEN :start AND :end
        GROUP BY 1 ORDER BY 1
        """
    )
    rows = (await db.execute(q, {"tz": TZ, "start": start, "end": end})).all()
    by_day = {
        r.day: {
            "pageviews": int(r.pageviews or 0),
            "visitors": int(r.visitors or 0),
            "bot_hits": int(r.bot_hits or 0),
        }
        for r in rows
    }
    # Fill gaps so charts render a continuous axis
    series = []
    d = start
    while d <= end:
        entry = by_day.get(d, {"pageviews": 0, "visitors": 0, "bot_hits": 0})
        series.append({"date": d.isoformat(), **entry})
        d += timedelta(days=1)
    return series


def _now_local_date() -> date:
    # Eastern is UTC-4/-5; subtracting 5h from UTC is wrong half the year,
    # so use zoneinfo for the app-side "today" anchor.
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(TZ)).date()


async def get_stats(db: AsyncSession) -> dict[str, Any]:
    """Full stats payload for the admin Traffic dashboard."""
    today = _now_local_date()
    yesterday = today - timedelta(days=1)
    jan1 = date(today.year, 1, 1)
    far_past = date(2020, 1, 1)

    summary = {
        "today": await _count_range(db, today, today),
        "yesterday": await _count_range(db, yesterday, yesterday),
        "last_7d": await _count_range(db, today - timedelta(days=6), today),
        "prev_7d": await _count_range(db, today - timedelta(days=13), today - timedelta(days=7)),
        "last_30d": await _count_range(db, today - timedelta(days=29), today),
        "prev_30d": await _count_range(db, today - timedelta(days=59), today - timedelta(days=30)),
        "ytd": await _count_range(db, jan1, today),
        "all_time": await _count_range(db, far_past, today),
    }

    window_30 = (today - timedelta(days=29), today)
    signups = {
        "today": await _signup_count(db, today, today),
        "last_7d": await _signup_count(db, today - timedelta(days=6), today),
        "last_30d": await _signup_count(db, *window_30),
        "ytd": await _signup_count(db, jan1, today),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timezone": TZ,
        "summary": summary,
        "signups": signups,
        "daily_series": await _daily_series(db, today - timedelta(days=89), today),
        "top_pages": await _top_list(db, "path", "path", *window_30),
        "top_referrers": await _top_referrers(db, *window_30),
        "utm_sources": await _top_list(
            db, "utm_source", "utm_source", *window_30,
            where_extra="AND NOT is_bot AND utm_source IS NOT NULL", limit=10,
        ),
        "utm_campaigns": await _top_list(
            db, "utm_campaign", "utm_campaign", *window_30,
            where_extra="AND NOT is_bot AND utm_campaign IS NOT NULL", limit=15,
        ),
        "devices": await _top_list(db, "COALESCE(device, 'unknown')", "device", *window_30, limit=5),
        "countries": await _top_list(
            db, "COALESCE(country, '??')", "country", *window_30, limit=10
        ),
        "domains": await _top_list(db, "COALESCE(domain, 'unknown')", "domain", *window_30, limit=5),
        "bots": {
            "today": await _bot_hits(db, today, today),
            "last_30d": await _bot_hits(db, *window_30),
        },
    }


async def build_daily_report(db: AsyncSession, report_date: Optional[date] = None) -> dict[str, Any]:
    """Daily report: the given day (default: yesterday) + WTD/MTD/YTD context."""
    if report_date is None:
        report_date = _now_local_date() - timedelta(days=1)

    prev_day = report_date - timedelta(days=1)
    week_ago = report_date - timedelta(days=6)
    prev_week_start, prev_week_end = report_date - timedelta(days=13), report_date - timedelta(days=7)
    month_start = date(report_date.year, report_date.month, 1)
    jan1 = date(report_date.year, 1, 1)

    day = await _count_range(db, report_date, report_date)
    day["bot_hits"] = sum(b["hits"] for b in await _bot_hits(db, report_date, report_date))
    day["signups"] = await _signup_count(db, report_date, report_date)

    prev = await _count_range(db, prev_day, prev_day)

    last7 = await _count_range(db, week_ago, report_date)
    last7["signups"] = await _signup_count(db, week_ago, report_date)
    prev7 = await _count_range(db, prev_week_start, prev_week_end)

    mtd = await _count_range(db, month_start, report_date)
    mtd["signups"] = await _signup_count(db, month_start, report_date)

    ytd = await _count_range(db, jan1, report_date)
    ytd["signups"] = await _signup_count(db, jan1, report_date)

    return {
        "date": report_date.isoformat(),
        "timezone": TZ,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "day": day,
        "previous_day": prev,
        "last_7d": last7,
        "prev_7d": prev7,
        "mtd": mtd,
        "ytd": ytd,
        "daily_series_14d": await _daily_series(db, report_date - timedelta(days=13), report_date),
        "top_pages": await _top_list(db, "path", "path", report_date, report_date, limit=10),
        "top_referrers": await _top_referrers(db, report_date, report_date, limit=10),
        "countries": await _top_list(
            db,
            "COALESCE(NULLIF(country, ''), 'unknown')",
            "country",
            report_date,
            report_date,
            limit=10,
        ),
        "bots": await _bot_hits(db, report_date, report_date, limit=10),
    }


def _pct_change(current: int, previous: int) -> str:
    if previous == 0:
        return "—" if current == 0 else "new"
    sign = "+" if current >= previous else ""
    return f"{sign}{(current - previous) / previous * 100:.0f}%"


def render_report_html(report: dict[str, Any]) -> str:
    """Simple, email-client-safe HTML for the daily report."""
    day, prev = report["day"], report["previous_day"]
    last7, prev7 = report["last_7d"], report["prev_7d"]
    mtd, ytd = report["mtd"], report["ytd"]

    def row(label: str, pv: Any, uv: Any, extra: str = "") -> str:
        return (
            f"<tr><td style='padding:6px 12px'>{label}</td>"
            f"<td style='padding:6px 12px;text-align:right'><b>{pv}</b></td>"
            f"<td style='padding:6px 12px;text-align:right'>{uv}</td>"
            f"<td style='padding:6px 12px;text-align:right'>{extra}</td></tr>"
        )

    pages = "".join(
        f"<tr><td style='padding:4px 12px'>{p['path']}</td>"
        f"<td style='padding:4px 12px;text-align:right'>{p['views']}</td></tr>"
        for p in report["top_pages"]
    ) or "<tr><td style='padding:4px 12px' colspan='2'>No page views recorded</td></tr>"

    refs = "".join(
        f"<tr><td style='padding:4px 12px'>{r['referrer']}</td>"
        f"<td style='padding:4px 12px;text-align:right'>{r['views']}</td></tr>"
        for r in report["top_referrers"]
    ) or "<tr><td style='padding:4px 12px' colspan='2'>No external referrers</td></tr>"

    bots = "".join(
        f"<tr><td style='padding:4px 12px'>{b['bot']}</td>"
        f"<td style='padding:4px 12px;text-align:right'>{b['hits']}</td></tr>"
        for b in report["bots"]
    ) or "<tr><td style='padding:4px 12px' colspan='2'>No AI/search crawler hits</td></tr>"

    countries = "".join(
        f"<tr><td style='padding:4px 12px'>{c['country']}</td>"
        f"<td style='padding:4px 12px;text-align:right'>{c['views']}</td>"
        f"<td style='padding:4px 12px;text-align:right'>{c['visitors']}</td></tr>"
        for c in report.get("countries", [])
    ) or "<tr><td style='padding:4px 12px' colspan='3'>No country data recorded</td></tr>"

    th = "padding:6px 12px;text-align:left;border-bottom:1px solid #ddd"
    thr = "padding:6px 12px;text-align:right;border-bottom:1px solid #ddd"

    return f"""
<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;color:#111">
  <h2 style="margin-bottom:4px">Predictium Traffic Report — {report['date']}</h2>
  <p style="color:#666;margin-top:0">All times US Eastern. Bots excluded from page views/visitors.</p>

  <table style="border-collapse:collapse;width:100%;border:1px solid #ddd">
    <tr style="background:#f5f5f5">
      <th style="{th}">Period</th><th style="{thr}">Page views</th>
      <th style="{thr}">Visitors</th><th style="{thr}">Signups</th>
    </tr>
    {row(f"{report['date']} (vs prev day {_pct_change(day['pageviews'], prev['pageviews'])})", day['pageviews'], day['visitors'], day['signups'])}
    {row(f"Last 7 days (vs prior 7d {_pct_change(last7['pageviews'], prev7['pageviews'])})", last7['pageviews'], last7['visitors'], last7['signups'])}
    {row("Month to date", mtd['pageviews'], mtd['visitors'], mtd['signups'])}
    {row("Year to date", ytd['pageviews'], ytd['visitors'], ytd['signups'])}
  </table>

  <p style="margin:16px 0 4px"><b>AI &amp; search crawler hits ({day.get('bot_hits', 0)} total)</b></p>
  <table style="border-collapse:collapse;width:100%;border:1px solid #ddd">{bots}</table>

  <p style="margin:16px 0 4px"><b>Top pages</b></p>
  <table style="border-collapse:collapse;width:100%;border:1px solid #ddd">{pages}</table>

  <p style="margin:16px 0 4px"><b>Top referrers</b></p>
  <table style="border-collapse:collapse;width:100%;border:1px solid #ddd">{refs}</table>

  <p style="margin:16px 0 4px"><b>Top countries</b></p>
  <table style="border-collapse:collapse;width:100%;border:1px solid #ddd">{countries}</table>

  <p style="color:#999;font-size:12px;margin-top:20px">
    Full dashboard: <a href="https://www.predictium.ai/admin/traffic">predictium.ai/admin/traffic</a>
  </p>
</div>
"""


def send_report_email(report: dict[str, Any]) -> bool:
    """Send the daily report via AWS SES. Returns True on success."""
    settings = get_settings()
    if not settings.report_email_to or not settings.report_email_from:
        logger.info("Daily report email skipped: REPORT_EMAIL_TO/FROM not configured")
        return False

    ses = boto3.client(
        "ses",
        region_name=settings.ses_region or settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
    )
    day = report["day"]
    subject = (
        f"Predictium traffic {report['date']}: {day['pageviews']} views, "
        f"{day['visitors']} visitors, {day.get('signups', 0)} signups"
    )
    ses.send_email(
        Source=settings.report_email_from,
        Destination={"ToAddresses": [a.strip() for a in settings.report_email_to.split(",") if a.strip()]},
        Message={
            "Subject": {"Data": subject},
            "Body": {"Html": {"Data": render_report_html(report)}},
        },
    )
    logger.info("Daily traffic report emailed for %s", report["date"])
    return True


def render_report_slack(report: dict[str, Any]) -> dict[str, Any]:
    """Slack incoming-webhook payload (mrkdwn) for the daily report."""
    day, prev = report["day"], report["previous_day"]
    last7, prev7 = report["last_7d"], report["prev_7d"]
    mtd, ytd = report["mtd"], report["ytd"]

    bots = ", ".join(f"{b['bot']} {b['hits']}" for b in report["bots"][:6]) or "none"
    pages = "\n".join(
        f"  {i + 1}. `{p['path']}` — {p['views']}"
        for i, p in enumerate(report["top_pages"][:5])
    ) or "  (no page views recorded)"
    refs = "\n".join(
        f"  {i + 1}. {r['referrer']} — {r['views']}"
        for i, r in enumerate(report["top_referrers"][:5])
    ) or "  (no external referrers)"
    countries = "\n".join(
        f"  {i + 1}. {c['country']} — {c['views']} views · {c['visitors']} visitors"
        for i, c in enumerate(report.get("countries", [])[:5])
    ) or "  (no country data recorded)"

    text_lines = [
        f"*Predictium Traffic — {report['date']}* (US Eastern, bots excluded)",
        f"Yesterday: *{day['pageviews']:,}* views · {day['visitors']:,} visitors · "
        f"{day.get('signups', 0)} signups ({_pct_change(day['pageviews'], prev['pageviews'])} vs prior day)",
        f"Last 7d: {last7['pageviews']:,} views ({_pct_change(last7['pageviews'], prev7['pageviews'])} vs prior 7d) · "
        f"MTD {mtd['pageviews']:,} · YTD {ytd['pageviews']:,}",
        f"AI/search crawler hits: {day.get('bot_hits', 0)} ({bots})",
        f"*Top pages:*\n{pages}",
        f"*Top referrers:*\n{refs}",
        f"*Top countries:*\n{countries}",
        "<https://www.predictium.ai/admin/traffic|Full dashboard →>",
    ]
    return {"text": "\n".join(text_lines)}


async def send_report_slack(report: dict[str, Any]) -> bool:
    """Post the daily report to Slack via incoming webhook. True on success."""
    settings = get_settings()
    if not settings.slack_webhook_url:
        logger.info("Daily report Slack post skipped: SLACK_WEBHOOK_URL not configured")
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(settings.slack_webhook_url, json=render_report_slack(report))
        resp.raise_for_status()
    logger.info("Daily traffic report posted to Slack for %s", report["date"])
    return True


async def send_daily_report(db: AsyncSession, report_date: Optional[date] = None) -> dict[str, Any]:
    """Build the report, then email it and post it to Slack.

    Delivery failures are logged, never raised — each channel is independent.
    """
    report = await build_daily_report(db, report_date)
    try:
        report["emailed"] = await asyncio.to_thread(send_report_email, report)
    except Exception:
        logger.exception("Failed to send daily traffic report email")
        report["emailed"] = False
    try:
        report["slacked"] = await send_report_slack(report)
    except Exception:
        logger.exception("Failed to post daily traffic report to Slack")
        report["slacked"] = False
    return report
