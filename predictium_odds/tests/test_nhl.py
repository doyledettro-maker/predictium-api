"""NHL through the shared layer (0.3.3). Fixtures only, no network.

Every Kalshi row below copies the SHAPE seen live on 2026-11-17 (ticker,
event ticker, strike type, floor, yes_sub_title, rules text, series fee
terms). Prices are synthetic round numbers: no venue or book price is
committed. Book fixtures (FanDuel, Bovada, Pinnacle) use the market type and
name strings seen live on FanDuel's NHL page the same day, also with
synthetic prices.
"""

import pytest

import predictium_odds.books.bovada as bovada
import predictium_odds.books.fanduel as fanduel
import predictium_odds.books.kalshi as kalshi
import predictium_odds.books.pinnacle as pinnacle
from predictium_odds.books.base import SportConfig
from predictium_odds.books.kalshi import NHL_SERIES, parse_contract
from predictium_odds.oddsmath import kalshi_ev_at_ask, kalshi_taker_fee

TEAMS = {"Calgary Flames": "CGY", "San Jose Sharks": "SJS",
         "Calgary": "CGY", "San Jose": "SJS"}

CFG = SportConfig(
    sport="nhl",
    resolve_team=lambda s: TEAMS.get(s),
    event_key=lambda h, a, t: f"{a}@{h}_{t[:10]}",
    bovada_path="/hockey/nhl",
    fanduel_page_id="nhl",
    kalshi_prefix="KXNHL",
)


def _k(ticker, event, stype, floor, sub, rules, bid="0.4000", ask="0.4500"):
    return {"ticker": ticker, "event_ticker": event, "strike_type": stype,
            "floor_strike": floor, "yes_sub_title": sub,
            "yes_bid_dollars": bid, "yes_ask_dollars": ask,
            "rules_primary": rules, "status": "active",
            "close_time": "2026-11-20T03:00:00Z"}


GAME = [
    _k("KXNHLGAME-26NOV23MINSJ-SJ", "KXNHLGAME-26NOV23MINSJ", "structured",
       None, "San Jose",
       "If San Jose wins the Minnesota vs San Jose NHL game originally "
       "scheduled for Nov 23, 2026, then the market resolves to Yes.",
       bid="0.3300", ask="0.3500"),
    _k("KXNHLGAME-26NOV23MINSJ-MIN", "KXNHLGAME-26NOV23MINSJ", "structured",
       None, "Minnesota",
       "If Minnesota wins the Minnesota vs San Jose NHL game originally "
       "scheduled for Nov 23, 2026, then the market resolves to Yes.",
       bid="0.6400", ask="0.6600"),
]
SPREAD = [
    _k("KXNHLSPREAD-26NOV19DALVGK-VGK2", "KXNHLSPREAD-26NOV19DALVGK",
       "greater", 1.5, "Vegas wins by over 1.5 goals",
       "If Vegas wins by over 1.5 goals in the Dallas vs Vegas NHL game "
       "originally scheduled for Nov 19, 2026, then the market resolves to Yes."),
    _k("KXNHLSPREAD-26NOV19DALVGK-DAL2", "KXNHLSPREAD-26NOV19DALVGK",
       "greater", 1.5, "Dallas wins by over 1.5 goals",
       "If Dallas wins by over 1.5 goals in the Dallas vs Vegas NHL game "
       "originally scheduled for Nov 19, 2026, then the market resolves to Yes."),
]
TOTAL = [
    _k(f"KXNHLTOTAL-26NOV19DALVGK-{n}", "KXNHLTOTAL-26NOV19DALVGK", "greater",
       n - 0.5, f"Over {n - 0.5:g} goals scored",
       f"If the teams collectively score more than {n - 0.5:g} goals in the "
       "Dallas vs Vegas NHL game originally scheduled for Nov 19, 2026, then "
       "the market resolves to Yes.")
    for n in (7, 6, 5)
]
TEAM_TOTAL = [
    _k(f"KXNHLTEAMTOTAL-26NOV18COLUTA-{code}{n}",
       "KXNHLTEAMTOTAL-26NOV18COLUTA", "greater", n - 0.5,
       f"{team} over {n - 0.5:g}",
       f"If {team} scores over {n - 0.5:g} goals in the Colorado vs Utah NHL "
       "game originally scheduled for Nov 18, 2026, then the market resolves "
       "to Yes.")
    for code, team in (("UTA", "Utah"), ("COL", "Colorado")) for n in (4, 3)
]
FEES = {"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1}


def test_the_series_reference_names_the_four_verified_series():
    assert NHL_SERIES == {"game": "KXNHLGAME", "spread": "KXNHLSPREAD",
                          "total": "KXNHLTOTAL",
                          "team_total": "KXNHLTEAMTOTAL"}
    assert all(s.startswith(CFG.kalshi_prefix) for s in NHL_SERIES.values())


# --- strikes, one per series ------------------------------------------------

def test_game_winner_is_a_named_outcome():
    c = parse_contract(GAME[0], spread=False, fees=FEES)
    assert (c.kind, c.outcome, c.over_line) == ("outcome", "San Jose", None)
    assert c.rules_primary.startswith("If San Jose wins")
    assert (c.fee_type, c.fee_multiplier) == ("quadratic_with_maker_fees", 1)


def test_puck_line_is_team_oriented_with_the_favourite_negative():
    vgk, dal = (parse_contract(r, spread=True) for r in SPREAD)
    assert (vgk.kind, vgk.team_code, vgk.spread_line) == ("spread", "VGK", -1.5)
    assert (dal.team_code, dal.spread_line, dal.subject) == ("DAL", -1.5, "Dallas")
    assert vgk.yes_means == "VGK -1.5"


def test_total_goals_floor_is_the_over_line_and_has_no_team():
    c = parse_contract(TOTAL[0], spread=False)
    assert (c.kind, c.over_line, c.team_code, c.subject) == ("over", 6.5, None, None)
    assert c.yes_means == "over 6.5"


NAMES = {"UTA": "Utah", "COL": "Colorado"}   # the NHL repo's own table, in use


def _tt(row, **kw):
    return parse_contract(row, spread=False, team_total=True,
                          team_names=kw.pop("team_names", NAMES), **kw)


def test_team_total_carries_its_team_so_the_two_ladders_stay_apart():
    got = [_tt(r) for r in TEAM_TOTAL]
    assert [(c.team_code, c.subject, c.over_line) for c in got] == [
        ("UTA", "Utah", 3.5), ("UTA", "Utah", 2.5),
        ("COL", "Colorado", 3.5), ("COL", "Colorado", 2.5)]
    assert got[0].yes_means == "Utah over 3.5"
    assert all(c.kind == "over" and c.spread_line is None for c in got)


def test_without_the_flag_a_team_total_parses_exactly_as_before():
    c = parse_contract(TEAM_TOTAL[0], spread=False)
    assert (c.team_code, c.subject, c.over_line) == (None, None, 3.5)


def test_the_flag_needs_the_name_table():
    with pytest.raises(ValueError):
        parse_contract(TEAM_TOTAL[0], spread=False, team_total=True)


def _variant(ticker_suffix=None, sub=None, floor=None, event=None):
    row = dict(TEAM_TOTAL[0])          # UTA4, "Utah over 3.5", floor 3.5
    if ticker_suffix is not None:
        row["ticker"] = "KXNHLTEAMTOTAL-26NOV18COLUTA-" + ticker_suffix
    if sub is not None:
        row["yes_sub_title"] = sub
    if floor is not None:
        row["floor_strike"] = floor
    if event is not None:
        row["event_ticker"] = event
    return row


# Each case below parsed cleanly at 2e010cff (review of #30).
@pytest.mark.parametrize("row, why", [
    (_variant(sub="Colorado over 3.5"), "subtitle names the other team"),
    (_variant(ticker_suffix="COL4"), "code is the other team, subtitle Utah"),
    (_variant(ticker_suffix="BOS4", sub="Boston over 3.5"), "team not in the event"),
    (_variant(ticker_suffix="UTA9"), "rung is not floor + 0.5"),
    (_variant(sub="Anything at all over 3.5"), "subtitle is not the team"),
    (_variant(ticker_suffix="4"), "no team code in the ticker"),
    (_variant(sub="Utah: 4+"), "a prop-style subtitle"),
    (_variant(event="KXNHLTEAMTOTAL-COLUTA"), "no matchup segment in the event"),
    (_variant(ticker_suffix="ARI4", sub="Arizona over 3.5"),
     "code in neither end of the matchup"),
])
def test_a_team_total_fails_closed(row, why):
    with pytest.raises(kalshi.StrikeParseError):
        _tt(row,
            team_names={**NAMES, "BOS": "Boston", "ARI": "Arizona"})


def test_a_code_missing_from_the_name_table_raises():
    with pytest.raises(kalshi.StrikeParseError):
        _tt(TEAM_TOTAL[0], team_names={"COL": "Colorado"})


def test_a_team_total_whose_subtitle_disagrees_with_its_strike_still_raises():
    bad = dict(TEAM_TOTAL[0], yes_sub_title="Utah over 4.5")  # floor is 3.5
    with pytest.raises(kalshi.StrikeParseError):
        _tt(bad)


def test_team_total_and_spread_together_raise():
    with pytest.raises(kalshi.StrikeParseError):
        parse_contract(TEAM_TOTAL[0], spread=True, team_total=True,
                       team_names=NAMES)


def test_wins_by_text_on_a_non_spread_contract_raises():
    with pytest.raises(kalshi.StrikeParseError):
        parse_contract(SPREAD[0], spread=False)


@pytest.mark.parametrize("ticker, event, sub, floor", [
    ("KXNBATEAMTOTAL-26NOV18BOSNYK-BOS115", "KXNBATEAMTOTAL-26NOV18BOSNYK",
     "Boston over 114.5", 114.5),
    ("KXATPGAMES-26NOV18SINALC-SIN22", "KXATPGAMES-26NOV18SINALC",
     "Sinner over 21.5", 21.5),
])
def test_other_sports_are_untouched_without_the_flag(ticker, event, sub, floor):
    c = parse_contract(_k(ticker, event, "greater", floor, sub, ""), spread=False)
    assert (c.team_code, c.subject, c.over_line) == (None, None, floor)


def test_props_and_totals_keep_their_old_subject_rules():
    prop = _k("KXNHLPTS-26NOV18COLUTA-MAKAR2", "KXNHLPTS-26NOV18COLUTA",
              "greater", 1.5, "Cale Makar: 2+", "")
    c = parse_contract(prop, spread=False)
    assert (c.subject, c.team_code, c.over_line) == ("Cale Makar", None, 1.5)


# --- fetch_ladder / fetch_game_quotes, the three read outcomes ---------------

class _HTTP(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP Error {code}")
        self.code = code
        self.headers = {}


@pytest.fixture
def venue(monkeypatch):
    monkeypatch.setattr(kalshi, "_sleep", lambda s: None)
    seen = []

    def install(rows_or_error):
        def fake(path, **params):
            if path.startswith("series/"):
                return {"series": dict(FEES)}
            seen.append(params)
            if isinstance(rows_or_error, Exception):
                raise rows_or_error
            return {"markets": rows_or_error}
        monkeypatch.setattr(kalshi, "_get", fake)
        return seen
    return install


@pytest.mark.parametrize("series, rows, kind, extra", [
    ("KXNHLSPREAD", SPREAD, "spread", {}),
    ("KXNHLTOTAL", TOTAL, "over", {}),
    ("KXNHLTEAMTOTAL", TEAM_TOTAL, "over",
     {"team_total": True, "team_names": {"UTA": "Utah", "COL": "Colorado"}}),
])
def test_each_ladder_series_captures_with_fees(venue, series, rows, kind, extra):
    seen = venue(rows)
    got, rep = kalshi.fetch_ladder(series, sport="nhl", **extra)
    assert rep.ok and rep.n_markets == len(rows)
    assert seen[0]["series_ticker"] == series
    assert {c.kind for c in got} == {kind}
    assert all(c.fee_type == "quadratic_with_maker_fees" for c in got)
    if extra:
        assert {c.team_code for c in got} == {"UTA", "COL"}


def test_fetch_ladder_team_total_needs_names_before_any_request(venue):
    seen = venue(TEAM_TOTAL)
    with pytest.raises(ValueError):
        kalshi.fetch_ladder("KXNHLTEAMTOTAL", sport="nhl", team_total=True)
    assert seen == []


@pytest.mark.parametrize("series", sorted(NHL_SERIES.values()))
def test_a_failed_read_is_none_never_an_empty_list(venue, series):
    venue(_HTTP(404))
    got, rep = kalshi.fetch_ladder(series, sport="nhl")
    assert got is None and rep.ok is False


@pytest.mark.parametrize("series", sorted(NHL_SERIES.values()))
def test_an_empty_read_is_an_empty_list_and_ok(venue, series):
    venue([])
    got, rep = kalshi.fetch_ladder(series, sport="nhl")
    assert got == [] and rep.ok


def test_game_quotes_come_from_kxnhlgame(monkeypatch):
    calls = []

    def fake(url, params=None, **kw):
        calls.append(params)
        return {"markets": GAME, "cursor": None}
    monkeypatch.setattr(kalshi, "get_json", fake)
    quotes, rep = kalshi.fetch_game_quotes(CFG)
    assert rep.ok and rep.n_markets == 2
    assert calls[0]["series_ticker"] == "KXNHLGAME"
    sj = next(q for q in quotes if q.extras["team"] == "SJS")
    assert sj.is_exchange and sj.event_key == "kalshi:KXNHLGAME-26NOV23MINSJ"


# --- fees and EV on an NHL contract -------------------------------------------

def test_kxnhlgame_fee_and_ev_use_the_series_terms():
    # 1 x 0.07 x 0.35 x 0.65 = 0.015925 (exact model fee, $0.000001 precision)
    fee = kalshi_taker_fee(0.35, "quadratic_with_maker_fees", 1)
    assert fee == pytest.approx(0.015925, abs=1e-9)
    ev = kalshi_ev_at_ask(0.40, 0.35, fee_type="quadratic_with_maker_fees",
                          fee_multiplier=1)
    assert ev.cost == pytest.approx(0.365925, abs=1e-9)
    assert ev.ev_per_contract == pytest.approx(0.40 - 0.365925, abs=1e-9)
    # one contract through an FCM pays the cent rounding: cost 0.37
    aligned = kalshi_ev_at_ask(0.40, 0.35, fee_type="quadratic_with_maker_fees",
                               fee_multiplier=1, cent_aligned=True)
    assert aligned.cost == pytest.approx(0.37, abs=1e-9)
    # no fee terms, no EV claim
    assert kalshi_ev_at_ask(0.40, 0.35, fee_type=None, fee_multiplier=None) is None


# --- books ---------------------------------------------------------------------

FD_NHL = {"attachments": {
    "events": {"1": {"eventId": 1, "name": "Calgary Flames @ San Jose Sharks",
                     "openDate": "2026-11-18T03:30:00.000Z"}},
    "markets": {
        "m1": {"marketId": "m1", "eventId": 1, "marketType": "MONEY_LINE",
               "marketName": "Moneyline", "runners": [
                   {"runnerName": "Calgary Flames", "handicap": 0,
                    "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": 110}}},
                   {"runnerName": "San Jose Sharks", "handicap": 0,
                    "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": -130}}}]},
        "m2": {"marketId": "m2", "eventId": 1,
               "marketType": "MATCH_HANDICAP_(2-WAY)", "marketName": "Puck Line",
               "runners": [
                   {"runnerName": "Calgary Flames", "handicap": 1.5,
                    "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": -200}}},
                   {"runnerName": "San Jose Sharks", "handicap": -1.5,
                    "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": 165}}}]},
        "m3": {"marketId": "m3", "eventId": 1,
               "marketType": "TOTAL_POINTS_(OVER/UNDER)", "marketName": "Total Goals",
               "runners": [
                   {"runnerName": "Over", "handicap": 5.5,
                    "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": -120}}},
                   {"runnerName": "Under", "handicap": 5.5,
                    "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": 100}}}]},
        # a season future on the same lobby: TEAM_TOTAL_POINTS_SGP is standings
        # points, not a game team total, and must not become a game quote
        "m4": {"marketId": "m4", "eventId": 1, "marketType": "TEAM_TOTAL_POINTS_SGP",
               "marketName": "Calgary Flames - O/U Regular Season Points",
               "runners": [{"runnerName": "Calgary Flames Over 85.5 Points",
                            "handicap": 0,
                            "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": -110}}}]},
    }}}


def test_fanduel_nhl_moneyline_puck_line_and_total_goals(monkeypatch):
    monkeypatch.setattr(fanduel, "get_json", lambda url, params=None, **kw: FD_NHL)
    quotes, rep = fanduel.fetch_game_lines(CFG)
    assert rep.ok and rep.n_markets == 6
    by = {(q.market, q.side): q for q in quotes}
    assert by[("moneyline", "home")].price_american == -130
    assert by[("spread", "home")].line == -1.5          # SJS -1.5
    assert by[("spread", "away")].line == -1.5          # home-framed both sides
    assert by[("total", "over")].line == 5.5
    assert all(q.event_key == "CGY@SJS_2026-11-18" for q in quotes)


BOVADA_NHL = [{"events": [{
    "link": "/hockey/nhl/x", "startTime": 1795318200000,
    "competitors": [{"home": True, "name": "San Jose Sharks"},
                    {"home": False, "name": "Calgary Flames"}],
    "displayGroups": [{"markets": [
        {"description": "Moneyline", "period": {"description": "Game"},
         "outcomes": [{"description": "San Jose Sharks", "price": {"american": "-130"}},
                      {"description": "Calgary Flames", "price": {"american": "+110"}}]},
        {"description": "Puck Line", "period": {"description": "Game"},
         "outcomes": [{"description": "San Jose Sharks",
                       "price": {"american": "+165", "handicap": "-1.5"}},
                      {"description": "Calgary Flames",
                       "price": {"american": "-200", "handicap": "1.5"}}]},
        {"description": "Total", "period": {"description": "Game"},
         "outcomes": [{"description": "Over", "price": {"american": "-120", "handicap": "5.5"}},
                      {"description": "Under", "price": {"american": "EVEN", "handicap": "5.5"}}]},
        {"description": "Puck Line", "period": {"description": "1st Period"},
         "outcomes": [{"description": "San Jose Sharks",
                       "price": {"american": "+300", "handicap": "-1.5"}}]},
    ]}]}]}]


def test_bovada_nhl_puck_line_parses_and_periods_are_dropped(monkeypatch):
    monkeypatch.setattr(bovada, "get_json", lambda url, **kw: BOVADA_NHL)
    quotes, rep = bovada.fetch_game_lines(CFG)
    assert rep.ok and rep.n_markets == 6
    by = {(q.market, q.side): q for q in quotes}
    assert by[("spread", "home")].line == -1.5
    assert by[("spread", "away")].line == -1.5
    assert by[("total", "under")].price_american == 100


def test_pinnacle_nhl_has_a_league_and_stays_internal_only(monkeypatch):
    urls = []

    def fake(url, params=None, headers=None, **kw):
        urls.append(url)
        if url.endswith("matchups"):
            return [{"id": 7, "type": "matchup", "participants": [
                {"alignment": "home", "name": "San Jose Sharks"},
                {"alignment": "away", "name": "Calgary Flames"}],
                "startTime": "2026-11-18T03:30:00Z"}]
        return [{"matchupId": 7, "type": "moneyline", "period": 0,
                 "isAlternate": False, "prices": [
                     {"designation": "home", "price": -125},
                     {"designation": "away", "price": 108}]}]
    monkeypatch.setattr(pinnacle, "get_json", fake)
    quotes, rep = pinnacle.fetch_game_lines(CFG)
    assert pinnacle.LEAGUE_IDS["nhl"] == 1456
    assert all("/leagues/1456/" in u for u in urls)
    assert rep.n_markets == 2 and quotes
    assert all(q.redistributable is False for q in quotes)
