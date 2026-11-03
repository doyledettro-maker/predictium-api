"""By-sport country split in the daily traffic report (Doyle, 2026-11-03):
which sections earn readers outside the US."""
from app.services import analytics_service as S


ROWS = [
    # sport totals (country None) carry their own distinct-visitor counts
    {"sport": "Tennis", "country": None, "views": 200, "visitors": 60},
    {"sport": "Tennis", "country": "US", "views": 120, "visitors": 40},
    {"sport": "Tennis", "country": "GB", "views": 40, "visitors": 10},
    {"sport": "Tennis", "country": "CA", "views": 25, "visitors": 6},
    {"sport": "Tennis", "country": "AU", "views": 10, "visitors": 3},
    {"sport": "Tennis", "country": "unknown", "views": 5, "visitors": 2},
    {"sport": "Other", "country": None, "views": 900, "visitors": 300},
    {"sport": "Other", "country": "US", "views": 900, "visitors": 300},
    {"sport": "NFL", "country": None, "views": 300, "visitors": 90},
    {"sport": "NFL", "country": "US", "views": 300, "visitors": 90},
]


def test_one_entry_per_sport_sorted_by_views_other_last():
    out = S.summarize_sport_countries(ROWS)
    assert [s["sport"] for s in out] == ["NFL", "Tennis", "Other"]


def test_totals_shares_and_top_non_us_countries():
    tennis = next(s for s in S.summarize_sport_countries(ROWS) if s["sport"] == "Tennis")
    assert tennis["views"] == 200 and tennis["visitors"] == 60
    # unknown geo (5 views) counts toward neither the US nor abroad
    assert tennis["us_views"] == 120 and tennis["unknown_views"] == 5
    assert tennis["non_us_views"] == 75 and tennis["non_us_share"] == 0.375
    assert [c["country"] for c in tennis["countries"]] == ["GB", "CA", "AU"]


def test_us_only_sport_reads_zero_outside_and_none():
    nfl = next(s for s in S.summarize_sport_countries(ROWS) if s["sport"] == "NFL")
    assert nfl["non_us_share"] == 0.0 and nfl["countries"] == []
    assert S._sport_country_line(nfl).endswith("0% outside US (top: none)")


def _report(sport_rows):
    day = {"pageviews": 10, "visitors": 5, "signups": 0, "bot_hits": 0}
    return {
        "date": "2026-11-02", "day": day, "previous_day": day, "last_7d": day,
        "prev_7d": day, "mtd": day, "ytd": day, "top_pages": [], "top_referrers": [],
        "countries": [], "bots": [], "sport_countries_7d": sport_rows,
    }


def test_slack_and_email_render_the_section():
    rows = S.summarize_sport_countries(ROWS)
    slack = S.render_report_slack(_report(rows))["text"]
    assert "By sport, last 7 days" in slack
    assert "Tennis: 200 views · 60 visitors · 38% outside US (top: GB 40, CA 25, AU 10)" in slack
    html = S.render_report_html(_report(rows))
    assert "share of views from outside the US" in html and "GB 40, CA 25, AU 10" in html


def test_empty_week_renders_a_placeholder():
    assert "(no page views recorded)" in S.render_report_slack(_report([]))["text"]
    assert "No page views recorded" in S.render_report_html(_report([]))


def test_sport_sections_match_the_frontend_menu_mapping():
    # Predictium_Front_End lib/sport-order.ts SECTION_TO_SPORT keys
    assert set(S.SPORT_SECTIONS) == {
        "nba", "rapm", "minutes", "fantasy", "nfl", "cfb", "tennis", "wnba",
        "soccer", "golf", "mlb", "world-cup", "bubble-watch",
    }
    assert "WHEN 'rapm' THEN 'NBA'" in S.SPORT_EXPR and S.SPORT_EXPR.endswith("ELSE 'Other' END")
