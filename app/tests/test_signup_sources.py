"""Sign-up source tracking (Doyle, 2026-11-14): which channel brought each
new user, read from the page views the tracker already logs."""
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import analytics_service as S


@pytest.mark.parametrize(
    "referrer, utm_source, utm_medium, expected",
    [
        ("https://t.co/abc123", None, None, "X"),
        ("https://x.com/PredictiumAI/status/1", None, None, "X"),
        ("https://www.google.com/", None, None, "Search"),
        ("https://www.google.co.uk/search?q=nfl", None, None, "Search"),
        ("https://duckduckgo.com/", None, None, "Search"),
        # AI hosts are checked before search, so Gemini is not Google search
        ("https://gemini.google.com/app", None, None, "AI assistant"),
        ("https://chatgpt.com/", None, None, "AI assistant"),
        ("https://www.perplexity.ai/search/x", None, None, "AI assistant"),
        ("https://old.reddit.com/r/sportsbook", None, None, "Reddit"),
        ("https://someblog.com/post", None, None, "Other site"),
        (None, None, None, "Direct"),
        ("", None, None, "Direct"),
        # our own pages and the sign-in round trip are not a source
        ("https://www.predictium.ai/nfl", None, None, "Direct"),
        ("https://predictium.auth.us-east-1.amazoncognito.com/login", None, None, "Direct"),
        ("https://accounts.google.com/", None, None, "Direct"),
        # UTM tags win over the referrer (our X links carry utm_source=x)
        ("https://www.google.com/", "x", "social", "X"),
        (None, "newsletter", None, "Email"),
        (None, "partner", "email", "Email"),
        (None, "reddit", "social", "Reddit"),
        (None, "chatgpt.com", None, "AI assistant"),
        (None, "somepartner", "referral", "Other site"),
        # #21 review: mail hosts were read as search, paid search as other
        ("https://mail.google.com/mail/u/0/", None, None, "Email"),
        ("https://mail.yahoo.com/d/folders/1", None, None, "Email"),
        ("android-app://com.google.android.gm/", None, None, "Email"),
        ("android-app://com.google.android.googlequicksearchbox/", None, None, "Search"),
        (None, "google", "cpc", "Search"),
        (None, "bing", "ppc", "Search"),
    ],
)
def test_classify_channel(referrer, utm_source, utm_medium, expected):
    assert S.classify_channel(referrer, utm_source, utm_medium) == expected


def test_referrer_host_normalizes():
    assert S.referrer_host("https://www.google.com/search?q=x") == "google.com"
    assert S.referrer_host("http://t.co:443/abc") == "t.co"
    assert S.referrer_host(None) is None and S.referrer_host("") is None


def _row(channel, path):
    first = {"path": path, "channel": channel} if channel != "Unknown" else None
    return {"user_id": "u", "signed_up_at": "", "channel": channel, "first_visit": first}


def test_summary_counts_in_fixed_order_and_drops_zeros():
    rows = [
        _row("Search", "/nfl"), _row("X", "/blog/a"), _row("X", "/blog/a"),
        _row("Unknown", None), _row("Direct", "/"),
    ]
    out = S.summarize_signup_sources(rows)
    assert out["signups"] == 5
    assert out["by_channel"] == [
        {"channel": "X", "signups": 2}, {"channel": "Search", "signups": 1},
        {"channel": "Direct", "signups": 1}, {"channel": "Unknown", "signups": 1},
    ]
    assert out["first_pages"][0] == {"path": "/blog/a", "signups": 2}
    assert "user_id" not in str(out) and "@" not in str(out)


def test_line_and_empty():
    out = S.summarize_signup_sources([_row("X", "/blog/a")])
    assert S._signup_sources_line(out) == "X 1 (first pages: /blog/a 1)"
    assert S._signup_sources_line(S.summarize_signup_sources([])) == "no sign-ups"


def test_touch_builds_a_visit_or_none():
    ts = datetime(2026, 11, 12, 22, 0, tzinfo=timezone.utc)
    row = SimpleNamespace(
        ft_ts=ts, ft_path="/blog/cfb", ft_referrer="https://t.co/x",
        ft_utm_source=None, ft_utm_medium=None, ft_utm_campaign=None,
        sv_ts=None, sv_path=None, sv_referrer=None,
        sv_utm_source=None, sv_utm_medium=None, sv_utm_campaign=None,
    )
    first = S._touch(row, "ft")
    assert first["channel"] == "X" and first["referrer_host"] == "t.co"
    assert first["path"] == "/blog/cfb" and first["at"].startswith("2026-11-12")
    assert S._touch(row, "sv") is None


def _report(signup_summary):
    day = {"pageviews": 10, "visitors": 5, "signups": 1, "bot_hits": 0}
    return {
        "date": "2026-11-13", "day": day, "previous_day": day, "last_7d": day,
        "prev_7d": day, "mtd": day, "ytd": day, "top_pages": [], "top_referrers": [],
        "countries": [], "bots": [], "sport_countries_7d": [],
        "signup_sources_7d": signup_summary,
    }


def test_report_renders_the_line_in_slack_and_email():
    summary = S.summarize_signup_sources([_row("X", "/blog/a"), _row("Unknown", None)])
    slack = S.render_report_slack(_report(summary))["text"]
    assert "Sign-ups by source, last 7 days:* X 1 · Unknown 1 (first pages: /blog/a 1)" in slack
    html = S.render_report_html(_report(summary))
    assert "Sign-ups by source, last 7 days" in html and "X 1 · Unknown 1" in html
    assert "no sign-ups" in S.render_report_slack(_report({}))["text"]


def test_report_survives_a_failed_attribution(monkeypatch):
    """#21 review: a failure in the sign-up summary must not take down the
    report. The savepoint is rolled back and the line reads unavailable."""
    import asyncio
    from contextlib import asynccontextmanager

    class FakeDB:
        nested = 0

        @asynccontextmanager
        async def _nested(self):
            FakeDB.nested += 1
            yield

        def begin_nested(self):
            return self._nested()

    async def boom(db, start, end):
        raise RuntimeError("relation page_views does not exist")

    monkeypatch.setattr(S, "_signup_attribution", boom)
    out = asyncio.run(S._signup_sources_summary_safe(FakeDB(), None, None))
    assert out["unavailable"] is True and FakeDB.nested == 1
    slack = S.render_report_slack(_report(out))["text"]
    assert "Sign-ups by source, last 7 days:* not available today" in slack
    assert "not available today" in S.render_report_html(_report(out))


def test_signup_sources_are_off_the_stats_hot_path():
    """#21 review: /analytics/stats feeds the nav-order route on a 15s
    timeout, so the attribution query never runs there."""
    import inspect
    assert "_signup_attribution" not in inspect.getsource(S.get_stats)
    assert "_signup_sources_summary_safe" in inspect.getsource(S.build_daily_report)


def test_no_em_dashes_in_new_copy():
    summary = S.summarize_signup_sources([_row("Search", "/nfl")])
    assert "—" not in S._signup_sources_line(summary)


# --- SQL against a real Postgres (skipped unless TEST_PG_URL is set) -----

PG_URL = os.environ.get("TEST_PG_URL")


@pytest.mark.skipif(not PG_URL, reason="TEST_PG_URL not set")
@pytest.mark.asyncio
async def test_attribution_sql_on_postgres():
    from datetime import date
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

    engine = create_async_engine(PG_URL)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS page_views, users"))
        await conn.execute(text(
            "CREATE TABLE users (cognito_id text, created_at timestamptz)"))
        await conn.execute(text(
            "CREATE TABLE page_views (ts timestamptz, path text, referrer text, "
            "utm_source text, utm_medium text, utm_campaign text, visitor_id text, "
            "session_id text, user_id text, is_bot boolean default false)"))
        await conn.execute(text("""
            INSERT INTO users VALUES
              ('sub-x', '2026-11-12 18:00+00'),      -- found us on X days earlier
              ('sub-none', '2026-11-12 19:00+00'),   -- signed up, no trail
              ('sub-newdev', '2026-11-12 20:00+00'), -- signed up on a new device
              ('sub-ancient', '2026-11-12 21:00+00'), -- only trail is past the lookback
              ('sub-old', '2026-10-01 12:00+00')     -- outside the window
        """))
        await conn.execute(text("""
            INSERT INTO page_views (ts, path, referrer, utm_source, visitor_id, session_id, user_id, is_bot) VALUES
              ('2026-11-08 12:00+00', '/blog/cfb-top-12', 'https://t.co/a', NULL, 'v1', 's1', NULL, false),
              ('2026-11-08 11:00+00', '/', NULL, NULL, 'v1', 's0', NULL, true),            -- bot, ignored
              ('2026-11-12 17:50+00', '/nfl', 'https://www.google.com/', NULL, 'v1', 's2', NULL, false),
              ('2026-11-12 17:55+00', '/login', NULL, NULL, 'v1', 's2', NULL, false),
              ('2026-11-12 18:01+00', '/dashboard', 'https://accounts.google.com/', NULL, 'v1', 's2', 'sub-x', false),
              ('2026-11-12 18:05+00', '/nba', NULL, NULL, 'v1', 's2', 'sub-x', false),
              ('2026-10-01 11:00+00', '/', 'https://t.co/b', NULL, 'v9', 's9', 'sub-old', false),
              -- new device: the first hit IS the return from the sign-in round trip
              ('2026-11-12 20:01+00', '/dashboard', 'https://predictium.auth.us-east-1.amazoncognito.com/', NULL, 'v5', 's5', 'sub-newdev', false),
              ('2026-11-12 20:02+00', '/nfl', NULL, NULL, 'v5', 's5', 'sub-newdev', false),
              -- a visit 300 days back is outside the 180-day scan and never read
              ('2026-01-15 12:00+00', '/blog/old', 'https://t.co/z', NULL, 'v7', 's7', NULL, false),
              ('2026-11-12 21:01+00', '/dashboard', NULL, NULL, 'v7', 's8', 'sub-ancient', false)
        """))
    async with AsyncSession(engine) as db:
        rows = await S._signup_attribution(db, date(2026, 11, 6), date(2026, 11, 13))
    await engine.dispose()

    by_id = {r["user_id"]: r for r in rows}
    assert set(by_id) == {"sub-x", "sub-none", "sub-newdev", "sub-ancient"}
    # the sign-in round trip is not a first visit: no earlier hit, Unknown
    nd = by_id["sub-newdev"]
    assert nd["channel"] == "Unknown" and nd["first_visit"] is None and nd["signup_visit"] is None
    anc = by_id["sub-ancient"]
    assert anc["channel"] == "Unknown" and anc["first_visit"] is None
    x = by_id["sub-x"]
    assert x["channel"] == "X"
    assert x["first_visit"]["path"] == "/blog/cfb-top-12"
    # the sign-up visit is the landing page of the session they signed in on
    assert x["signup_visit"]["path"] == "/nfl" and x["signup_visit"]["channel"] == "Search"
    none = by_id["sub-none"]
    assert none["channel"] == "Unknown" and none["first_visit"] is None and none["signup_visit"] is None
