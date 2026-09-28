"""Pricing the strike that is offered (Doyle, 2026-09-27).

Every model prices Kalshi "k+" rungs and half-point ladders, and Novig x.5
lines, from its own distribution and settles at the traded strike. These pin
the three shared pieces: reading a Kalshi strike without guessing, EV at the
fee-inclusive ask, and pricing an offered strike from a model's own ladder.

The rows below are copied from live /markets responses on 2026-09-28, so the
parser is tested against the venue's shapes rather than our memory of them.
"""
from __future__ import annotations

import math

import pytest

from predictium_odds.books import kalshi
from predictium_odds.books.kalshi import (
    LadderContract,
    StrikeParseError,
    parse_contract,
)
from predictium_odds.lines import kplus_to_over_line, price_at_strike
from predictium_odds.oddsmath import (
    FeeInclusiveEV,
    ev_at_ask,
    exchange_fee,
    kalshi_ev_at_ask,
)


def _row(ticker, stype, floor, sub, *, bid="0.4000", ask="0.4500",
         event=None, **extra):
    return {"ticker": ticker, "event_ticker": event or ticker.rsplit("-", 1)[0],
            "strike_type": stype, "floor_strike": floor, "yes_sub_title": sub,
            "yes_bid_dollars": bid, "yes_ask_dollars": ask,
            "no_bid_dollars": "0.5500", "no_ask_dollars": "0.6000",
            "volume_fp": "3527.37", "open_interest_fp": "2561.58",
            "close_time": "2026-09-30T00:20:00Z", **extra}


NFL_SPREAD = _row("KXNFLSPREAD-26SEP27LARDEN-LAR28", "greater", 27.5,
                  "LA Rams wins by over 27.5 points", bid="0.0000", ask="0.1800")
NFL_TOTAL = _row("KXNFLTOTAL-26OCT04DETCAR-71", "greater", 70.5,
                 "Over 70.5 points")
MLB_KS = _row("KXMLBKS-26SEP292000BOSNYY-NYYCSCHLITTLER31-9", "greater", 8.5,
              "Cam Schlittler: 9+", event="KXMLBKS-26SEP292000BOSNYY")
NFL_WINS = _row("KXNFLWINS-27IND-9", "greater_or_equal", 9, "9+ wins")
NFL_GAME = _row("KXNFLGAME-26OCT05ATLNO-NO", "structured", None, "New Orleans",
                custom_strike={"football_team": "f7c2cd06"})
SOCCER_SPREAD = _row("KXEPLSPREAD-26OCT10SUNBRI-BRI2", "greater", 1.5,
                     "Brighton wins by more than 1.5 goals")


# --- strike parsing --------------------------------------------------------

def test_a_greater_half_point_floor_is_the_over_line_itself():
    """The double-adjust trap: 70.5 is 'Over 70.5', not 'Over 70'."""
    c = parse_contract(NFL_TOTAL, spread=False)
    assert (c.kind, c.over_line, c.yes_means) == ("over", 70.5, "over 70.5")


def test_k_plus_is_over_k_minus_half_on_a_greater_contract():
    c = parse_contract(MLB_KS, spread=False)
    assert c.over_line == 8.5 == kplus_to_over_line(9)
    assert c.subject == "Cam Schlittler"
    assert c.yes_means == "Cam Schlittler over 8.5"


def test_greater_or_equal_on_an_integer_is_over_n_minus_half():
    c = parse_contract(NFL_WINS, spread=False)
    assert c.over_line == 8.5


def test_spread_is_team_oriented_with_negative_for_the_favourite():
    c = parse_contract(NFL_SPREAD, spread=True)
    assert c.kind == "spread"
    assert (c.team_code, c.over_line, c.spread_line) == ("LAR", 27.5, -27.5)
    assert c.subject == "LA Rams"
    assert c.yes_means == "LAR -27.5"


def test_soccer_spread_wording_parses():
    c = parse_contract(SOCCER_SPREAD, spread=True)
    assert (c.team_code, c.spread_line) == ("BRI", -1.5)


def test_structured_is_a_named_outcome_with_no_strike():
    c = parse_contract(NFL_GAME, spread=False)
    assert (c.kind, c.outcome, c.over_line) == ("outcome", "New Orleans", None)


def test_prices_and_depth_are_carried_and_an_empty_side_is_none():
    c = parse_contract(NFL_SPREAD, spread=True)
    assert c.yes_bid is None            # "0.0000": nobody bidding
    assert c.yes_ask == 0.18 and c.no_bid == 0.55 and c.no_ask == 0.60
    assert c.volume == pytest.approx(3527.37)
    assert c.open_interest == pytest.approx(2561.58)
    assert c.close_time == "2026-09-30T00:20:00Z"


@pytest.mark.parametrize("row", [
    _row("KX-A-9", "greater", 9, "Over 9 points"),          # push semantics
    _row("KX-A-9", "greater_or_equal", 8.5, "Over 8.5"),     # unseen shape
    _row("KX-A-9", "between", 8.5, "8 to 9", cap_strike=9.5),
    _row("KX-A-9", "less", 8.5, "Under 8.5"),
    _row("KX-A-9", "greater", None, "Over ?"),
    _row("KX-A-9", None, 8.5, "Over 8.5"),
])
def test_an_unsupported_strike_raises_rather_than_guesses(row):
    with pytest.raises(StrikeParseError):
        parse_contract(row, spread=False)


def test_a_subtitle_that_disagrees_with_the_strike_raises():
    """'9+' on a greater 9.5 floor would mean one of them is wrong."""
    with pytest.raises(StrikeParseError, match="disagrees"):
        parse_contract(_row("KX-A-10", "greater", 9.5, "Player: 9+"),
                       spread=False)
    with pytest.raises(StrikeParseError, match="disagrees"):
        parse_contract(_row("KX-A-71", "greater", 70.5, "Over 71.5 points"),
                       spread=False)


def test_a_spread_without_a_team_code_raises():
    with pytest.raises(StrikeParseError):
        parse_contract(_row("KXNFLSPREAD-26SEP27LARDEN-28", "greater", 27.5,
                            "LA Rams wins by over 27.5 points"), spread=True)
    with pytest.raises(StrikeParseError):
        parse_contract(_row("KXNFLSPREAD-X-LAR28", "greater", 27.5,
                            "Over 27.5 points"), spread=True)


# --- fetch_ladder: the three read outcomes ---------------------------------

class _HTTP(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP Error {code}")
        self.code = code
        self.headers = {}


@pytest.fixture
def pages(monkeypatch):
    monkeypatch.setattr(kalshi, "_sleep", lambda s: None)
    seen = []

    def install(*outcomes):
        it = iter(outcomes)

        def fake(path, **params):
            seen.append(params)
            out = next(it)
            if isinstance(out, Exception):
                raise out
            return {"markets": out}
        monkeypatch.setattr(kalshi, "_get", fake)
        return seen
    return install


def test_capture_returns_every_contract_sorted(pages):
    rows = [_row(f"KXNFLTOTAL-E-{n}", "greater", n - 0.5, f"Over {n - 0.5:g} points",
                 event="KXNFLTOTAL-E") for n in (71, 45, 48)]
    pages(rows)
    got, rep = kalshi.fetch_ladder("KXNFLTOTAL", sport="nfl")
    assert rep.ok and rep.n_markets == 3 and rep.note == ""
    assert [c.over_line for c in got] == [44.5, 47.5, 70.5]
    assert all(isinstance(c, LadderContract) for c in got)


def test_the_event_filter_is_sent_to_the_venue(pages):
    seen = pages([])
    kalshi.fetch_ladder("KXNFLSPREAD", "KXNFLSPREAD-26SEP27LARDEN", sport="nfl")
    assert seen[0]["event_ticker"] == "KXNFLSPREAD-26SEP27LARDEN"
    assert seen[0]["series_ticker"] == "KXNFLSPREAD"


def test_spread_is_inferred_from_the_series_name(pages):
    pages([NFL_SPREAD])
    got, _ = kalshi.fetch_ladder("KXNFLSPREAD", sport="nfl")
    assert got[0].kind == "spread"


def test_an_empty_read_is_an_empty_list_and_ok(pages):
    pages([])
    got, rep = kalshi.fetch_ladder("KXEPLSPREAD", sport="soccer")
    assert got == [] and rep.ok


def test_a_failed_read_is_none_and_not_ok(pages):
    pages(*([_HTTP(429)] * (len(kalshi.RETRY_BACKOFF) + 1)))
    got, rep = kalshi.fetch_ladder("KXNFLTOTAL", sport="nfl")
    assert got is None and not rep.ok and "429" in rep.note


def test_strict_raises_on_one_bad_strike(pages):
    pages([NFL_TOTAL, _row("KX-A-9", "between", 8.5, "8 to 9")])
    with pytest.raises(StrikeParseError):
        kalshi.fetch_ladder("KXNFLTOTAL", sport="nfl")


def test_lenient_keeps_the_good_and_names_the_bad(pages):
    pages([NFL_TOTAL, _row("KX-BAD-9", "between", 8.5, "8 to 9")])
    got, rep = kalshi.fetch_ladder("KXNFLTOTAL", sport="nfl", strict=False)
    assert [c.ticker for c in got] == [NFL_TOTAL["ticker"]]
    assert rep.ok and "1 unparseable" in rep.note and "KX-BAD-9" in rep.note


# --- fee-inclusive EV -------------------------------------------------------

def test_kalshi_fee_formula():
    assert exchange_fee(0.50) == pytest.approx(0.0175)
    assert exchange_fee(0.10) == pytest.approx(0.0063)
    assert exchange_fee(0.50, 100) == pytest.approx(1.75)


def test_round_up_per_contract_and_per_order():
    assert exchange_fee(0.50, round_up=True) == 0.02
    assert exchange_fee(0.50, 100, round_up=True) == 1.75   # exact cents
    assert exchange_fee(0.50, 3, round_up=True) == 0.06     # 5.25c -> 6c
    assert exchange_fee(0.01, round_up=True) == 0.01


def test_ev_at_ask_includes_the_fee_in_the_price_paid():
    ev = kalshi_ev_at_ask(0.60, 0.52)
    fee = 0.07 * 0.52 * 0.48
    assert ev.fee == pytest.approx(fee)
    assert ev.cost == pytest.approx(0.52 + fee)
    assert ev.ev_per_contract == pytest.approx(0.60 - 0.52 - fee)
    assert ev.breakeven == ev.cost


def test_to_win_one_unit_risk_is_at_the_fee_inclusive_price():
    ev = kalshi_ev_at_ask(0.60, 0.52, round_up=True)      # cost 0.54
    assert ev.cost == pytest.approx(0.54)
    assert ev.risk_to_win_1u == pytest.approx(0.54 / 0.46)
    # expected units: win +1 with p, lose risk with 1-p
    assert ev.ev_units == pytest.approx(0.60 * 1 - 0.40 * (0.54 / 0.46))


def test_a_fee_can_turn_a_bare_edge_negative():
    assert ev_at_ask(0.51, 0.50).ev_per_contract > 0
    assert kalshi_ev_at_ask(0.51, 0.50).ev_per_contract < 0


def test_round_up_on_a_bigger_order_drags_less_per_contract():
    one = kalshi_ev_at_ask(0.6, 0.5, round_up=True).fee
    hundred = kalshi_ev_at_ask(0.6, 0.5, contracts=100, round_up=True).fee
    assert one == pytest.approx(0.02) and hundred == pytest.approx(0.0175)


@pytest.mark.parametrize("p,ask,fee", [(1.2, 0.5, 0), (0.5, 0.0, 0),
                                       (0.5, 1.0, 0), (0.5, 0.99, 0.02),
                                       (0.5, 0.5, -0.01)])
def test_ev_at_ask_rejects_impossible_inputs(p, ask, fee):
    with pytest.raises(ValueError):
        ev_at_ask(p, ask, fee)


def test_fee_ev_is_a_frozen_record():
    ev = kalshi_ev_at_ask(0.6, 0.5)
    assert isinstance(ev, FeeInclusiveEV)
    with pytest.raises(AttributeError):
        ev.p = 0.7  # type: ignore[misc]


# --- pricing an offered strike from the model's ladder ---------------------

OVER = {44.5: 0.62, 45.5: 0.58, 46.5: 0.55, 47.5: 0.49}


def test_on_grid_returns_the_models_own_p():
    assert price_at_strike(OVER, 46.5) == 0.55


def test_off_grid_without_a_continuous_form_is_none_not_nearest():
    assert price_at_strike(OVER, 46.0) is None
    assert price_at_strike(OVER, 46.4) is None


def test_outside_the_grid_is_none_even_with_a_continuous_form():
    cdf = lambda x: 0.5
    assert price_at_strike(OVER, 43.5, continuous=cdf) is None
    assert price_at_strike(OVER, 48.5, continuous=cdf) is None


def test_continuous_form_is_used_only_inside_the_grid():
    surv = lambda x: 1 - 1 / (1 + math.exp(-(x - 47) / 3))
    assert price_at_strike(OVER, 46.0, continuous=surv) == pytest.approx(surv(46.0))
    assert price_at_strike(OVER, 45.5, continuous=surv) == 0.58   # grid wins


def test_an_increasing_ladder_is_accepted():
    """A team spread ladder keyed by spread line rises as the line loosens."""
    team = {-7.5: 0.41, -3.5: 0.55, -2.5: 0.60}
    assert price_at_strike(team, -3.5) == 0.55


def test_a_non_monotone_or_invalid_ladder_raises():
    with pytest.raises(ValueError, match="monotone"):
        price_at_strike({44.5: 0.6, 45.5: 0.62, 46.5: 0.5}, 45.5)
    with pytest.raises(ValueError):
        price_at_strike({44.5: 1.2}, 44.5)
    with pytest.raises(ValueError):
        price_at_strike({}, 44.5)


def test_a_kalshi_rung_prices_end_to_end():
    """KXMLBKS '9+' at a 0.31 ask against a model that says 36% over 8.5."""
    c = parse_contract(MLB_KS, spread=False)
    p = price_at_strike({7.5: 0.52, 8.5: 0.36, 9.5: 0.23}, c.over_line)
    ev = kalshi_ev_at_ask(p, 0.31)
    assert p == 0.36 and ev.ev_per_contract > 0
