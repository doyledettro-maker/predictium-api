"""A 429 is congestion, not an answer.

Lifted into the shared client 2026-09-26 from the CFB session's fix, which
was measured on a 53-game Saturday: a burst of paged Kalshi reads drew HTTP
429 within a second, and the shared `_paged` had no retry at all, so a 429 on
any page raised and the caller published a failed SourceReport with no
pricing for that series. Kalshi rate-limits by IP and the Mac mini runs every
sport's pipeline from one IP, so the sports rate-limit each other.

The load-bearing property these tests pin is the LAST one: a read that failed
must never be indistinguishable from a series that genuinely has no markets.
"""
from __future__ import annotations

import pytest

from predictium_odds.books import kalshi
from predictium_odds.books.base import SportConfig

CFG = SportConfig(
    sport="ncaaf",
    resolve_team=lambda s: s,
    event_key=lambda h, a, t: f"{a}@{h}_{t[:10]}",
    kalshi_prefix="KXNCAAF",
)


class _HTTP(Exception):
    """Shaped like urllib's HTTPError: carries .code and .headers."""

    def __init__(self, code, retry_after=None):
        super().__init__(f"HTTP Error {code}")
        self.code = code
        self.headers = {"Retry-After": retry_after} if retry_after else {}


@pytest.fixture
def waits(monkeypatch):
    """Record the sleeps instead of taking them."""
    slept = []
    monkeypatch.setattr(kalshi, "_sleep", slept.append)
    return slept


def _pages(monkeypatch, outcomes):
    """Stub the per-request `_get`: raise or return each outcome in turn.

    A bare list outcome is wrapped as a single final page.
    """
    calls = {"n": 0, "params": []}

    def fake(path, **params):
        calls["params"].append(dict(params))
        out = outcomes[calls["n"]]
        calls["n"] += 1
        if isinstance(out, Exception):
            raise out
        return {"markets": out} if isinstance(out, list) else out

    monkeypatch.setattr(kalshi, "_get", fake)
    return calls


def test_a_429_is_retried_with_backoff_then_succeeds(monkeypatch, waits):
    calls = _pages(monkeypatch, [_HTTP(429), _HTTP(429), [{"ticker": "X"}]])
    out = kalshi._paged("markets", "markets", series_ticker="KXNCAAFSPREAD")
    assert out == [{"ticker": "X"}]
    assert waits == [2, 4]
    assert calls["n"] == 3


def test_a_429_mid_pagination_retries_that_page_not_the_series(monkeypatch, waits):
    """The failure of CFB's first fix: restarting from page one re-walks the
    pages that drew the limit. Page 2 is refused once; pages 1 and 3 must be
    fetched exactly once each and the cursor must carry through."""
    calls = _pages(monkeypatch, [
        {"markets": [{"t": 1}], "cursor": "c2"},
        _HTTP(429),
        {"markets": [{"t": 2}], "cursor": "c3"},
        {"markets": [{"t": 3}]},
    ])
    out = kalshi._paged("markets", "markets", series_ticker="KXNCAAFSPREAD")
    assert [m["t"] for m in out] == [1, 2, 3]
    cursors = [p.get("cursor") for p in calls["params"]]
    assert cursors == [None, "c2", "c2", "c3"]   # only the refused page twice
    assert waits.count(2) == 1                   # one backoff, not a restart


def test_retry_after_is_honoured_over_our_backoff(monkeypatch, waits):
    _pages(monkeypatch, [_HTTP(429, retry_after="7"), []])
    kalshi._paged("markets", "markets", series_ticker="KXNCAAFTOTAL")
    assert waits == [7.0]


def test_a_malformed_retry_after_falls_back_to_our_backoff(monkeypatch, waits):
    """Never let a junk header become the wait: float('soon') must not raise
    out of the retry path."""
    _pages(monkeypatch, [_HTTP(429, retry_after="soon"), []])
    kalshi._paged("markets", "markets", series_ticker="KXNCAAFTOTAL")
    assert waits == [2]


def test_any_other_error_is_raised_at_once(monkeypatch, waits):
    _pages(monkeypatch, [_HTTP(500)])
    with pytest.raises(_HTTP):
        kalshi._paged("markets", "markets", series_ticker="KXNCAAFGAME")
    assert waits == []                  # a retry is for congestion only


def test_a_429_that_never_clears_still_raises(monkeypatch, waits):
    """It must reach the caller, which turns it into SourceReport(ok=False).
    Swallowing it into [] would publish as 'no markets in this series'."""
    _pages(monkeypatch, [_HTTP(429)] * 6)
    with pytest.raises(_HTTP):
        kalshi._paged("markets", "markets", series_ticker="KXNCAAFSPREAD")
    assert waits == list(kalshi.RETRY_BACKOFF)


def test_pagination_is_bounded(monkeypatch, waits):
    """A venue that always returns a cursor must not loop forever."""
    _pages(monkeypatch, [{"markets": [{}], "cursor": "again"}] * kalshi.MAX_PAGES)
    out = kalshi._paged("markets", "markets", series_ticker="S")
    assert len(out) == kalshi.MAX_PAGES


def test_pages_are_paced_but_a_single_page_is_not(monkeypatch, waits):
    """Pacing exists because we share the IP, but it must not tax the common
    case of a series that fits in one page."""
    _pages(monkeypatch, [{"markets": [{"t": 1}], "cursor": "c2"},
                         {"markets": [{"t": 2}]}])
    kalshi._paged("markets", "markets", series_ticker="S")
    assert waits == [kalshi.PAGE_PACING_SECONDS]

    waits.clear()
    _pages(monkeypatch, [[{"t": 1}]])
    kalshi._paged("markets", "markets", series_ticker="S")
    assert waits == []


def test_caller_params_are_not_mutated_by_pagination(monkeypatch, waits):
    """`_paged` used to write the cursor into the caller's own kwargs dict."""
    _pages(monkeypatch, [{"markets": [], "cursor": "c2"}, {"markets": []}])
    params = {"series_ticker": "S"}
    kalshi._paged("markets", "markets", **params)
    assert params == {"series_ticker": "S"}


# --- the distinction that matters ---------------------------------------------

def test_rate_limited_read_reports_not_ok_while_a_real_empty_reports_ok(
        monkeypatch, waits):
    """A failed read and an empty series must not look alike downstream.

    Both return zero quotes. The difference is carried by SourceReport.ok, so
    a pipeline can tell "Kalshi did not answer" from "Kalshi has no markets
    here", and only the first is a reason to distrust the slate.
    """
    _pages(monkeypatch, [_HTTP(429)] * 6)
    quotes, report = kalshi.fetch_game_quotes(CFG)
    assert quotes == []
    assert report.ok is False
    assert "429" in (report.note or "")

    _pages(monkeypatch, [[]])
    quotes, report = kalshi.fetch_game_quotes(CFG)
    assert quotes == []
    assert report.ok is True          # we asked, and the answer was "none"


def test_the_failure_note_names_rate_limiting_explicitly(monkeypatch, waits):
    """An operator reading the health line must be able to tell WHY.

    The note used to be `f"{series}: {e}"`, which relies on the exception
    stringifying its own code. urllib's HTTPError happens to; a wrapped or
    bare re-raise need not, and the note then read "KXNCAAFGAME: " with
    nothing after the colon.
    """
    class _Bare(Exception):
        def __init__(self):
            super().__init__()
            self.code = 429
            self.headers = {}

    _pages(monkeypatch, [_Bare()] * 6)
    _, report = kalshi.fetch_game_quotes(CFG)
    assert report.ok is False
    assert "429" in report.note
    assert "rate limited" in report.note
    assert report.note.rstrip().endswith("retries")


def test_a_non_http_failure_still_names_something(monkeypatch, waits):
    """A bare exception with no message must not produce an empty note."""
    _pages(monkeypatch, [TimeoutError()])
    _, report = kalshi.fetch_game_quotes(CFG)
    assert report.ok is False
    assert "TimeoutError" in report.note
