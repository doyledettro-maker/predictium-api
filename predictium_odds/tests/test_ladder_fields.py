"""predictium_odds 0.3.1: Kalshi ladder fields for the NBA props capture
(nba_prediction_model_2026 docs/briefs/predictium_odds_kalshi_ladder_fields_2026-11.md).

The market rows and order book below are recorded from the live API on
2026-11-09, trimmed to the fields that matter, so the parser is tested
against the venue's shapes. The recorded book is self-consistent with
Kalshi's stated equivalence (a YES bid at X is a NO ask at 1 - X, same
size): best NO bid 0.67 x 1.00 is the market's YES ask 0.33 x 1.00.
"""
from __future__ import annotations

import urllib.parse

import pytest

from predictium_odds.books import kalshi
from predictium_odds.books.kalshi import LadderContract, parse_contract

OPEN = {"ticker": "KXNBAGAME-26NOV13PORSAC-SAC", "event_ticker": "KXNBAGAME-26NOV13PORSAC",
        "strike_type": "structured", "floor_strike": None, "yes_sub_title": "Sacramento",
        "yes_bid_dollars": "0.2200", "yes_ask_dollars": "0.3300",
        "no_bid_dollars": "0.6700", "no_ask_dollars": "0.7800",
        "yes_bid_size_fp": "2.00", "yes_ask_size_fp": "1.00",
        "last_price_dollars": "0.3900", "volume_fp": "3.45", "open_interest_fp": "3.45",
        "close_time": "2026-11-16T03:00:00Z", "status": "active", "result": ""}
SETTLED = {"ticker": "KXNBAGAME-26NOV08MIASAC-SAC", "event_ticker": "KXNBAGAME-26NOV08MIASAC",
           "strike_type": "structured", "floor_strike": None, "yes_sub_title": "Sacramento",
           "yes_bid_dollars": "0.0000", "yes_ask_dollars": "1.0000",
           "no_bid_dollars": "0.0000", "no_ask_dollars": "1.0000",
           "yes_bid_size_fp": "0.00", "yes_ask_size_fp": "0.00",
           "last_price_dollars": "0.0100", "volume_fp": "3530498.45",
           "open_interest_fp": "1600385.38", "close_time": "2026-11-09T04:31:50Z",
           "status": "finalized", "result": "no"}
# Worst level first, as the venue lists it: the best bid is the LAST entry.
BOOK = {"ticker": OPEN["ticker"], "orderbook_fp": {
    "no_dollars": [["0.0100", "306416.00"], ["0.6600", "84.33"], ["0.6700", "1.00"]],
    "yes_dollars": [["0.0100", "306649.00"], ["0.2100", "8.00"], ["0.2200", "2.00"]]}}
NEW_FIELDS = ("yes_bid_size", "yes_ask_size", "no_bid_size", "no_ask_size",
              "last_price", "market_status", "result")


class _HTTP(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP Error {code}")
        self.code = code
        self.headers = {}


@pytest.fixture
def venue(monkeypatch):
    """A fake _get routed by path. Records every call. `answers` maps a path
    prefix to a payload, an exception, or a callable(path, params)."""
    monkeypatch.setattr(kalshi, "_sleep", lambda s: None)
    calls, answers = [], {}

    def fake(path, **params):
        calls.append((path, dict(params)))
        for prefix in sorted(answers, key=len, reverse=True):
            if path.startswith(prefix):
                a = answers[prefix]
                if isinstance(a, Exception):
                    raise a
                return a(path, params) if callable(a) else a
        raise AssertionError(f"unexpected request {path}")
    monkeypatch.setattr(kalshi, "_get", fake)
    answers["series/"] = {"series": {"fee_type": "quadratic_with_maker_fees",
                                     "fee_multiplier": 1}}
    return calls, answers


# --- 1. new fields parse, and are None when absent ---------------------------

def test_every_new_field_parses_from_the_recorded_open_market():
    c = parse_contract(OPEN, spread=False)
    assert (c.yes_bid_size, c.yes_ask_size) == (2.0, 1.0)
    assert c.last_price == pytest.approx(0.39)
    assert (c.market_status, c.result) == ("active", "")     # "" kept verbatim
    assert (c.no_bid_size, c.no_ask_size) == (None, None)    # book only


def test_a_settled_market_reads_verbatim():
    c = parse_contract(SETTLED, spread=False)
    assert (c.market_status, c.result) == ("finalized", "no")
    assert c.last_price == pytest.approx(0.01)
    assert (c.yes_bid_size, c.yes_ask_size) == (0.0, 0.0)    # venue sent 0
    assert c.yes_bid is None and c.yes_ask is None           # unchanged rule


def test_each_new_field_is_none_when_the_venue_omits_it():
    bare = {k: v for k, v in OPEN.items()
            if k not in ("yes_bid_size_fp", "yes_ask_size_fp", "last_price_dollars",
                         "status", "result")}
    c = parse_contract(bare, spread=False)
    assert all(getattr(c, f) is None for f in NEW_FIELDS)


def test_non_string_status_or_result_is_none_not_coerced():
    c = parse_contract({**OPEN, "status": 3, "result": None}, spread=False)
    assert (c.market_status, c.result) == (None, None)


@pytest.mark.parametrize("raw,expected", [
    ("0.3900", 0.39), ("0.0000", None), ("1.0000", None), (None, None), ("x", None)])
def test_last_price_follows_the_quote_dollars_convention(raw, expected):
    """Same _dollars rule as yes_bid/yes_ask: outside (0, 1) is no price."""
    c = parse_contract({**OPEN, "last_price_dollars": raw}, spread=False)
    q = parse_contract({**OPEN, "yes_bid_dollars": raw}, spread=False)
    assert c.last_price == q.yes_bid == (pytest.approx(expected) if expected else None)


def test_existing_positional_construction_still_works():
    """New fields are appended with defaults: older callers are unchanged."""
    c = LadderContract("T", "E", "over", 8.5, None, None, None, None, 0.4, 0.45,
                       0.55, 0.6, 1.0, 1.0, None, "s", "greater", 8.5)
    assert all(getattr(c, f) is None for f in NEW_FIELDS)


# --- 2. status filter ----------------------------------------------------------

@pytest.mark.parametrize("status", ["open", "closed", "settled"])
def test_status_values_pass_through_to_the_query(venue, status):
    calls, answers = venue
    answers["markets"] = {"markets": []}
    kalshi.fetch_ladder("KXNBAGAME", sport="nba", status=status)
    assert calls[0][1]["status"] == status


def test_status_none_sends_no_status(venue):
    calls, answers = venue
    answers["markets"] = {"markets": []}
    kalshi.fetch_ladder("KXNBAGAME", sport="nba", status=None)
    assert "status" not in calls[0][1]


def test_the_default_is_still_open(venue):
    calls, answers = venue
    answers["markets"] = {"markets": []}
    kalshi.fetch_ladder("KXNBAGAME", sport="nba")
    assert calls[0][1]["status"] == "open"


def test_an_unknown_status_raises_before_any_request(venue):
    calls, _ = venue
    with pytest.raises(ValueError):
        kalshi.fetch_ladder("KXNBAGAME", sport="nba", status="finalized")
    assert calls == []


# --- 3. with_sizes -------------------------------------------------------------

def test_with_sizes_fills_no_side_from_the_book(venue):
    calls, answers = venue
    answers["markets"] = {"markets": [OPEN]}
    answers["markets/orderbooks"] = {"orderbooks": [BOOK]}
    got, rep = kalshi.fetch_ladder("KXNBAGAME", sport="nba", with_sizes=True)
    c = got[0]
    assert (c.no_bid_size, c.no_ask_size) == (1.0, 2.0)   # best by PRICE, not index
    assert (c.yes_bid_size, c.yes_ask_size) == (2.0, 1.0)
    assert rep.ok and rep.note == ""
    path = next(p for p, _ in calls if p.startswith("markets/orderbooks"))
    assert urllib.parse.parse_qs(path.split("?", 1)[1]) == {"tickers": [OPEN["ticker"]]}


def test_without_sizes_no_book_is_read(venue):
    calls, answers = venue
    answers["markets"] = {"markets": [OPEN]}
    got, _ = kalshi.fetch_ladder("KXNBAGAME", sport="nba")
    assert not any(p.startswith("markets/orderbooks") for p, _ in calls)
    assert got[0].no_bid_size is None


def test_tickers_go_as_repeated_keys_in_batches_of_100(venue):
    """Comma-joining is answered 200 with one bogus empty book."""
    calls, answers = venue
    rows = [{**OPEN, "ticker": f"KXNBAGAME-X-T{i:03d}"} for i in range(150)]
    answers["markets"] = {"markets": rows}
    answers["markets/orderbooks"] = lambda path, params: {"orderbooks": [
        {"ticker": t, "orderbook_fp": BOOK["orderbook_fp"]}
        for t in urllib.parse.parse_qs(path.split("?", 1)[1])["tickers"]]}
    got, rep = kalshi.fetch_ladder("KXNBAGAME", sport="nba", with_sizes=True)
    batches = [urllib.parse.parse_qs(p.split("?", 1)[1])["tickers"]
               for p, _ in calls if p.startswith("markets/orderbooks")]
    assert [len(b) for b in batches] == [100, 50]
    assert all("," not in t for b in batches for t in b)
    assert all(c.no_bid_size == 1.0 for c in got) and rep.note == ""


def test_a_failed_size_read_keeps_the_capture_with_none_sizes(venue):
    _, answers = venue
    answers["markets"] = {"markets": [OPEN]}
    answers["markets/orderbooks"] = _HTTP(500)
    got, rep = kalshi.fetch_ladder("KXNBAGAME", sport="nba", with_sizes=True)
    assert rep.ok and len(got) == 1
    assert (got[0].no_bid_size, got[0].no_ask_size) == (None, None)
    assert got[0].yes_bid_size == 2.0                      # market object's own
    assert "order book sizes unread for 1 of 1" in rep.note and "500" in rep.note


def test_an_empty_book_side_is_none_not_zero(venue):
    _, answers = venue
    answers["markets"] = {"markets": [OPEN]}
    answers["markets/orderbooks"] = {"orderbooks": [
        {"ticker": OPEN["ticker"], "orderbook_fp": {"no_dollars": [], "yes_dollars": []}}]}
    got, _ = kalshi.fetch_ladder("KXNBAGAME", sport="nba", with_sizes=True)
    assert (got[0].no_bid_size, got[0].no_ask_size) == (None, None)


def test_the_book_fills_only_a_missing_yes_size(venue):
    _, answers = venue
    answers["markets"] = {"markets": [{k: v for k, v in OPEN.items()
                                       if k not in ("yes_bid_size_fp", "yes_ask_size_fp")}]}
    answers["markets/orderbooks"] = {"orderbooks": [BOOK]}
    got, _ = kalshi.fetch_ladder("KXNBAGAME", sport="nba", with_sizes=True)
    assert (got[0].yes_bid_size, got[0].yes_ask_size) == (2.0, 1.0)


# --- 4. fetch_market: three states, and not-found is FAILED --------------------

def test_fetch_market_ok(venue):
    calls, answers = venue
    answers["markets/"] = {"market": SETTLED}
    c, rep = kalshi.fetch_market(SETTLED["ticker"], sport="nba")
    assert rep.ok and rep.n_markets == 1
    assert (c.ticker, c.market_status, c.result) == (SETTLED["ticker"], "finalized", "no")
    assert calls[0][0] == f"markets/{SETTLED['ticker']}"
    assert c.fee_type is None                    # fees are a series read


def test_fetch_market_empty(venue):
    _, answers = venue
    answers["markets/"] = {}
    c, rep = kalshi.fetch_market("KXNBAGAME-X-Y", sport="nba")
    assert c is None and rep.ok and "without a market" in rep.note


def test_fetch_market_not_found_is_failed_not_empty(venue):
    _, answers = venue
    answers["markets/"] = _HTTP(404)
    c, rep = kalshi.fetch_market("KXNBAGAME-NOPE-XYZ", sport="nba")
    assert c is None and not rep.ok and "404 not found" in rep.note


def test_fetch_market_failed_on_other_errors(venue):
    _, answers = venue
    answers["markets/"] = _HTTP(500)
    c, rep = kalshi.fetch_market("KXNBAGAME-X-Y", sport="nba")
    assert c is None and not rep.ok and "500" in rep.note


def test_fetch_market_an_unreadable_strike_is_failed(venue):
    _, answers = venue
    answers["markets/"] = {"market": {**OPEN, "strike_type": "between", "floor_strike": 3}}
    c, rep = kalshi.fetch_market(OPEN["ticker"], sport="nba")
    assert c is None and not rep.ok and "unsupported strike" in rep.note


# --- 0.3.2: 5xx retried on every _get_page path ---------------------------------

def _flaky(fail_times, code, then):
    """An answer that raises HTTP `code` `fail_times` times, then returns `then`."""
    state = {"n": 0}

    def answer(path, params):
        state["n"] += 1
        if state["n"] <= fail_times:
            raise _HTTP(code)
        return then
    return answer, state


def test_the_order_book_read_rides_the_5xx_retry(venue, monkeypatch):
    waits = []
    monkeypatch.setattr(kalshi, "_sleep", waits.append)
    _, answers = venue
    answers["markets"] = {"markets": [OPEN]}
    book, state = _flaky(1, 503, {"orderbooks": [BOOK]})
    answers["markets/orderbooks"] = book
    got, rep = kalshi.fetch_ladder("KXNBAGAME", sport="nba", with_sizes=True)
    assert state["n"] == 2 and 2 in waits
    assert got[0].no_bid_size == 1.0 and rep.note == ""


def test_fetch_market_rides_the_5xx_retry(venue, monkeypatch):
    monkeypatch.setattr(kalshi, "_sleep", lambda s: None)
    _, answers = venue
    one, state = _flaky(2, 502, {"market": SETTLED})
    answers["markets/"] = one
    c, rep = kalshi.fetch_market(SETTLED["ticker"], sport="nba")
    assert state["n"] == 3 and rep.ok and c.result == "no"


def test_fetch_market_persistent_5xx_is_failed(venue, monkeypatch):
    monkeypatch.setattr(kalshi, "_sleep", lambda s: None)
    _, answers = venue
    answers["markets/"] = _HTTP(504)
    c, rep = kalshi.fetch_market("KXNBAGAME-X-Y", sport="nba")
    assert c is None and not rep.ok and "HTTP 504 server error" in rep.note


def test_fetch_market_404_is_not_retried(venue, monkeypatch):
    waits = []
    monkeypatch.setattr(kalshi, "_sleep", waits.append)
    calls, answers = venue
    answers["markets/"] = _HTTP(404)
    c, rep = kalshi.fetch_market("KXNBAGAME-NOPE-XYZ", sport="nba")
    assert c is None and not rep.ok and "404 not found" in rep.note
    assert waits == [] and len(calls) == 1
