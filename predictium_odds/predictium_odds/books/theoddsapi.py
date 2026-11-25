"""The Odds API: one shared client for every sport (spec
docs/THE_ODDS_API_CLIENT_SPEC.md, approved 2026-11-25).

Authorized fleet-wide on 2026-11-21 for live and ongoing ingestion as well
as historical pulls (handover §3). It is the one keyed odds source in the
package, so four controls live here and nowhere else:

1. The key (D2). It is read from THE_ODDS_API_KEY inside `_send`, the one
   function that builds a URL, and the locals holding it are cleared in a
   `finally`. NO Exception ever leaves this module: every entry point turns
   any failure into a FAILED read whose note is built from fixed phrases,
   the HTTP status and the exception's class name, never from `str(e)`,
   `e.url` or `e.reason`, because urllib puts the URL (and so the key) in
   all of those. `raise ... from None` would not be enough: `__context__`
   survives it. Redirects are refused (a redirect resends the URL to a
   host we did not pick) and the HTTP handlers run at debuglevel 0.
   KeyboardInterrupt and SystemExit do propagate; they leave with their
   traceback and context cleared and the key-bearing locals emptied.

2. Credits (D3). Every call needs a CreditBudget. The cost is estimated
   before the network from COST_TABLE and refused when it would pass the
   job's cap. A fleet-wide account floor refuses any call that would leave
   fewer than THE_ODDS_API_FLOOR_FRACTION (default 0.20) of
   THE_ODDS_API_MONTHLY_CREDITS. The cap is per run and in memory; the
   floor is advisory, because it rests on the latest x-requests-remaining
   this process saw and another process may spend in between.

3. Three read outcomes (D4). CAPTURED, EMPTY and FAILED never merge. A 429
   or 5xx that does not clear is FAILED, never EMPTY, and a 200 whose
   listed events all drop as unresolved is FAILED too: a broken alias
   table must not look like an offseason.

4. Licence (D5). Every row is tagged. Pinnacle (exact key "pinnacle") and
   every historical row are redistributable=False / internal_only; other
   live rows may appear inside our own payloads and pages, never as a
   standalone feed or a raw dump.

Capture scope stays permanently decoupled from publication scope: what this
client reads says nothing about what any repo may publish.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import Self

from predictium_odds.books.base import SportConfig, utcnow_iso
from predictium_odds.health import SourceReport
from predictium_odds.schema import (
    MARKET_MONEYLINE,
    MARKET_SPREAD,
    MARKET_TOTAL,
    SIDE_AWAY,
    SIDE_HOME,
    SIDE_OVER,
    SIDE_UNDER,
    Quote,
)

BASE = "https://api.the-odds-api.com"
BOOK = "theoddsapi"
SOURCE = "theoddsapi"

KEY_ENV = "THE_ODDS_API_KEY"
MONTHLY_ENV = "THE_ODDS_API_MONTHLY_CREDITS"
FLOOR_ENV = "THE_ODDS_API_FLOOR_FRACTION"
DEFAULT_FLOOR_FRACTION = 0.20

LICENSE_DISPLAY = "display_in_model_payloads"
LICENSE_INTERNAL = "internal_only"
PINNACLE_KEY = "pinnacle"   # exact, lowercase; no prefix or case-folded match

TIMEOUT = 30
RETRY_BACKOFF = (2, 4, 8, 16)               # the org standard (Kalshi rule)
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER = 60.0
_sleep = time.sleep                         # module-level so tests stub it

log = logging.getLogger("predictium_odds.theoddsapi")

# Our market key <- the provider's market key.
MARKETS = {"h2h": MARKET_MONEYLINE, "spreads": MARKET_SPREAD,
           "totals": MARKET_TOTAL}

# The committed bookmaker set. A key not here is counted and skipped, never
# guessed (identity before modelling). Pinnacle is here so that its rows are
# tagged internal-only rather than silently dropped.
BOOKMAKERS = frozenset({
    "betmgm", "betonlineag", "betrivers", "betus", "bovada", "draftkings",
    "espnbet", "fanatics", "fanduel", "hardrockbet", "lowvig", "mybookieag",
    "pinnacle", "williamhill_us",
})

# Cost rule per endpoint (D3). Checked against the provider's published usage
# rules on 2026-11-25; re-check when the provider changes them. "free" costs
# 0; "markets_x_regions" costs len(markets) * regions; "historical" multiplies
# that by HISTORICAL_MULTIPLIER. An endpoint not listed here is refused.
COST_TABLE_CHECKED = "2026-11-25"
HISTORICAL_MULTIPLIER = 10
COST_TABLE = {
    "sports": "free",
    "odds": "markets_x_regions",
    "event_odds": "markets_x_regions",      # an upper bound: billed on returned
    "historical_odds": "historical",
}
BOOKMAKERS_PER_REGION = 10


# --------------------------------------------------------------- budget

class CreditBudget:
    """One job's credit allowance for one run, in memory (D3).

    `cap` must be a positive int; anything else raises ValueError here, in
    the caller's own code, before any request. That is the ONE exception this
    module raises, and it carries no key. Thread-safe: a request reserves its
    estimate under the lock before it is sent and reconciles afterwards.
    Used as a context manager it logs the per-run total on exit."""

    def __init__(self, job: str, cap: int, logger: logging.Logger | None = None):
        if not isinstance(job, str) or not job.strip():
            raise ValueError("CreditBudget job must be a non-empty string")
        if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
            raise ValueError("CreditBudget cap must be a positive integer")
        self.job, self.cap = job, cap
        self.spent = 0
        self.reserved = 0
        self.requests = 0
        self.refused = 0
        self._lock = threading.Lock()
        self._log = logger or log

    def _reserve(self, estimate: int) -> str | None:
        with self._lock:
            left = self.cap - self.spent - self.reserved
            if estimate > left:
                self.refused += 1
                return (f"credit cap: would spend {estimate}, "
                        f"{max(left, 0)} of {self.cap} left")
            self.reserved += estimate
            return None

    def _release(self, estimate: int) -> None:
        with self._lock:
            self.reserved -= estimate

    def _charge(self, estimate: int, actual: int) -> None:
        with self._lock:
            self.reserved -= estimate
            self.spent += actual
            self.requests += 1

    def _refused_elsewhere(self) -> None:
        with self._lock:
            self.refused += 1

    def summary(self) -> str:
        rem = _ACCOUNT.remaining
        return (f"theoddsapi run job={self.job} requests={self.requests} "
                f"spent={self.spent} cap={self.cap} refused={self.refused} "
                f"remaining={rem if rem is not None else '?'}")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> bool:
        self._log.info(self.summary())
        return False


class _Account:
    """What this process knows about the account: the latest
    x-requests-remaining and the credits reserved by calls in flight."""

    def __init__(self) -> None:
        self.remaining: int | None = None
        self.reserved = 0
        self.lock = threading.Lock()

    def reset(self) -> None:          # tests
        with self.lock:
            self.remaining, self.reserved = None, 0


_ACCOUNT = _Account()


def _floor_config() -> tuple[int, float] | None:
    """(monthly_credits, floor_fraction) from the environment, or None when
    either is missing or invalid. Never logs the values."""
    raw = os.environ.get(MONTHLY_ENV, "").strip()
    try:
        monthly = int(raw)
    except ValueError:
        return None
    if monthly <= 0:
        return None
    raw_f = os.environ.get(FLOOR_ENV, "").strip()
    try:
        frac = float(raw_f) if raw_f else DEFAULT_FLOOR_FRACTION
    except ValueError:
        return None
    if not (0.0 <= frac < 1.0) or math.isnan(frac):
        return None
    return monthly, frac


def estimate_cost(endpoint: str, markets: tuple[str, ...],
                  regions: tuple[str, ...] = (),
                  bookmakers: tuple[str, ...] | None = None) -> int | None:
    """Credits a request will cost, as an upper bound; None when the endpoint
    is not in COST_TABLE (the caller refuses it)."""
    rule = COST_TABLE.get(endpoint)
    if rule is None:
        return None
    if rule == "free":
        return 0
    if bookmakers:
        n_regions = math.ceil(len(bookmakers) / BOOKMAKERS_PER_REGION)
    else:
        n_regions = len(regions)
    cost = len(markets) * max(n_regions, 1)
    if rule == "historical":
        cost *= HISTORICAL_MULTIPLIER
    return cost


# ------------------------------------------------------------- transport

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would resend the URL, key included, to a host we did not
    choose. Returning None makes urllib raise HTTPError with the 3xx code."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        _NoRedirect(),
        urllib.request.HTTPHandler(debuglevel=0),
        urllib.request.HTTPSHandler(debuglevel=0),
    )


@dataclass
class _Response:
    status: int | None            # None when no HTTP response came back
    headers: dict[str, str]       # lower-cased names
    body: bytes | None
    error: str | None             # class name of a transport/read failure


def _lower(headers: Any) -> dict[str, str]:
    try:
        return {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}
    except Exception:   # noqa: BLE001  (a malformed header map is just absent)
        return {}


def _send(path: str, params: dict[str, Any]) -> _Response:
    """The ONLY place the key and a full URL exist. Every Exception is turned
    into a _Response here; the locals holding the key are cleared in the
    finally, so even a KeyboardInterrupt's frame shows no key."""
    key = url = req = query = None
    status: int | None = None
    headers: dict[str, str] = {}
    body: bytes | None = None
    error: str | None = None
    try:
        key = os.environ.get(KEY_ENV, "")
        query = dict(params)
        query["apiKey"] = key
        url = f"{BASE}{path}?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with _opener().open(req, timeout=TIMEOUT) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            headers = _lower(resp.headers)
            body = resp.read()
    except urllib.error.HTTPError as e:   # carries .url: read fields, drop it
        status = e.code
        headers = _lower(e.headers)
        try:
            e.close()
        except Exception:   # noqa: BLE001, S110  (closing is best effort)
            pass
    except Exception as e:   # noqa: BLE001  (urlopen AND the body read)
        error = type(e).__name__
    except BaseException as e:
        # KeyboardInterrupt / SystemExit / GeneratorExit propagate, but bare:
        # no traceback into urllib's frames, no chained context, and this
        # frame's key-bearing locals emptied first.
        key = url = req = query = None
        e.__traceback__ = None
        e.__context__ = None
        e.__cause__ = None
        raise e   # noqa: TRY201  (a bare raise would restore the traceback)
    finally:
        key = url = req = query = None
    return _Response(status, headers, body, error)


def _int_header(headers: dict[str, str], name: str) -> int | None:
    raw = headers.get(name)
    try:
        v = int(float(raw))
    except (TypeError, ValueError, OverflowError):
        return None
    return v if v >= 0 else None


def _retry_after(headers: dict[str, str]) -> float | None:
    try:
        v = float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None
    return v if 0 < v <= MAX_RETRY_AFTER else None   # False for nan


# --------------------------------------------------------------- results

@dataclass
class OddsApiRead:
    status: str                       # "captured" | "empty" | "failed"
    quotes: list[Quote] | None        # None only when failed
    report: SourceReport
    credits: dict[str, int | None] | None
    provenance: dict[str, Any]
    payload: bytes | None = None      # only with keep_payload=True


@dataclass
class _Call:
    """What one request (with its retries) produced."""
    response: _Response | None
    note: str | None                  # set when the call FAILED
    credits: dict[str, int | None] | None = None
    attempts: int = 0
    params: dict[str, Any] = field(default_factory=dict)
    fetched_at: str = ""


def _failure_note(resp: _Response) -> str:
    """Fixed phrases, the status code and an exception class name: nothing
    that can carry the URL."""
    if resp.status is None:
        return f"transport error ({resp.error or 'unknown'})"
    code = resp.status
    if code == 429:
        return f"HTTP 429 rate limited, still refused after {len(RETRY_BACKOFF)} retries"
    if code in RETRY_STATUS:
        return f"HTTP {code} server error, still failing after {len(RETRY_BACKOFF)} retries"
    if 300 <= code < 400:
        return f"HTTP {code} unexpected redirect"
    if code == 401:
        return "HTTP 401 unauthorized (bad or exhausted key)"
    if resp.error:
        return f"HTTP {code} body read failed ({resp.error})"
    return f"HTTP {code}"


# ----------------------------------------------------------- the request

def _ensure_remaining(budget: CreditBudget, logger: logging.Logger) -> str | None:
    """The floor needs a known `remaining`. Read it from the free sports
    list when this process has not seen one. Fails closed."""
    with _ACCOUNT.lock:
        known = _ACCOUNT.remaining is not None
    if known:
        return None
    resp = _send("/v4/sports", {})
    rem = _int_header(resp.headers, "x-requests-remaining")
    last = _int_header(resp.headers, "x-requests-last")
    budget._charge(0, last or 0)
    logger.info(f"theoddsapi job={budget.job} endpoint=/v4/sports sport=- "
                f"status={'captured' if resp.status == 200 else 'failed'} "
                f"credits_last={last if last is not None else '?'} "
                f"spent={budget.spent}/{budget.cap} "
                f"remaining={rem if rem is not None else '?'}")
    if resp.status != 200 or rem is None:
        return "account floor: remaining unknown"
    with _ACCOUNT.lock:
        if _ACCOUNT.remaining is None:
            _ACCOUNT.remaining = rem
    return None


def _floor_refusal(budget: CreditBudget, estimate: int,
                   logger: logging.Logger) -> str | None:
    """Reserve `estimate` against the account floor, or say why not."""
    cfg = _floor_config()
    if cfg is None:
        return "account floor not configured"
    monthly, frac = cfg
    if estimate == 0:
        return None
    floor = frac * monthly
    with _ACCOUNT.lock:
        remaining = _ACCOUNT.remaining
        if remaining is None:
            return "account floor: remaining unknown"
        after = remaining - _ACCOUNT.reserved - estimate
        if after < floor:
            logger.warning(f"theoddsapi account-floor job={budget.job} "
                           f"remaining={remaining} estimate={estimate} "
                           f"floor={floor:.0f}")
            return f"account floor: would leave {after} credits, floor {floor:.0f}"
        _ACCOUNT.reserved += estimate
    return None


def _account_settle(estimate: int, actual: int, remaining: int | None) -> None:
    with _ACCOUNT.lock:
        _ACCOUNT.reserved -= estimate
        if remaining is not None:
            _ACCOUNT.remaining = remaining
        elif _ACCOUNT.remaining is not None:
            # no header on a billable response: lower the cache by what was
            # charged, so the floor cannot go stale-high (review nit 2)
            _ACCOUNT.remaining = max(_ACCOUNT.remaining - actual, 0)


def _request(path: str, params: dict[str, Any], endpoint: str,
             markets: tuple[str, ...], regions: tuple[str, ...],
             bookmakers: tuple[str, ...] | None, sport_key: str,
             budget: CreditBudget, logger: logging.Logger) -> _Call:
    """One logical request with the org retry rule. Never raises."""
    estimate = estimate_cost(endpoint, markets, regions, bookmakers)
    if estimate is None:
        budget._refused_elsewhere()
        return _Call(None, "endpoint not in cost table", params=params)
    if not os.environ.get(KEY_ENV, "").strip():
        return _Call(None, "no API key in environment", params=params)
    if estimate > 0:
        if _floor_config() is None:
            budget._refused_elsewhere()
            return _Call(None, "account floor not configured", params=params)
        why = _ensure_remaining(budget, logger)
        if why:
            budget._refused_elsewhere()
            return _Call(None, why, params=params)

    last_resp: _Response | None = None
    credits: dict[str, int | None] | None = None
    attempts = 0
    fetched_at = ""
    for wait in (*RETRY_BACKOFF, None):
        why = budget._reserve(estimate)
        if why:
            return _Call(last_resp, why, credits, attempts, params, fetched_at)
        why = _floor_refusal(budget, estimate, logger)
        if why:
            budget._release(estimate)
            budget._refused_elsewhere()
            return _Call(last_resp, why, credits, attempts, params, fetched_at)
        fetched_at = utcnow_iso()
        resp = _send(path, params)
        attempts += 1
        last_resp = resp
        actual = _int_header(resp.headers, "x-requests-last")
        if actual is None:
            if estimate:
                logger.warning(f"theoddsapi job={budget.job} endpoint={path}: "
                               f"no usable x-requests-last; counting the "
                               f"estimate {estimate}")
            actual = estimate
        elif actual > estimate:
            logger.warning(f"theoddsapi job={budget.job} endpoint={path}: "
                           f"charged {actual} above estimate {estimate}; "
                           f"COST_TABLE needs checking")
        remaining = _int_header(resp.headers, "x-requests-remaining")
        budget._charge(estimate, actual)
        _account_settle(estimate, actual, remaining)
        credits = {"last": actual,
                   "used": _int_header(resp.headers, "x-requests-used"),
                   "remaining": remaining}
        status = "captured" if resp.status == 200 and resp.body is not None \
            and resp.error is None else "failed"
        logger.info(f"theoddsapi job={budget.job} endpoint={path} "
                    f"sport={sport_key} status={status} credits_last={actual} "
                    f"spent={budget.spent}/{budget.cap} "
                    f"remaining={remaining if remaining is not None else '?'}")
        if status == "captured":
            return _Call(resp, None, credits, attempts, params, fetched_at)
        if resp.status in RETRY_STATUS and wait is not None:
            _sleep(_retry_after(resp.headers) or wait)
            continue
        return _Call(resp, _failure_note(resp), credits, attempts, params,
                     fetched_at)
    return _Call(last_resp, _failure_note(last_resp), credits, attempts,
                 params, fetched_at)   # pragma: no cover (loop always returns)


# ---------------------------------------------------------------- parsing

def _price(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v == 0 or v != int(v):
        return None
    return int(v)


def _parse_time(raw: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class _Counts:
    listed: int = 0
    unresolved: int = 0
    inplay: int = 0
    no_books: int = 0
    unknown_book: int = 0
    bad_price: int = 0
    resolved_events: int = 0


def _quotes_from_events(events: list[dict], cfg: SportConfig, ts: str,
                        as_of: datetime, historical: bool,
                        counts: _Counts) -> list[Quote]:
    quotes: list[Quote] = []
    for ev in events:
        if not isinstance(ev, dict):
            counts.unresolved += 1
            continue
        counts.listed += 1
        start = _parse_time(ev.get("commence_time"))
        if start is None:
            counts.unresolved += 1
            continue
        if start <= as_of:
            counts.inplay += 1
            continue
        raw_home, raw_away = ev.get("home_team") or "", ev.get("away_team") or ""
        home, away = cfg.resolve_team(raw_home), cfg.resolve_team(raw_away)
        if not home or not away:
            counts.unresolved += 1
            continue
        counts.resolved_events += 1
        ekey = cfg.event_key(home, away, str(ev.get("commence_time")))
        sids = {"theoddsapi_event_id": ev.get("id")}
        if not ev.get("bookmakers"):
            counts.no_books += 1      # books have not posted yet: normal
            continue
        for bm in ev.get("bookmakers") or []:
            bkey = bm.get("key") if isinstance(bm, dict) else None
            if bkey not in BOOKMAKERS:
                counts.unknown_book += 1
                continue
            internal = historical or bkey == PINNACLE_KEY
            extras = {"source": SOURCE, "bookmaker": bkey,
                      "bookmaker_title": bm.get("title"),
                      "last_update": bm.get("last_update"),
                      "license_class": LICENSE_INTERNAL if internal
                      else LICENSE_DISPLAY}
            for mk in bm.get("markets") or []:
                market = MARKETS.get(mk.get("key")) if isinstance(mk, dict) else None
                if market is None:
                    continue
                for o in mk.get("outcomes") or []:
                    name = o.get("name")
                    if market == MARKET_TOTAL:
                        side = {"Over": SIDE_OVER, "Under": SIDE_UNDER}.get(name)
                    else:
                        side = SIDE_HOME if name == raw_home else \
                            SIDE_AWAY if name == raw_away else None
                    p = _price(o.get("price"))
                    if side is None or p is None:
                        counts.bad_price += 1
                        continue
                    line = None
                    if market in (MARKET_SPREAD, MARKET_TOTAL):
                        try:
                            line = float(o.get("point"))
                        except (TypeError, ValueError):
                            counts.bad_price += 1
                            continue
                        if not math.isfinite(line):
                            counts.bad_price += 1
                            continue
                    # spreads: the provider's point is THAT team's handicap,
                    # negative = favourite, which is our betting convention.
                    quotes.append(Quote(
                        cfg.sport, ekey, f"{BOOK}:{bkey}", market, side, p,
                        line=line, redistributable=not internal, ts=ts,
                        source_ids=dict(sids), extras=dict(extras)))
    return quotes


def _outcome(quotes: list[Quote], counts: _Counts) -> tuple[str, str]:
    """(status, diagnostic) per D4."""
    drops = []
    if counts.unresolved:
        drops.append(f"{counts.unresolved} unresolved team")
    if counts.unknown_book:
        drops.append(f"{counts.unknown_book} unknown bookmaker")
    if counts.inplay:
        drops.append(f"{counts.inplay} in-play dropped")
    if counts.bad_price:
        drops.append(f"{counts.bad_price} bad price")
    detail = ", ".join(drops)
    if counts.no_books:
        drops.append(f"{counts.no_books} with no bookmaker prices yet")
        detail = ", ".join(drops)
    if quotes:
        return "captured", detail
    if counts.listed == 0:
        return "empty", "no events listed"
    if counts.inplay == counts.listed:
        return "empty", "all listed events in-play"
    if not (counts.unresolved or counts.unknown_book or counts.bad_price):
        # Portfolio ruling 2026-11-25: listed events with no bookmakers at all
        # are EMPTY (books post later). FAILED is kept for rows that are
        # present but unresolved, which means our tables broke.
        return "empty", (f"{counts.listed} events listed, no bookmaker "
                         f"prices yet" + (f" ({detail})" if detail else ""))
    return "failed", (f"{counts.listed} listed, 0 priced"
                      + (f": {detail}" if detail else ""))


# --------------------------------------------------------------- reading

def _credit_tail(credits: dict | None, budget: CreditBudget) -> str:
    last = credits.get("last") if credits else None
    rem = credits.get("remaining") if credits else None
    return (f"credits {last if last is not None else 0} "
            f"(spent {budget.spent}/{budget.cap}, "
            f"remaining {rem if rem is not None else '?'})")


def _read(cfg: SportConfig, budget: CreditBudget, endpoint: str, path: str,
          params: dict[str, Any], markets: tuple[str, ...],
          regions: tuple[str, ...], bookmakers: tuple[str, ...] | None,
          historical: bool, keep_payload: bool,
          logger: logging.Logger | None) -> OddsApiRead:
    logger = logger or budget._log
    sport_key = (cfg.extras or {}).get("theoddsapi_sport") or "?"
    call = _request(path, params, endpoint, markets, regions, bookmakers,
                    sport_key, budget, logger)
    resp = call.response
    body = resp.body if resp else None
    prov = {"endpoint": path,
            "params": {k: v for k, v in params.items() if k != "apiKey"},
            "fetched_at": call.fetched_at,
            "http_status": resp.status if resp else None,
            "sha256": hashlib.sha256(body).hexdigest() if body is not None else None,
            "credits": call.credits}

    def done(status: str, quotes: list[Quote] | None, n: int, ok: bool,
             note: str) -> OddsApiRead:
        full = f"{note}; {_credit_tail(call.credits, budget)}" if note \
            else _credit_tail(call.credits, budget)
        return OddsApiRead(status, quotes,
                           SourceReport(BOOK, cfg.sport, "*", n, ok, full),
                           call.credits, prov,
                           body if keep_payload else None)

    if call.note:
        return done("failed", None, 0, False, call.note)
    try:
        doc = json.loads(body.decode("utf-8"))
    except Exception as e:   # noqa: BLE001  (class name only)
        return done("failed", None, 0, False,
                    f"unparseable body ({type(e).__name__})")
    try:
        if historical:
            if not isinstance(doc, dict):
                return done("failed", None, 0, False, "unexpected body shape")
            as_of = _parse_time(doc.get("timestamp"))
            if as_of is None:
                return done("failed", None, 0, False, "historical body has no timestamp")
            events = doc.get("data")
        else:
            as_of = _parse_time(call.fetched_at) or datetime.now(timezone.utc)
            events = [doc] if isinstance(doc, dict) and endpoint == "event_odds" \
                else doc
        if not isinstance(events, list):
            return done("failed", None, 0, False, "unexpected body shape")
        counts = _Counts()
        quotes = _quotes_from_events(events, cfg, call.fetched_at, as_of,
                                     historical, counts)
    except Exception as e:   # noqa: BLE001  (class name only)
        return done("failed", None, 0, False, f"parse error ({type(e).__name__})")
    status, detail = _outcome(quotes, counts)
    if status == "failed":
        return done("failed", None, 0, False, detail)
    return done(status, quotes, counts.resolved_events if quotes else 0, True,
                detail)


def _guarded(cfg: SportConfig, budget: CreditBudget, fn) -> OddsApiRead:
    """The last line of D2: no Exception leaves a public entry point."""
    try:
        return fn()
    except Exception as e:   # noqa: BLE001  (class name only)
        name = type(e).__name__
    sport = getattr(cfg, "sport", "?")
    return OddsApiRead("failed", None,
                       SourceReport(BOOK, sport, "*", 0, False,
                                    f"client error ({name})"),
                       None, {"endpoint": None, "params": {}, "fetched_at": "",
                              "http_status": None, "sha256": None,
                              "credits": None})


def _common(markets, regions, bookmakers):
    markets = tuple(markets)
    regions = tuple(regions)
    bookmakers = tuple(bookmakers) if bookmakers else None
    params = {"markets": ",".join(markets), "oddsFormat": "american",
              "dateFormat": "iso"}
    if bookmakers:
        params["bookmakers"] = ",".join(bookmakers)
    else:
        params["regions"] = ",".join(regions)
    return markets, regions, bookmakers, params


def read_odds(cfg: SportConfig, budget: CreditBudget, *,
              markets=("h2h", "spreads", "totals"), regions=("us",),
              bookmakers=None, keep_payload: bool = False,
              logger: logging.Logger | None = None) -> OddsApiRead:
    """Live pre-game odds for the sport in cfg.extras["theoddsapi_sport"]."""
    def run():
        sport = (cfg.extras or {}).get("theoddsapi_sport")
        if not sport:
            return OddsApiRead("failed", None, SourceReport(
                BOOK, cfg.sport, "*", 0, False, "no theoddsapi_sport configured"),
                None, {"endpoint": None, "params": {}, "fetched_at": "",
                       "http_status": None, "sha256": None, "credits": None})
        m, r, b, params = _common(markets, regions, bookmakers)
        return _read(cfg, budget, "odds", f"/v4/sports/{sport}/odds", params,
                     m, r, b, False, keep_payload, logger)
    return _guarded(cfg, budget, run)


def read_event_odds(cfg: SportConfig, budget: CreditBudget, event_id: str, *,
                    markets=("h2h", "spreads", "totals"), regions=("us",),
                    bookmakers=None, keep_payload: bool = False,
                    logger: logging.Logger | None = None) -> OddsApiRead:
    """One event's live odds. Cost is estimated as markets requested times
    regions, an upper bound on what the provider bills."""
    def run():
        sport = (cfg.extras or {}).get("theoddsapi_sport")
        if not sport or not isinstance(event_id, str) or not event_id:
            return OddsApiRead("failed", None, SourceReport(
                BOOK, cfg.sport, "*", 0, False, "no sport or event id"),
                None, {"endpoint": None, "params": {}, "fetched_at": "",
                       "http_status": None, "sha256": None, "credits": None})
        m, r, b, params = _common(markets, regions, bookmakers)
        path = f"/v4/sports/{sport}/events/{urllib.parse.quote(event_id, safe='')}/odds"
        return _read(cfg, budget, "event_odds", path, params, m, r, b, False,
                     keep_payload, logger)
    return _guarded(cfg, budget, run)


def read_historical_odds(cfg: SportConfig, budget: CreditBudget, date_iso: str, *,
                         markets=("h2h", "spreads", "totals"), regions=("us",),
                         bookmakers=None, keep_payload: bool = False,
                         logger: logging.Logger | None = None) -> OddsApiRead:
    """A historical snapshot at `date_iso`. Every row is internal_only.
    In-play is judged against the snapshot's own timestamp, not the fetch."""
    def run():
        sport = (cfg.extras or {}).get("theoddsapi_sport")
        if not sport or not isinstance(date_iso, str) or not date_iso:
            return OddsApiRead("failed", None, SourceReport(
                BOOK, cfg.sport, "*", 0, False, "no sport or date"),
                None, {"endpoint": None, "params": {}, "fetched_at": "",
                       "http_status": None, "sha256": None, "credits": None})
        m, r, b, params = _common(markets, regions, bookmakers)
        params["date"] = date_iso
        return _read(cfg, budget, "historical_odds",
                     f"/v4/historical/sports/{sport}/odds", params, m, r, b,
                     True, keep_payload, logger)
    return _guarded(cfg, budget, run)


def fetch_game_lines(cfg: SportConfig, budget: CreditBudget, **kw
                     ) -> tuple[list[Quote], SourceReport]:
    """The registry shape every adapter shares. A FAILED read is
    ([], report) with report.ok False: check report.ok before reading an
    empty list as an empty board."""
    r = read_odds(cfg, budget, **kw)
    return (r.quotes or []), r.report


def _reset_for_tests() -> None:
    _ACCOUNT.reset()


__all__ = [
    "BOOKMAKERS",
    "COST_TABLE",
    "COST_TABLE_CHECKED",
    "CreditBudget",
    "OddsApiRead",
    "estimate_cost",
    "fetch_game_lines",
    "read_event_odds",
    "read_historical_odds",
    "read_odds",
]
