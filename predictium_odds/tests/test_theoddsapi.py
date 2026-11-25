"""The Odds API client (spec docs/THE_ODDS_API_CLIENT_SPEC.md, tests 1-13).

No test touches the network. Requests go to a fake opener (or, for the
redirect test, a server on 127.0.0.1). The key is a sentinel and the plan
size is made up: neither the real key nor the real plan belongs in a repo.
"""
from __future__ import annotations

import hashlib
import http.client
import http.server
import io
import json
import logging
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import warnings
from typing import ClassVar

import pytest

from predictium_odds.books import PROVIDERS
from predictium_odds.books import theoddsapi as toa
from predictium_odds.books.base import SportConfig

SENTINEL = "SENTINEL-KEY-7Qx9Zp"
MADE_UP_PLAN = 1000          # not the real plan size
TEAMS = {"Buffalo Bills": "BUF", "Kansas City Chiefs": "KC",
         "Miami Dolphins": "MIA", "New York Jets": "NYJ"}
CFG = SportConfig(
    sport="nfl",
    resolve_team=TEAMS.get,
    event_key=lambda h, a, t: f"{a}@{h}_{t[:10]}",
    extras={"theoddsapi_sport": "americanfootball_nfl"},
)
FUTURE = "2099-09-13T17:00:00Z"
PAST = "2001-09-13T17:00:00Z"


def event(home="Kansas City Chiefs", away="Buffalo Bills", start=FUTURE,
          books=None, eid="ev1"):
    if books is None:
        books = [bookmaker("draftkings")]
    return {"id": eid, "sport_key": "americanfootball_nfl",
            "commence_time": start, "home_team": home, "away_team": away,
            "bookmakers": books}


def bookmaker(key, home="Kansas City Chiefs", away="Buffalo Bills",
              spread=-3.5, total=47.5, ml=(-170, 145)):
    return {"key": key, "title": key.title(), "last_update": "2099-09-12T00:00:00Z",
            "markets": [
                {"key": "h2h", "outcomes": [{"name": home, "price": ml[0]},
                                            {"name": away, "price": ml[1]}]},
                {"key": "spreads", "outcomes": [
                    {"name": home, "price": -110, "point": spread},
                    {"name": away, "price": -110, "point": -spread}]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "price": -108, "point": total},
                    {"name": "Under", "price": -112, "point": total}]},
            ]}


class FakeResp:
    def __init__(self, status=200, body=b"[]", headers=None, read_exc=None):
        self.status = status
        self.headers = headers if headers is not None else {}
        self._body = body
        self._read_exc = read_exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return self.status

    def read(self):
        if self._read_exc is not None:
            raise self._read_exc
        return self._body


def ok(body, last=3, remaining=900, extra=None):
    h = {"x-requests-last": str(last), "x-requests-used": "100",
         "x-requests-remaining": str(remaining)}
    h.update(extra or {})
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return FakeResp(200, raw, h)


def http_error(req, code, headers=None):
    hdrs = {"x-requests-last": "0"}
    hdrs.update(headers or {})
    return urllib.error.HTTPError(req.full_url, code, f"err {req.full_url}",
                                  hdrs, io.BytesIO(b""))


class Net:
    """A scripted fake opener. Each script entry is a FakeResp, an exception,
    or a callable(req) returning either."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.lock = threading.Lock()

    def open(self, req, timeout=None):
        with self.lock:
            self.requests.append(req.full_url)
            out = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if callable(out) and not isinstance(out, FakeResp):
            out = out(req)
        if isinstance(out, BaseException):
            raise out
        return out

    @property
    def n(self):
        return len(self.requests)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv(toa.KEY_ENV, SENTINEL)
    monkeypatch.setenv(toa.MONTHLY_ENV, str(MADE_UP_PLAN))
    monkeypatch.delenv(toa.FLOOR_ENV, raising=False)
    toa._reset_for_tests()
    toa._ACCOUNT.remaining = 900
    yield
    toa._reset_for_tests()


@pytest.fixture
def waits(monkeypatch):
    slept = []
    monkeypatch.setattr(toa, "_sleep", slept.append)
    return slept


@pytest.fixture
def net(monkeypatch):
    def install(*script):
        n = Net(script)
        monkeypatch.setattr(toa, "_opener", lambda: n)
        return n
    return install


def budget(cap=100, job="test-job"):
    return toa.CreditBudget(job=job, cap=cap)


def blob(read) -> str:
    return repr(read) + repr(read.report) + json.dumps(read.provenance, default=str)


# ---------------------------------------------------------------- test 1

def _failure_modes():
    url_in = SENTINEL  # every injected exception carries the key in its text
    return {
        "301": lambda r: http_error(r, 301, {"Location": r.full_url}),
        "401": lambda r: http_error(r, 401),
        "429": lambda r: http_error(r, 429),
        "500": lambda r: http_error(r, 500),
        "503": lambda r: http_error(r, 503),
        "timeout": lambda r: TimeoutError(f"timed out {r.full_url}"),
        "dns": lambda r: urllib.error.URLError(
            socket.gaierror(-2, f"Name or service not known {r.full_url}")),
        "reset-mid-body": lambda r: FakeResp(
            200, headers={"x-requests-last": "3"},
            read_exc=ConnectionResetError(f"reset {r.full_url}")),
        "incomplete-read": lambda r: FakeResp(
            200, headers={"x-requests-last": "3"},
            read_exc=http.client.IncompleteRead(f"partial {url_in}".encode())),
        "invalid-json": lambda r: ok(f"{{not json {url_in}".encode()),
        "non-utf8": lambda r: ok(b"\xff\xfe\x00" + url_in.encode()),
    }


@pytest.mark.parametrize("mode", sorted(_failure_modes()))
def test_1_the_key_never_leaves(mode, net, waits, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    n = net(_failure_modes()[mode])
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")
        read = toa.read_odds(CFG, budget(cap=1000), keep_payload=False)
        quotes, report = toa.fetch_game_lines(CFG, budget(cap=1000))
    assert n.n >= 1 and SENTINEL in n.requests[0]   # it WAS sent, to the API
    assert read.status == "failed" and read.quotes is None
    assert quotes == [] and report.ok is False
    out = capsys.readouterr()
    for text in (blob(read), repr(report), out.out, out.err, caplog.text,
                 " ".join(str(w.message) for w in warned)):
        assert SENTINEL not in text, mode
        assert "apiKey" not in text, mode


def _chain(e, seen=None):
    seen = seen or set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        yield e
        for nxt in (e.__cause__, e.__context__):
            if nxt is not None:
                yield from _chain(nxt, seen)
        e = None


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_1_interrupts_propagate_without_the_key(exc, net, waits):
    """Review nit 1: BaseException escapes `except Exception`. It must
    leave with no frame local and no chained exception holding the key."""
    def boom(req):
        try:
            raise ValueError(f"inner {req.full_url}")
        except ValueError:
            raise exc(f"stop {req.full_url[:0]}")
    net(boom)
    with pytest.raises(exc) as info:
        toa.read_odds(CFG, budget())
    e = info.value
    for link in _chain(e):
        assert SENTINEL not in repr(link) and SENTINEL not in str(link.args)
    tb = traceback.TracebackException.from_exception(e, capture_locals=True)
    assert SENTINEL not in "".join(tb.format())


def test_1_no_key_means_failed_without_a_request(monkeypatch, net):
    monkeypatch.delenv(toa.KEY_ENV)
    n = net(ok([event()]))
    read = toa.read_odds(CFG, budget())
    assert read.status == "failed" and n.n == 0
    assert "no API key in environment" in read.report.note


def test_1_redirect_disabled_handlers_are_quiet():
    opener = toa._opener()
    assert any(isinstance(h, toa._NoRedirect) for h in opener.handlers)
    for h in opener.handlers:
        if isinstance(h, (urllib.request.HTTPHandler, urllib.request.HTTPSHandler)):
            assert h._debuglevel == 0


# ---------------------------------------------------------------- test 2

class _Redirector(http.server.BaseHTTPRequestHandler):
    hits: ClassVar[list] = []

    def do_GET(self):
        type(self).hits.append(self.path)
        if self.path.startswith("/v4/sports/americanfootball_nfl/odds"):
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"[]")

    def log_message(self, *a):
        pass


def test_2_a_redirect_is_never_followed(monkeypatch):
    _Redirector.hits = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Redirector)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        monkeypatch.setattr(toa, "BASE", f"http://127.0.0.1:{srv.server_port}")
        read = toa.read_odds(CFG, budget())
    finally:
        srv.shutdown()
        srv.server_close()
    assert len(_Redirector.hits) == 1            # no second request
    assert read.status == "failed"
    assert "unexpected redirect" in read.report.note


# ---------------------------------------------------------------- test 3

def test_3_cap_refuses_before_the_network(net):
    n = net(ok([event()]))
    b = budget(cap=2)                          # 3 markets x 1 region = 3
    read = toa.read_odds(CFG, b)
    assert read.status == "failed" and n.n == 0
    assert "credit cap" in read.report.note
    assert b.refused == 1 and b.spent == 0


def test_3_every_retry_reserves_its_own_estimate(net, waits):
    n = net(lambda r: http_error(r, 503, {"x-requests-last": "3"}))
    b = budget(cap=7)
    read = toa.read_odds(CFG, b)
    assert n.n == 2                            # 3 + 3 spent, 1 left < 3
    assert b.spent == 6 and b.refused == 1
    assert read.status == "failed" and "credit cap" in read.report.note
    assert waits == [2, 4]


@pytest.mark.parametrize("cap", [0, -1, 1.5, True, False, float("nan"),
                                 float("inf"), "5", None, 10.0])
def test_3_bad_caps_raise_at_construction(cap):
    with pytest.raises(ValueError):
        toa.CreditBudget(job="j", cap=cap)


@pytest.mark.parametrize("job", ["", "  ", None, 3])
def test_3_bad_job_raises(job):
    with pytest.raises(ValueError):
        toa.CreditBudget(job=job, cap=5)


# ---------------------------------------------------------------- test 4

def test_4_cost_estimates():
    e = toa.estimate_cost
    assert e("odds", ("h2h", "spreads", "totals"), ("us",)) == 3
    assert e("odds", ("h2h",), ("us", "uk", "eu")) == 3
    assert e("odds", ("h2h", "totals"), ("us", "uk")) == 4
    ten = tuple(f"b{i}" for i in range(10))
    assert e("odds", ("h2h", "spreads"), ("us",), ten) == 2           # 1 region
    assert e("odds", ("h2h", "spreads"), ("us",), ten + ("b10",)) == 4  # 2
    assert e("event_odds", ("h2h", "player_points"), ("us",)) == 2   # upper bound
    assert e("historical_odds", ("h2h", "spreads"), ("us",)) == 20
    assert e("historical_odds", ("h2h",), ("us",), ten + ("b10",)) == 20
    assert e("sports", (), ()) == 0
    assert e("scores", ("h2h",), ("us",)) is None
    assert toa.COST_TABLE_CHECKED


def test_4_unknown_endpoint_is_refused(net):
    n = net(ok([]))
    call = toa._request("/v4/sports/x/scores", {}, "scores", ("h2h",), ("us",),
                        None, "x", budget(), toa.log)
    assert call.note == "endpoint not in cost table" and n.n == 0


def test_4_bookmakers_param_replaces_regions(net):
    n = net(ok([event()]))
    toa.read_odds(CFG, budget(), bookmakers=("draftkings", "fanduel"))
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(n.requests[0]).query)
    assert q["bookmakers"] == ["draftkings,fanduel"] and "regions" not in q


# ---------------------------------------------------------------- test 5

@pytest.mark.parametrize("hdr", [None, "abc", "", "-4", "nan"])
def test_5_unusable_last_header_counts_the_estimate(hdr, net, caplog):
    h = {"x-requests-remaining": "800"}
    if hdr is not None:
        h["x-requests-last"] = hdr
    net(FakeResp(200, json.dumps([event()]).encode(), h))
    b = budget()
    with caplog.at_level(logging.WARNING, logger="predictium_odds.theoddsapi"):
        read = toa.read_odds(CFG, b)
    assert read.status == "captured"
    assert b.spent == 3
    assert "no usable x-requests-last" in caplog.text


def test_5_actual_above_estimate_warns_and_overshoot_stands(net, caplog):
    net(ok([event()], last=5))
    b = budget(cap=3)
    with caplog.at_level(logging.WARNING, logger="predictium_odds.theoddsapi"):
        first = toa.read_odds(CFG, b)
        second = toa.read_odds(CFG, b)
    assert first.status == "captured"          # the overshooting call stands
    assert b.spent == 5
    assert "above estimate" in caplog.text
    assert second.status == "failed" and "credit cap" in second.report.note
    free = toa._request("/v4/sports", {}, "sports", (), (), None, "-", b, toa.log)
    assert free.note and "credit cap" in free.note   # every later call refused


def test_5_missing_remaining_lowers_the_cache(net):
    """Review nit 2: the floor must not go stale-high."""
    toa._ACCOUNT.remaining = 500
    net(FakeResp(200, json.dumps([event()]).encode(), {"x-requests-last": "3"}))
    toa.read_odds(CFG, budget())
    assert toa._ACCOUNT.remaining == 497
    net(FakeResp(200, b"[]", {}))               # no headers at all
    toa.read_odds(CFG, budget())
    assert toa._ACCOUNT.remaining == 494       # the estimate, 3


def test_5_header_remaining_wins(net):
    net(ok([event()], remaining=321))
    toa.read_odds(CFG, budget())
    assert toa._ACCOUNT.remaining == 321


# ---------------------------------------------------------------- test 6

def test_6_threads_never_pass_the_cap(net):
    def slow(req):
        time.sleep(0.01)
        return ok([event()], last=3)
    n = net(slow)
    b = budget(cap=30)
    reads = []
    lock = threading.Lock()

    def go():
        r = toa.read_odds(CFG, b)
        with lock:
            reads.append(r)
    ts = [threading.Thread(target=go) for _ in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert n.n == 10 and b.spent == 30 and b.requests == 10
    assert b.refused == 10
    assert sum(r.status == "captured" for r in reads) == 10
    assert toa._ACCOUNT.reserved == 0


# ---------------------------------------------------------------- test 7

def test_7_floor_refuses_and_warns(net, caplog):
    n = net(ok([event()]))
    toa._ACCOUNT.remaining = 202                 # floor 0.20 x 1000 = 200
    with caplog.at_level(logging.WARNING, logger="predictium_odds.theoddsapi"):
        read = toa.read_odds(CFG, budget(job="floorjob"))
    assert read.status == "failed" and read.report.ok is False and n.n == 0
    assert "account floor" in read.report.note
    assert ("theoddsapi account-floor job=floorjob remaining=202 estimate=3 "
            "floor=200") in caplog.text


def test_7_exactly_at_the_floor_is_allowed(net):
    n = net(ok([event()]))
    toa._ACCOUNT.remaining = 203
    assert toa.read_odds(CFG, budget()).status == "captured" and n.n == 1


def test_7_floor_fraction_from_env(monkeypatch, net):
    monkeypatch.setenv(toa.FLOOR_ENV, "0.5")
    n = net(ok([event()]))
    toa._ACCOUNT.remaining = 502
    assert toa.read_odds(CFG, budget()).status == "failed" and n.n == 0
    monkeypatch.setenv(toa.FLOOR_ENV, "0")
    toa._ACCOUNT.remaining = 3
    assert toa.read_odds(CFG, budget()).status == "captured"


def test_7_in_flight_reservations_count():
    toa._ACCOUNT.remaining = 260
    toa._ACCOUNT.reserved = 58                   # 260 - 58 - 3 = 199 < 200
    assert toa._floor_refusal(budget(), 3, toa.log)
    toa._ACCOUNT.reserved = 57
    assert toa._floor_refusal(budget(), 3, toa.log) is None
    assert toa._ACCOUNT.reserved == 60


@pytest.mark.parametrize("plan,frac", [(None, None), ("", None), ("abc", None),
                                       ("0", None), ("-5", None),
                                       (str(MADE_UP_PLAN), "1.0"),
                                       (str(MADE_UP_PLAN), "-0.1"),
                                       (str(MADE_UP_PLAN), "nan"),
                                       (str(MADE_UP_PLAN), "x")])
def test_7_floor_not_configured(monkeypatch, net, plan, frac):
    if plan is None:
        monkeypatch.delenv(toa.MONTHLY_ENV)
    else:
        monkeypatch.setenv(toa.MONTHLY_ENV, plan)
    if frac is not None:
        monkeypatch.setenv(toa.FLOOR_ENV, frac)
    n = net(ok([event()]))
    read = toa.read_odds(CFG, budget())
    assert read.status == "failed" and n.n == 0
    assert "account floor not configured" in read.report.note


def test_7_unknown_remaining_is_probed_on_the_free_endpoint(net):
    toa._ACCOUNT.remaining = None

    def route(req):
        if urllib.parse.urlsplit(req.full_url).path == "/v4/sports":
            return FakeResp(200, b"[]", {"x-requests-last": "0",
                                         "x-requests-remaining": "700"})
        return ok([event()], remaining=697)
    n = net(route)
    b = budget()
    read = toa.read_odds(CFG, b)
    assert read.status == "captured" and n.n == 2
    assert urllib.parse.urlsplit(n.requests[0]).path == "/v4/sports"
    assert b.spent == 3


def test_7_failed_probe_refuses(net):
    toa._ACCOUNT.remaining = None
    n = net(lambda r: http_error(r, 500))
    read = toa.read_odds(CFG, budget())
    assert read.status == "failed" and n.n == 1
    assert "account floor: remaining unknown" in read.report.note


# ---------------------------------------------------------------- test 8

def test_8_429_then_success(net, waits):
    n = net(lambda r: http_error(r, 429), lambda r: http_error(r, 429),
            ok([event()]))
    read = toa.read_odds(CFG, budget())
    assert read.status == "captured" and n.n == 3 and waits == [2, 4]


def test_8_retry_after_honoured_only_in_range(net, waits):
    net(lambda r: http_error(r, 503, {"Retry-After": "7"}), ok([event()]))
    toa.read_odds(CFG, budget())
    assert waits == [7.0]
    for bad in ("61", "0", "-1", "nan", "soon"):
        waits.clear()
        net(lambda r, b=bad: http_error(r, 503, {"Retry-After": b}), ok([event()]))
        toa.read_odds(CFG, budget())
        assert waits == [2], bad


@pytest.mark.parametrize("code", [401, 403, 404, 422])
def test_8_client_errors_are_not_retried(code, net, waits):
    n = net(lambda r: http_error(r, code))
    read = toa.read_odds(CFG, budget())
    assert n.n == 1 and waits == []
    assert read.status == "failed" and f"HTTP {code}" in read.report.note


def test_8_persistent_5xx_ends_failed(net, waits):
    n = net(lambda r: http_error(r, 502))
    read = toa.read_odds(CFG, budget())
    assert n.n == 5 and waits == [2, 4, 8, 16]
    assert read.status == "failed" and "HTTP 502 server error" in read.report.note


# ---------------------------------------------------------------- test 9

def test_9_no_events_is_empty(net):
    net(ok([]))
    read = toa.read_odds(CFG, budget())
    assert read.status == "empty" and read.quotes == [] and read.report.ok
    assert "no events listed" in read.report.note


def test_9_all_in_play_is_empty(net):
    net(ok([event(start=PAST), event(start=PAST, eid="ev2")]))
    read = toa.read_odds(CFG, budget())
    assert read.status == "empty" and read.report.ok
    assert "in-play" in read.report.note


def test_9_all_unresolved_is_failed(net):
    net(ok([event(home="Nowhere FC", away="Buffalo Bills"),
            event(home="Kansas City Chiefs", away="Atlantis", eid="ev2")]))
    read = toa.read_odds(CFG, budget())
    assert read.status == "failed" and read.quotes is None and not read.report.ok
    assert "2 listed, 0 priced" in read.report.note
    assert "2 unresolved team" in read.report.note


def test_9_all_bookmakers_unknown_is_failed(net):
    net(ok([event(books=[bookmaker("someoffshorebook")])]))
    read = toa.read_odds(CFG, budget())
    assert read.status == "failed" and "1 unknown bookmaker" in read.report.note


def test_9_no_bookmakers_yet_is_empty_not_failed(net):
    """Portfolio ruling (b): events listed before books post lines are a
    normal EMPTY; present-but-unresolved stays FAILED."""
    net(ok([event(books=[]), event(books=[], eid="ev2", home="New York Jets",
                                   away="Miami Dolphins")]))
    read = toa.read_odds(CFG, budget())
    assert read.status == "empty" and read.quotes == [] and read.report.ok
    assert "2 events listed, no bookmaker prices yet" in read.report.note

    net(ok([event(books=[]), event(start=PAST, eid="ev2")]))
    read = toa.read_odds(CFG, budget())
    assert read.status == "empty" and read.report.ok


def test_9_no_bookmakers_beside_an_unresolved_row_is_failed(net):
    """One broken alias next to an unposted event is still our tables
    breaking: FAILED, never hidden behind the normal case."""
    net(ok([event(books=[]), event(home="Nowhere FC", eid="ev2")]))
    read = toa.read_odds(CFG, budget())
    assert read.status == "failed" and not read.report.ok
    assert "1 unresolved team" in read.report.note
    net(ok([event(books=[]), event(books=[bookmaker("mystery")], eid="ev2",
                                   home="New York Jets", away="Miami Dolphins")]))
    read = toa.read_odds(CFG, budget())
    assert read.status == "failed" and "1 unknown bookmaker" in read.report.note


def test_9_partial_drops_still_capture(net):
    net(ok([event(), event(home="Nowhere FC", eid="ev2"),
            event(books=[bookmaker("draftkings"), bookmaker("mystery")], eid="ev3",
                  home="New York Jets", away="Miami Dolphins")]))
    read = toa.read_odds(CFG, budget())
    assert read.status == "captured" and read.report.ok
    assert read.report.n_markets == 2
    assert "1 unresolved team" in read.report.note
    assert "1 unknown bookmaker" in read.report.note


def test_9_fetch_game_lines_shapes(net):
    net(ok([event()]))
    quotes, report = toa.fetch_game_lines(CFG, budget())
    assert len(quotes) == 6 and report.ok and report.book == "theoddsapi"
    net(lambda r: http_error(r, 401))
    quotes, report = toa.fetch_game_lines(CFG, budget())
    assert quotes == [] and report.ok is False


def test_9_registered_as_a_provider():
    assert PROVIDERS["theoddsapi"] is toa


# --------------------------------------------------------------- test 10

def test_10_pinnacle_is_internal_and_others_display(net):
    net(ok([event(books=[bookmaker("pinnacle"), bookmaker("draftkings"),
                         bookmaker("Pinnacle")])]))
    read = toa.read_odds(CFG, budget())
    by = {}
    for q in read.quotes:
        by.setdefault(q.book, set()).add((q.redistributable, q.extras["license_class"]))
    assert by["theoddsapi:pinnacle"] == {(False, "internal_only")}
    assert by["theoddsapi:draftkings"] == {(True, "display_in_model_payloads")}
    assert "theoddsapi:Pinnacle" not in by          # case variant: unknown
    assert "1 unknown bookmaker" in read.report.note


def test_10_historical_rows_are_all_internal(net):
    snap = {"timestamp": "2099-09-10T12:00:00Z",
            "previous_timestamp": "2099-09-10T11:55:00Z",
            "next_timestamp": "2099-09-10T12:05:00Z",
            "data": [event(books=[bookmaker("draftkings"), bookmaker("fanduel")]),
                     event(start="2099-09-10T11:00:00Z", eid="ev2",
                           home="New York Jets", away="Miami Dolphins")]}
    n = net(ok(snap, last=30))
    b = budget(cap=30)
    read = toa.read_historical_odds(CFG, b, "2099-09-10T12:00:00Z")
    assert read.status == "captured" and b.spent == 30
    assert all(q.redistributable is False for q in read.quotes)
    assert {q.extras["license_class"] for q in read.quotes} == {"internal_only"}
    # in-play judged against the snapshot time: ev2 started before it
    assert "1 in-play dropped" in read.report.note
    assert "/v4/historical/sports/americanfootball_nfl/odds" in n.requests[0]
    assert "date=2099-09-10T12%3A00%3A00Z" in n.requests[0]


def test_10_historical_cost_is_refused_on_a_small_cap(net):
    n = net(ok({"timestamp": FUTURE, "data": []}))
    read = toa.read_historical_odds(CFG, budget(cap=29), FUTURE)
    assert read.status == "failed" and n.n == 0


# --------------------------------------------------------------- test 11

def test_11_book_tag_and_identity(net):
    net(ok([event()]))
    read = toa.read_odds(CFG, budget())
    assert len(read.quotes) == 6
    q = read.quotes[0]
    assert q.book == "theoddsapi:draftkings" and q.sport == "nfl"
    assert q.event_key == "BUF@KC_2099-09-13"
    assert q.source_ids == {"theoddsapi_event_id": "ev1"}
    assert q.extras == {"source": "theoddsapi", "bookmaker": "draftkings",
                        "bookmaker_title": "Draftkings",
                        "last_update": "2099-09-12T00:00:00Z",
                        "license_class": "display_in_model_payloads"}
    assert q.is_exchange is False and q.ts


def test_11_event_odds_reads_one_event(net):
    n = net(ok(event()))
    read = toa.read_event_odds(CFG, budget(), "ev1")
    assert read.status == "captured" and len(read.quotes) == 6
    assert "/v4/sports/americanfootball_nfl/events/ev1/odds" in n.requests[0]


def test_11_missing_sport_key_fails_without_a_request(net):
    n = net(ok([]))
    cfg = SportConfig(sport="nfl", resolve_team=TEAMS.get,
                      event_key=lambda h, a, t: "k")
    read = toa.read_odds(cfg, budget())
    assert read.status == "failed" and n.n == 0


# --------------------------------------------------------------- test 12

def test_12_in_play_events_drop(net):
    net(ok([event(), event(start=PAST, eid="ev2", home="New York Jets",
                           away="Miami Dolphins")]))
    read = toa.read_odds(CFG, budget())
    assert {q.event_key for q in read.quotes} == {"BUF@KC_2099-09-13"}
    assert "1 in-play dropped" in read.report.note


def test_12_spread_sign_is_betting_convention(net):
    net(ok([event(books=[bookmaker("draftkings", spread=-3.5)])]))
    read = toa.read_odds(CFG, budget())
    sp = {q.side: q.line for q in read.quotes if q.market == "spread"}
    assert sp == {"home": -3.5, "away": 3.5}     # negative = home favoured
    tot = {q.side: q.line for q in read.quotes if q.market == "total"}
    assert tot == {"over": 47.5, "under": 47.5}
    ml = {q.side: q.price_american for q in read.quotes if q.market == "moneyline"}
    assert ml == {"home": -170, "away": 145}


def test_12_requests_american_odds(net):
    n = net(ok([event()]))
    toa.read_odds(CFG, budget())
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(n.requests[0]).query)
    assert q["oddsFormat"] == ["american"]


@pytest.mark.parametrize("bad", [0, 110.5, "abc", None, float("nan"), True])
def test_12_bad_prices_drop(bad, net):
    bm = bookmaker("draftkings")
    bm["markets"][0]["outcomes"][0]["price"] = bad
    net(ok([event(books=[bm])]))
    read = toa.read_odds(CFG, budget())
    assert len(read.quotes) == 5 and "1 bad price" in read.report.note


# --------------------------------------------------------------- test 13

def test_13_provenance(net):
    body = json.dumps([event()]).encode()
    net(FakeResp(200, body, {"x-requests-last": "3", "x-requests-used": "42",
                             "x-requests-remaining": "800"}))
    read = toa.read_odds(CFG, budget(cap=10), keep_payload=True)
    p = read.provenance
    assert p["endpoint"] == "/v4/sports/americanfootball_nfl/odds"
    assert "apiKey" not in p["params"] and p["params"]["oddsFormat"] == "american"
    assert p["http_status"] == 200 and p["fetched_at"]
    assert p["sha256"] == hashlib.sha256(body).hexdigest()
    assert p["credits"] == {"last": 3, "used": 42, "remaining": 800}
    assert read.payload == body
    assert read.report.note.endswith("credits 3 (spent 3/10, remaining 800)")
    assert toa.read_odds(CFG, budget()).payload is None


def test_13_per_request_and_run_total_lines(net, caplog):
    net(ok([event()], remaining=800))
    caplog.set_level(logging.INFO, logger="predictium_odds.theoddsapi")
    with toa.CreditBudget(job="nfl-publish", cap=60) as b:
        toa.read_odds(CFG, b)
        toa.read_odds(CFG, toa.CreditBudget(job="other", cap=1))  # refused
    assert ("theoddsapi job=nfl-publish endpoint=/v4/sports/americanfootball_nfl/odds "
            "sport=americanfootball_nfl status=captured credits_last=3 "
            "spent=3/60 remaining=800") in caplog.text
    line = ("theoddsapi run job=nfl-publish requests=1 spent=3 cap=60 "
            "refused=0 remaining=800")
    assert line in caplog.text and b.summary() == line


def test_13_the_client_writes_nothing(net, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    net(ok([event()]))
    toa.read_odds(CFG, budget(), keep_payload=True)
    assert list(tmp_path.iterdir()) == []


def test_module_is_stdlib_only():
    with open(toa.__file__) as fh:
        src = fh.read()
    for line in src.splitlines():
        if line.startswith(("import ", "from ")):
            mod = line.split()[1].split(".")[0]
            assert mod in sys.stdlib_module_names or mod in (
                "predictium_odds", "__future__"), line
