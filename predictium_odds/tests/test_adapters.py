"""Adapter parsing tests against fixture payloads (no network)."""

import predictium_odds.books.bovada as bovada
import predictium_odds.books.fanduel as fanduel
import predictium_odds.books.kalshi as kalshi
import predictium_odds.books.pinnacle as pinnacle
from predictium_odds.books.base import SportConfig
from predictium_odds.lines import align_main_line

TEAMS = {"Buffalo Bills": "BUF", "Kansas City Chiefs": "KC",
         "Buffalo": "BUF", "Kansas City": "KC"}

CFG = SportConfig(
    sport="nfl",
    resolve_team=lambda s: TEAMS.get(s),
    event_key=lambda h, a, t: f"{a}@{h}_{t[:10]}",
    bovada_path="/football/nfl",
    fanduel_page_id="nfl",
    espn_league="football/nfl",
    kalshi_prefix="KXNFL",
)


# --- bovada -------------------------------------------------------------------

BOVADA_GAME = [{
    "events": [{
        "link": "/football/nfl/x", "startTime": 1789300000000,
        "competitors": [{"home": True, "name": "Kansas City Chiefs"},
                        {"home": False, "name": "Buffalo Bills"}],
        "displayGroups": [{"markets": [
            {"description": "Moneyline", "period": {"description": "Game"},
             "outcomes": [
                 {"description": "Kansas City Chiefs",
                  "price": {"american": "-130"}},
                 {"description": "Buffalo Bills",
                  "price": {"american": "EVEN"}}]},
            {"description": "Point Spread", "period": {"description": "Game"},
             "outcomes": [
                 {"description": "Kansas City Chiefs",
                  "price": {"american": "-110", "handicap": "-2.5"}},
                 {"description": "Buffalo Bills",
                  "price": {"american": "-110", "handicap": "2.5"}}]},
            {"description": "Total", "period": {"description": "Game"},
             "outcomes": [
                 {"description": "Over",
                  "price": {"american": "-105", "handicap": "47.5"}},
                 {"description": "Under",
                  "price": {"american": "-115", "handicap": "47.5"}}]},
            # non-game period must be dropped
            {"description": "Moneyline",
             "period": {"description": "First Half"},
             "outcomes": [{"description": "Kansas City Chiefs",
                           "price": {"american": "-150"}}]},
        ]}],
    }],
}]


def test_bovada_game_lines(monkeypatch):
    monkeypatch.setattr(bovada, "get_json", lambda url, **kw: BOVADA_GAME)
    quotes, report = bovada.fetch_game_lines(CFG)
    assert report.ok and report.n_markets == 6
    by = {(q.market, q.side): q for q in quotes}
    assert by[("moneyline", "home")].price_american == -130
    assert by[("moneyline", "away")].price_american == 100  # EVEN
    # betting convention: home-framed line on both spread sides
    assert by[("spread", "home")].line == -2.5
    assert by[("spread", "away")].line == -2.5
    assert by[("total", "over")].line == 47.5
    # epoch-ms startTime is normalized to ISO before event_key sees it
    assert all(q.event_key == "BUF@KC_2026-09-13" for q in quotes)


def test_bovada_failure_reports_not_raises(monkeypatch):
    def boom(url, **kw):
        raise OSError("down")
    monkeypatch.setattr(bovada, "get_json", boom)
    quotes, report = bovada.fetch_game_lines(CFG)
    assert quotes == [] and not report.ok and "down" in report.note


BOVADA_WINS = [{
    "events": [{
        "link": ("/football/nfl-regular-season-wins/afc-east/buffalo-bills/"
                 "regular-season-wins-2026-27-202701030000"),
        "displayGroups": [{"markets": [{
            "description": "Bills Regular Season Wins",
            "outcomes": [
                {"description": "Over 10.5", "price": {"american": "-130"}},
                {"description": "Under 10.5", "price": {"american": "EVEN"}},
            ]}]}],
    }],
}]


def test_bovada_win_totals(monkeypatch):
    monkeypatch.setattr(bovada, "get_json", lambda url, **kw: BOVADA_WINS)
    quotes, report = bovada.fetch_win_totals(CFG, "/football/nfl-regular-season-wins")
    assert report.n_markets == 2
    over = next(q for q in quotes if q.side == "over")
    assert (over.event_key, over.line, over.price_american) == ("BUF", 10.5, -130)


# --- fanduel ------------------------------------------------------------------

FANDUEL_PAGE = {"attachments": {
    "events": {"9": {"name": "Buffalo Bills @ Kansas City Chiefs",
                     "openDate": "2026-09-13T17:00:00.000Z"}},
    "markets": {
        "m1": {"marketType": "MONEY_LINE", "eventId": 9, "marketId": "m1",
               "runners": [
                   {"runnerName": "Kansas City Chiefs",
                    "winRunnerOdds": {"americanDisplayOdds":
                                      {"americanOdds": "-125"}}},
                   {"runnerName": "Buffalo Bills",
                    "winRunnerOdds": {"americanDisplayOdds":
                                      {"americanOdds": "105"}}}]},
        "m2": {"marketType": "MATCH_HANDICAP_(2-WAY)", "eventId": 9,
               "marketId": "m2",
               "runners": [
                   {"runnerName": "Kansas City Chiefs", "handicap": -2.5,
                    "winRunnerOdds": {"americanDisplayOdds":
                                      {"americanOdds": "-108"}}},
                   {"runnerName": "Buffalo Bills", "handicap": 2.5,
                    "winRunnerOdds": {"americanDisplayOdds":
                                      {"americanOdds": "-112"}}}]},
        "m3": {"marketType": "TOTAL_POINTS_(OVER/UNDER)", "eventId": 9,
               "marketId": "m3",
               "runners": [
                   {"runnerName": "Over 47.5 Points", "handicap": 47.5,
                    "winRunnerOdds": {"americanDisplayOdds":
                                      {"americanOdds": "-110"}}},
                   {"runnerName": "Under 47.5 Points", "handicap": 47.5,
                    "winRunnerOdds": {"americanDisplayOdds":
                                      {"americanOdds": "-110"}}}]},
        "skip": {"marketType": "SUPER_BOWL_WINNER_SGP", "eventId": 9,
                 "runners": []},
    },
}}


def test_fanduel_game_lines(monkeypatch):
    monkeypatch.setattr(fanduel, "get_json", lambda url, params=None, **kw:
                        FANDUEL_PAGE)
    quotes, report = fanduel.fetch_game_lines(CFG)
    assert report.ok and report.n_markets == 6
    by = {(q.market, q.side): q for q in quotes}
    assert by[("moneyline", "home")].price_american == -125
    assert by[("spread", "home")].line == -2.5
    assert by[("spread", "away")].line == -2.5  # home-framed on both sides
    assert by[("total", "under")].line == 47.5
    assert all(q.event_key == "BUF@KC_2026-09-13" for q in quotes)


# --- kalshi -------------------------------------------------------------------

KALSHI_WINS_PAGE = {"markets": [
    {"event_ticker": "KXNFLWINS-27BUF", "ticker": "KXNFLWINS-27BUF-10",
     "strike_type": "greater_or_equal", "floor_strike": 10,
     "yes_bid_dollars": "0.70", "yes_ask_dollars": "0.74"},
    {"event_ticker": "KXNFLWINS-27BUF", "ticker": "KXNFLWINS-27BUF-11",
     "strike_type": "greater_or_equal", "floor_strike": 11,
     "yes_bid_dollars": "0.52", "yes_ask_dollars": "0.57"},
    # legacy-null fields (post-2026-07 API): dropped as unquoted
    {"event_ticker": "KXNFLWINS-27BUF", "ticker": "KXNFLWINS-27BUF-12",
     "strike_type": "greater_or_equal", "floor_strike": 12,
     "yes_bid_dollars": None, "yes_ask_dollars": None},
    # too-wide spread: dropped as thin
    {"event_ticker": "KXNFLWINS-27BUF", "ticker": "KXNFLWINS-27BUF-13",
     "strike_type": "greater_or_equal", "floor_strike": 13,
     "yes_bid_dollars": "0.05", "yes_ask_dollars": "0.40"},
    {"event_ticker": "KXNFLWINS-27JAC", "ticker": "KXNFLWINS-27JAC-9",
     "strike_type": "greater_or_equal", "floor_strike": 9,
     "yes_bid_dollars": "0.48", "yes_ask_dollars": "0.52"},
], "cursor": None}


def test_kalshi_ladders_and_alignment(monkeypatch):
    monkeypatch.setattr(kalshi, "get_json", lambda url, params=None, **kw:
                        KALSHI_WINS_PAGE)
    ladders, report = kalshi.fetch_ladders(
        CFG, "WINS", team_code_map={"JAC": "JAX", "LAR": "LA"})
    assert report.ok and set(ladders) == {"BUF", "JAX"}
    assert [r["strike"] for r in ladders["BUF"]] == [10, 11]
    by_line = kalshi.ladder_by_line(ladders["BUF"])
    assert align_main_line(by_line, 10.5)["ticker"] == "KXNFLWINS-27BUF-11"
    assert align_main_line(by_line, 12.5) is None  # thin rung was dropped


KALSHI_GAME_PAGE = {"markets": [
    {"event_ticker": "KXNFLGAME-26SEP13BUFKC", "ticker": "…-KC",
     "yes_sub_title": "Kansas City",
     "yes_bid_dollars": "0.55", "yes_ask_dollars": "0.57",
     "close_time": "2026-09-13T23:00:00Z"},
    {"event_ticker": "KXNFLGAME-26SEP13BUFKC", "ticker": "…-BUF",
     "yes_sub_title": "Buffalo",
     "yes_bid_dollars": "0.43", "yes_ask_dollars": "0.45",
     "close_time": "2026-09-13T23:00:00Z"},
], "cursor": None}


def test_kalshi_game_quotes(monkeypatch):
    monkeypatch.setattr(kalshi, "get_json", lambda url, params=None, **kw:
                        KALSHI_GAME_PAGE)
    quotes, report = kalshi.fetch_game_quotes(CFG)
    assert report.n_markets == 2
    kc = next(q for q in quotes if q.extras["team"] == "KC")
    assert kc.is_exchange and kc.event_key == "kalshi:KXNFLGAME-26SEP13BUFKC"
    assert kc.price_american == -127  # mid 0.56


# --- pinnacle -----------------------------------------------------------------

def test_pinnacle_internal_only(monkeypatch):
    def fake_get(url, params=None, headers=None, **kw):
        if url.endswith("matchups"):
            return [{"id": 1, "type": "matchup", "participants": [
                {"alignment": "home", "name": "Kansas City Chiefs"},
                {"alignment": "away", "name": "Buffalo Bills"}],
                "startTime": "2026-09-13T17:00:00Z"}]
        return [{"matchupId": 1, "type": "spread", "period": 0,
                 "isAlternate": False, "prices": [
                     {"designation": "home", "points": -2.5, "price": -104},
                     {"designation": "away", "points": 2.5, "price": -106}]}]
    monkeypatch.setattr(pinnacle, "get_json", fake_get)
    quotes, report = pinnacle.fetch_game_lines(CFG)
    assert report.n_markets == 2
    assert all(q.redistributable is False for q in quotes)
    assert {q.line for q in quotes} == {-2.5}  # home-framed both sides


FD_TEAM_FUTURES = {"pageProps": {"headerProps": {"team": {"teamFutures": [
    {"marketType": "REGULAR_SEASON_WINS_SGP",
     "description": "Buffalo Bills - Regular Season Wins 2026-27",
     "text": "Buffalo Bills Over 10.5 Wins", "odds": "-125"},
    {"marketType": "REGULAR_SEASON_WINS_SGP",
     "description": "Buffalo Bills - Regular Season Wins 2026-27",
     "text": "Buffalo Bills Under 10.5 Wins", "odds": "+105"},
    # H2H variant must be skipped
    {"marketType": "REGULAR_SEASON_WINS_SGP",
     "description": "Miami Dolphins To Have More Regular Season Wins Than",
     "text": "Buffalo Bills", "odds": "-300"},
]}}}}


def test_fanduel_team_page_win_totals(monkeypatch):
    monkeypatch.setattr(fanduel, "discover_build_id", lambda sport, slug: "B1")
    monkeypatch.setattr(fanduel, "get_json",
                        lambda url, **kw: FD_TEAM_FUTURES)
    quotes, report = fanduel.fetch_win_totals(CFG, {"BUF": "buffalo-bills"})
    assert report.ok and report.n_markets == 2
    over = next(q for q in quotes if q.side == "over")
    assert (over.event_key, over.line, over.price_american) == \
        ("BUF", 10.5, -125)
    assert over.source_ids["build_id"] == "B1"


def test_fanduel_team_page_no_build_id_reports(monkeypatch):
    monkeypatch.setattr(fanduel, "discover_build_id", lambda s, g: None)
    quotes, report = fanduel.fetch_win_totals(CFG, {"BUF": "buffalo-bills"})
    assert quotes == [] and not report.ok
    assert "build-id" in report.note
