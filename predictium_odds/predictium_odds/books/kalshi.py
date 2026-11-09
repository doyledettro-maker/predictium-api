"""Kalshi public market-data API (keyless — auth is only for trading, which
we never do). CFTC-regulated exchange: prices are two-sided probabilities
with no bookmaker vig. NEVER de-vig a Kalshi quote.

Series naming is uniform: KX{SPORT}{GAME|SPREAD|TOTAL|WINS|MATCH|SERIES}
(verified live: NFL/NBA/WNBA/MLB/NCAAF + KXATPMATCH/KXWTAMATCH; plus
championship/outright series like KXNBA, KXMLBWS). cfg.kalshi_prefix is the
KX{SPORT} stem.

API shape (2026-07): the legacy integer-cent fields (yes_bid, yes_ask,
last_price, volume) return null — prices live in the "*_dollars" string
fields. Anything still reading the integer fields is silently getting nulls.

Correctness rule: strike ladders (win totals, spreads, totals) are ONLY
comparable to a book/consensus main line at the aligned contract —
lines.align_main_line() keyed by over line. For a >= win-total rung
"Over L" == "wins >= L + 0.5" (ladder_by_line); for a "greater" contract the
floor already IS the over line. fetch_ladder / parse_contract read the
strike type and return every contract, for pricing any offered strike.
Integer book lines (push semantics) have no equivalent binary contract.
Never nearest-strike; a missing equivalent is a loud skip.

Liquidity: treat thin contracts like thin books — quotes with a spread wider
than MAX_SPREAD are dropped (report notes how many), the same posture the
repos take toward stale/pulled book lines.
"""

from __future__ import annotations

import math
import re
import time
import urllib.parse
from dataclasses import dataclass, replace

from predictium_odds.books.base import SportConfig, get_json, utcnow_iso
from predictium_odds.health import SourceReport
from predictium_odds.oddsmath import prob_to_american
from predictium_odds.schema import MARKET_MONEYLINE, Quote

BASE = "https://api.elections.kalshi.com/trade-api/v2"
BOOK = "kalshi"
MAX_SPREAD = 0.10  # yes_ask - yes_bid above this = too thin to quote

# Retry on HTTP 429 only, PER PAGE, with the org's backoff (2, 4, 8, 16s).
# Kalshi rate-limits by IP and the Mac mini runs every sport's pipeline from
# one IP, so the sports rate-limit each other. Measured by the CFB session
# 2026-09-26 on a 53-game Saturday: a burst of paged reads drew 429s within a
# second, and retrying the WHOLE series from page one (their first version)
# failed KXNCAAFSPREAD outright, because each restart re-walked the pages that
# had already drawn the limit. Retrying only the page that was refused pulled
# 2,458 spread and 1,831 total markets through 62 429s.
#
# Why this belongs here and not in base.get_json: the fix is pagination-aware
# ("retry the refused page, not the series"), and Kalshi's limits are its own.
# A blanket retry in get_json would change Bovada/FanDuel behaviour too.
#
# An unclearable 429 still RAISES, deliberately. The callers below turn that
# into SourceReport(ok=False), which is what keeps a failed read
# distinguishable from a genuinely empty market list — a 429 swallowed into
# `[]` would publish as "this series has no markets", which is a different
# and false claim. Any non-429 raises at once: a retry is for congestion,
# not for a broken request.
RETRY_BACKOFF = (2, 4, 8, 16)
PAGE_PACING_SECONDS = 0.2
MAX_PAGES = 50                # never loop forever on a bad cursor
_sleep = time.sleep           # module-level so tests can stub the wait


def _failure_note(series: str, e: Exception) -> str:
    """Why a read failed, in a form an operator can act on.

    Names the HTTP code explicitly rather than trusting `str(e)` to carry it.
    urllib's HTTPError happens to stringify as "HTTP Error 429: ...", but a
    wrapped or re-raised error need not, and a note reading "KXNCAAFGAME: "
    tells whoever reads the health line nothing. Rate limiting is called out
    by name because it is the one failure here that is expected, transient,
    and already retried — so seeing it means the retries were exhausted.
    """
    code = getattr(e, "code", None)
    if code == 429:
        return (f"{series}: HTTP 429 rate limited, still refused after "
                f"{len(RETRY_BACKOFF)} retries")
    detail = str(e) or type(e).__name__
    return f"{series}: HTTP {code} {detail}" if code else f"{series}: {detail}"


def _get(path: str, **params):
    return get_json(f"{BASE}/{path}", params,
                    headers={"Accept": "application/json"})


def _is_rate_limit(e: Exception) -> bool:
    return getattr(e, "code", None) == 429


def _retry_after(e: Exception) -> float | None:
    """The venue's own requested wait, if it sent one, else None."""
    try:
        return float((getattr(e, "headers", None) or {}).get("Retry-After"))
    except (TypeError, ValueError):
        return None


def _get_page(path: str, params: dict) -> dict:
    """One page, retried on 429 only. Honours Retry-After over our backoff."""
    for attempt, wait in enumerate((*RETRY_BACKOFF, None)):
        try:
            return _get(path, **params)
        except Exception as e:   # re-raised below unless it is a 429
            if wait is None or not _is_rate_limit(e):
                raise
            wait = _retry_after(e) or wait
            print(f"kalshi: 429 on {params.get('series_ticker', path)}"
                  f"{' (page cursor)' if params.get('cursor') else ''}, "
                  f"retry {attempt + 1}/{len(RETRY_BACKOFF)} in {wait:.0f}s")
            _sleep(wait)
    raise RuntimeError("unreachable")


def _paged(path: str, key: str, **params) -> list[dict]:
    out: list[dict] = []
    cursor = None
    for _ in range(MAX_PAGES):
        q = dict(params)          # per-page copy: never mutate the caller's
        if cursor:
            q["cursor"] = cursor
        data = _get_page(path, q)
        out.extend(data.get(key, []))
        cursor = data.get("cursor")
        if not cursor:
            break
        _sleep(PAGE_PACING_SECONDS)   # light pacing; we share the IP
    return out


def _dollars(market: dict, field: str) -> float | None:
    try:
        v = float(market.get(f"{field}_dollars"))
    except (TypeError, ValueError):
        return None
    return v if 0.0 < v < 1.0 else None


def _two_sided(m: dict) -> tuple[float, float] | None:
    bid, ask = _dollars(m, "yes_bid"), _dollars(m, "yes_ask")
    if bid is None or ask is None or ask - bid > MAX_SPREAD:
        return None
    return bid, ask


def fetch_game_quotes(cfg: SportConfig, series_suffix: str = "GAME",
                      ) -> tuple[list[Quote], SourceReport]:
    """Winner quotes from KX{SPORT}GAME (KX{ATP,WTA}MATCH for tennis).

    Kalshi's event tickers don't encode home/away order, so event_key is the
    source-local "kalshi:{event_ticker}" and each Quote carries the resolved
    team in extras["team"] (via cfg.resolve_team on the market subtitle) plus
    close_time. The consuming repo joins to its schedule on (teams, date) —
    the same join posture as tennis's book_join.
    """
    series = f"{cfg.kalshi_prefix}{series_suffix}"
    ts = utcnow_iso()
    try:
        markets = _paged("markets", "markets", series_ticker=series,
                         status="open", limit=1000)
    except Exception as e:  # noqa: BLE001
        return [], SourceReport(BOOK, cfg.sport, MARKET_MONEYLINE, 0, False,
                                _failure_note(series, e))

    quotes: list[Quote] = []
    thin = 0
    for m in markets:
        prices = _two_sided(m)
        if prices is None:
            thin += 1
            continue
        bid, ask = prices
        mid = (bid + ask) / 2
        team = cfg.resolve_team((m.get("yes_sub_title") or "").strip())
        quotes.append(Quote(
            cfg.sport, f"kalshi:{m.get('event_ticker')}", BOOK,
            MARKET_MONEYLINE, team or (m.get("yes_sub_title") or "?"),
            prob_to_american(mid), is_exchange=True, ts=ts,
            source_ids={"ticker": m.get("ticker"),
                        "event_ticker": m.get("event_ticker")},
            extras={"team": team, "yes_bid": bid, "yes_ask": ask, "mid": mid,
                    "close_time": m.get("close_time")}))
    note = f"{thin} thin/unquoted dropped" if thin else ""
    return quotes, SourceReport(BOOK, cfg.sport, MARKET_MONEYLINE,
                                len(quotes), True, note)


_LADDER_EVENT_RE = re.compile(r"-(\d+)(?P<code>[A-Z]+)$")


def fetch_ladders(cfg: SportConfig, series_suffix: str = "WINS",
                  team_code_map: dict[str, str] | None = None,
                  ) -> tuple[dict[str, list[dict]], SourceReport]:
    """Per-team strike ladders from e.g. KX{SPORT}WINS.

    Returns {team: [{strike, yes_bid, yes_ask, mid, ticker}, ...]} sorted by
    strike. team_code_map translates Kalshi team codes that differ from the
    repo's abbrs (NFL: {"JAC": "JAX", "LAR": "LA"}). Unrecognized codes are
    reported, never silently dropped.
    """
    series = f"{cfg.kalshi_prefix}{series_suffix}"
    market_key = series_suffix.lower()
    try:
        markets = _paged("markets", "markets", series_ticker=series,
                         status="open", limit=1000)
    except Exception as e:  # noqa: BLE001
        return {}, SourceReport(BOOK, cfg.sport, market_key, 0, False,
                                _failure_note(series, e))

    ladders: dict[str, list[dict]] = {}
    unmapped: set[str] = set()
    skipped = thin = 0
    for m in markets:
        em = _LADDER_EVENT_RE.search(m.get("event_ticker") or "")
        strike = m.get("floor_strike")
        if not em or m.get("strike_type") != "greater_or_equal" \
                or strike is None:
            skipped += 1      # not a >= team ladder; counted, not hidden
            continue
        code = em.group("code")
        team = (team_code_map or {}).get(code, code)
        prices = _two_sided(m)
        if prices is None:
            thin += 1
            continue
        bid, ask = prices
        if team_code_map is not None and code not in team_code_map \
                and len(code) > 3:
            unmapped.add(code)
        ladders.setdefault(team, []).append({
            "strike": int(strike), "yes_bid": bid, "yes_ask": ask,
            "mid": (bid + ask) / 2, "ticker": m.get("ticker")})
    for rungs in ladders.values():
        rungs.sort(key=lambda r: r["strike"])
    parts = [f"unmapped codes {sorted(unmapped)}"] if unmapped else []
    if skipped:
        parts.append(f"{skipped} non->= contracts skipped")
    if thin:
        parts.append(f"{thin} thin/unquoted dropped")
    note = "; ".join(parts)
    return ladders, SourceReport(BOOK, cfg.sport, market_key,
                                 sum(len(v) for v in ladders.values()),
                                 True, note)


def ladder_by_line(rungs: list[dict]) -> dict[float, dict]:
    """Strike ladder re-keyed by book-equivalent line for align_main_line:
    contract "wins >= N" prices exactly the book's "Over N - 0.5".

    ONLY for fetch_ladders' rungs, which are greater_or_equal contracts with
    an integer strike. A `greater` contract's floor is ALREADY the over line
    (KXNFLTOTAL floor 70.5 = "Over 70.5"); subtracting 0.5 from it prices
    the wrong rung. For any series other than a >= ladder, use fetch_ladder
    and LadderContract.over_line, which reads the strike type.
    """
    return {r["strike"] - 0.5: r for r in rungs}


# ---------------------------------------------------------------------------
# Full-ladder capture (Doyle, 2026-09-27: every model prices the strike that
# is actually offered, from its own distribution, and settles at the traded
# strike). fetch_ladders above returns >= team ladders only; this returns
# EVERY contract on a series, parsed into one strike convention.
#
# The strike convention, verified live 2026-09-28 across NFL, NCAAF, MLB and
# WNBA spread/total/prop/win-total series:
#
#   strike_type "greater", half-point floor F   -> YES = outcome > F,
#       i.e. "Over F". The floor IS the over line. (KXNFLTOTAL floor 70.5,
#       "Over 70.5 points"; KXMLBKS floor 8.5, "Cam Schlittler: 9+";
#       KXNFLSPREAD floor 27.5, "LA Rams wins by over 27.5 points".)
#   strike_type "greater_or_equal", integer floor N -> YES = outcome >= N,
#       i.e. "Over N - 0.5". (KXNFLWINS floor 9, "9+ wins".)
#   strike_type "structured" -> a named outcome with no strike (KX*GAME
#       winners, soccer 1X2 incl. "Tie").
#
# Anything else raises StrikeParseError. A "greater" contract on an integer
# floor or a ">=" on a half-point floor is a push-semantics question we have
# not seen and will not guess at, and "between"/"less" contracts price a
# different event altogether. The double-adjust trap this closes: "k+" is
# over k - 0.5, and Kalshi has ALREADY applied that to a "greater" floor, so
# subtracting 0.5 again prices the wrong rung. The subtitle is parsed as an
# independent cross-check on the strike, and a disagreement raises.
#
# Spreads: "TEAM wins by over X" is TEAM at -X in our convention (negative =
# that team favoured). The NO side is the opponent at +X. The team is read
# from the ticker suffix ("-LAR28") as a Kalshi code; mapping it to the
# repo's own identity stays in the repo, via its committed code tables.
# ---------------------------------------------------------------------------

class StrikeParseError(ValueError):
    """A contract whose strike we cannot read with certainty. Never guessed."""


_KPLUS_RE = re.compile(r"(?<![\d.])(\d+)\+")
_OVER_RE = re.compile(r"\b(?:over|more than)\s+(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_SPREAD_TEXT_RE = re.compile(r"\bwins by (?:over|more than)\b", re.IGNORECASE)
_TEAM_SUFFIX_RE = re.compile(r"-(?P<code>[A-Z]+?)(?P<rung>\d+)$")


@dataclass(frozen=True)
class LadderContract:
    """One Kalshi contract, strike-normalised. Prices are YES-side dollars
    in (0, 1); None means nobody is on that side of the book."""

    ticker: str
    event_ticker: str
    kind: str                       # "over" | "spread" | "outcome"
    over_line: float | None         # YES = outcome > over_line (half-point)
    team_code: str | None           # spread only: Kalshi's code, unmapped
    spread_line: float | None       # spread only: -over_line, for team_code
    outcome: str | None             # "outcome" only: yes_sub_title
    subject: str | None             # player / team text from the subtitle
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    volume: float | None
    open_interest: float | None
    close_time: str | None
    subtitle: str
    strike_type: str
    floor_strike: float | None
    # Settlement terms VERBATIM from the API (golf 0038 §7; push/void on
    # half-point vs whole-number strikes everywhere). Never paraphrased,
    # never inferred: a model that prices a tie-sensitive contract reads the
    # payout rule from here. "" when the venue sent none.
    rules_primary: str = ""
    rules_secondary: str = ""
    # The series' fee terms, read from /series/{ticker} in the same capture.
    # None when that read failed or the venue omitted them, and then
    # oddsmath.kalshi_ev_at_ask returns None: no fee, no EV claim.
    fee_type: str | None = None
    fee_multiplier: float | None = None
    # 0.3.1 (NBA props capture brief, 2026-11-09). All optional, all None
    # when the venue omits them: never inferred, never zero-filled.
    # yes_bid_size / yes_ask_size: the market object's *_size_fp, verbatim.
    # no_bid_size / no_ask_size: the order book only (with_sizes=True). Kalshi
    # publishes bids on both sides and no asks: "a yes bid at 7c is the same
    # as a no ask at 93c, with identical contract sizes" (docs.kalshi.com,
    # Get Market Orderbook). So no_bid_size is the best NO bid level's size
    # and no_ask_size the best YES bid level's size, by that equivalence.
    yes_bid_size: float | None = None
    yes_ask_size: float | None = None
    no_bid_size: float | None = None
    no_ask_size: float | None = None
    last_price: float | None = None    # last trade, *_dollars convention
    market_status: str | None = None   # venue `status`, verbatim
    result: str | None = None          # venue `result`, verbatim ("" unsettled)

    @property
    def yes_means(self) -> str:
        """What buying YES is, in betting terms."""
        if self.kind == "spread":
            return f"{self.team_code} {self.spread_line:+g}"
        if self.kind == "over":
            who = f"{self.subject} " if self.subject else ""
            return f"{who}over {self.over_line:g}"
        return self.outcome or ""


def _num(market: dict, field: str) -> float | None:
    try:
        return float(market.get(field))
    except (TypeError, ValueError):
        return None


def _verbatim_str(market: dict, field: str) -> str | None:
    """The venue's string as sent ("" included), or None if absent/not a str."""
    v = market.get(field)
    return v if isinstance(v, str) else None


def _is_half(x: float) -> bool:
    return abs(x - math.floor(x) - 0.5) < 1e-9


def _is_int(x: float) -> bool:
    return abs(x - round(x)) < 1e-9


def parse_contract(m: dict, *, spread: bool,
                   fees: dict | None = None) -> LadderContract:
    """Normalise one /markets row. Raises StrikeParseError rather than guess.

    fees: the series' {"fee_type", "fee_multiplier"} from fetch_series_fees,
    attached verbatim to the contract; None leaves them None."""
    ticker = m.get("ticker") or "?"
    stype = m.get("strike_type")
    floor = _num(m, "floor_strike")
    sub = (m.get("yes_sub_title") or "").strip()
    common = {
        "ticker": ticker, "event_ticker": m.get("event_ticker") or "",
        "yes_bid": _dollars(m, "yes_bid"), "yes_ask": _dollars(m, "yes_ask"),
        "no_bid": _dollars(m, "no_bid"), "no_ask": _dollars(m, "no_ask"),
        "volume": _num(m, "volume_fp"),
        "open_interest": _num(m, "open_interest_fp"),
        "close_time": m.get("close_time"), "subtitle": sub,
        "strike_type": stype or "", "floor_strike": floor,
        "rules_primary": m.get("rules_primary") or "",
        "rules_secondary": m.get("rules_secondary") or "",
        "fee_type": (fees or {}).get("fee_type"),
        "fee_multiplier": (fees or {}).get("fee_multiplier"),
        "yes_bid_size": _num(m, "yes_bid_size_fp"),
        "yes_ask_size": _num(m, "yes_ask_size_fp"),
        # Same convention as the quotes: a price outside (0, 1), "0.0000"
        # for no trade yet, is no price.
        "last_price": _dollars(m, "last_price"),
        "market_status": _verbatim_str(m, "status"),
        "result": _verbatim_str(m, "result")}

    if stype == "structured":
        if spread:
            raise StrikeParseError(f"{ticker}: structured contract on a "
                                   "spread series")
        if not sub:
            raise StrikeParseError(f"{ticker}: structured contract with no "
                                   "outcome name")
        return LadderContract(kind="outcome", over_line=None, team_code=None,
                              spread_line=None, outcome=sub, subject=None,
                              **common)

    if floor is None:
        raise StrikeParseError(f"{ticker}: strike_type {stype!r} with no "
                               "floor_strike")
    if stype == "greater" and _is_half(floor):
        over = floor
    elif stype == "greater_or_equal" and _is_int(floor):
        over = floor - 0.5
    else:
        raise StrikeParseError(f"{ticker}: unsupported strike {stype!r} "
                               f"floor {floor:g}")

    # Independent cross-check from the subtitle: "k+" is over k - 0.5, and
    # "over X" / "more than X" is over X. Either must agree with the strike.
    for rx, to_line, label in ((_KPLUS_RE, lambda v: v - 0.5, "k+"),
                               (_OVER_RE, lambda v: v, "over")):
        hit = rx.search(sub)
        if hit and abs(to_line(float(hit.group(1))) - over) > 1e-9:
            raise StrikeParseError(
                f"{ticker}: subtitle {sub!r} ({label}) disagrees with "
                f"strike {stype} {floor:g}")

    subject = sub.split(":", 1)[0].strip() if ":" in sub else None
    if not spread:
        return LadderContract(kind="over", over_line=over, team_code=None,
                              spread_line=None, outcome=None, subject=subject,
                              **common)

    tm = _TEAM_SUFFIX_RE.search(ticker)
    if not tm or not _SPREAD_TEXT_RE.search(sub):
        raise StrikeParseError(f"{ticker}: spread contract without a team "
                               f"code and 'wins by over' text: {sub!r}")
    subject = _SPREAD_TEXT_RE.split(sub, 1)[0].strip() or None
    return LadderContract(kind="spread", over_line=over,
                          team_code=tm.group("code"), spread_line=-over,
                          outcome=None, subject=subject, **common)


def fetch_series_fees(series: str) -> dict | None:
    """{"fee_type", "fee_multiplier"} for a series, verbatim, or None when the
    read failed. Rides the per-page 429 retry. Values are passed through as
    the venue sent them, unvalidated: oddsmath.kalshi_taker_fee decides what
    it can price and returns None for the rest."""
    try:
        data = _get_page(f"series/{series}", {})
    except Exception as e:  # noqa: BLE001
        print(f"kalshi: series fee read failed, {_failure_note(series, e)}")
        return None
    sr = (data or {}).get("series") or {}
    if "fee_type" not in sr and "fee_multiplier" not in sr:
        return None
    return {"fee_type": sr.get("fee_type"),
            "fee_multiplier": sr.get("fee_multiplier")}


# The venue's own `status` filter values on GET /markets. Note the filter is
# not the field: status="settled" returns markets whose `status` reads
# "finalized" (observed 2026-11-09). None sends no filter at all.
LADDER_STATUSES = ("open", "closed", "settled", None)
ORDERBOOK_BATCH = 100          # GET /markets/orderbooks takes 1-100 tickers


def _best_size(levels: object) -> float | None:
    """Size at the best (highest-priced) bid level, or None for an empty or
    malformed side. Levels arrive as [[price, size], ...] strings, listed
    worst first, so the best is picked by price, never by position."""
    best: tuple[float, float] | None = None
    for lvl in levels if isinstance(levels, list) else []:
        try:
            price, size = float(lvl[0]), float(lvl[1])
        except (TypeError, ValueError, IndexError):
            continue
        if best is None or price > best[0]:
            best = (price, size)
    return best[1] if best else None


def _fetch_book_sizes(tickers: list[str]) -> tuple[dict, list[str]]:
    """{ticker: (yes_best_bid_size, no_best_bid_size)} from the batch order
    book, plus failure notes. One read per ORDERBOOK_BATCH tickers, riding
    the per-page 429 retry and pacing. A failed batch costs that batch's
    sizes, never the capture.

    The tickers go as REPEATED `tickers=` keys. A comma-joined value is
    answered 200 with one empty book keyed by the joined string (observed
    2026-11-09), which would read as "no size" on every contract."""
    out: dict = {}
    notes: list[str] = []
    for i in range(0, len(tickers), ORDERBOOK_BATCH):
        batch = tickers[i:i + ORDERBOOK_BATCH]
        if i:
            _sleep(PAGE_PACING_SECONDS)
        query = urllib.parse.urlencode([("tickers", t) for t in batch])
        try:
            data = _get_page(f"markets/orderbooks?{query}", {})
        except Exception as e:  # noqa: BLE001
            notes.append(_failure_note(f"orderbooks[{len(batch)}]", e))
            continue
        for ob in (data or {}).get("orderbooks") or []:
            book = (ob or {}).get("orderbook_fp") or {}
            out[(ob or {}).get("ticker")] = (_best_size(book.get("yes_dollars")),
                                             _best_size(book.get("no_dollars")))
    return out, notes


def _with_book_sizes(contracts: list[LadderContract],
                     ) -> tuple[list[LadderContract], str]:
    sizes, notes = _fetch_book_sizes([c.ticker for c in contracts])
    filled = []
    for c in contracts:
        if c.ticker not in sizes:
            filled.append(c)
            continue
        yes_bid_sz, no_bid_sz = sizes[c.ticker]
        filled.append(replace(
            c, no_bid_size=no_bid_sz, no_ask_size=yes_bid_sz,
            # the market object's own sizes win; the book only fills a gap
            yes_bid_size=c.yes_bid_size if c.yes_bid_size is not None else yes_bid_sz,
            yes_ask_size=c.yes_ask_size if c.yes_ask_size is not None else no_bid_sz))
    missing = sum(1 for c in contracts if c.ticker not in sizes)
    note = ""
    if notes or missing:
        note = (f"order book sizes unread for {missing} of {len(contracts)} "
                f"contracts" + (f" ({'; '.join(notes[:3])})" if notes else ""))
    return filled, note


def fetch_ladder(series: str, event_ticker: str | None = None, *,
                 sport: str, spread: bool | None = None, strict: bool = True,
                 status: str | None = "open", with_sizes: bool = False,
                 ) -> tuple[list[LadderContract] | None, SourceReport]:
    """Every contract on a Kalshi series (optionally one event), parsed.

    Capture scope stays permanently decoupled from publication scope: this
    returns what the venue offers, and nothing about it says any of it may
    publish. Kalshi prices are exchange data under the venue register's
    accepted-risk clearance; raw rows never reach a published artifact.

    Three read outcomes, never merged:
      capture    -> (contracts, ok=True)
      empty read -> ([], ok=True): the venue answered with no markets
      failed     -> (None, ok=False): it did not answer (a 429 that outlived
                    the per-page retries is congestion, not an answer)

    strict=True raises StrikeParseError on the first contract it cannot read
    with certainty. strict=False keeps the ones it can and names the rest in
    the report note; it never guesses at them. `spread` defaults to whether
    the series name contains SPREAD. No liquidity filter is applied: a
    one-sided or wide contract is returned with its None sides so the caller
    sees the real book, and decides for itself what it will trade into.

    Every contract carries its settlement rules verbatim (rules_primary,
    rules_secondary) and the series' fee_type / fee_multiplier, read from
    /series/{ticker} in the same call. A failed fee read does not fail the
    capture: the contracts come back with None fees, the note says so, and
    kalshi_ev_at_ask then refuses to claim EV on them.

    status: "open" (default), "closed", "settled", or None for no filter;
    anything else raises ValueError before a request is made. with_sizes:
    also read the batch order book (one request per 100 contracts) to fill
    no_bid_size / no_ask_size; a failed size read leaves them None and says
    so in the note, never failing the capture. The YES sizes come from the
    market list and the NO sizes from the book: two reads seconds apart, so
    on a live book they can disagree (2026-11-09: 7 of 62 NBA game markets
    at the same price showed a different size between the two reads). Treat
    them as two observations, not one snapshot.
    """
    if status not in LADDER_STATUSES:
        raise ValueError(f"status must be one of {LADDER_STATUSES}, got {status!r}")
    if spread is None:
        spread = "SPREAD" in series.upper()
    params = {"series_ticker": series, "limit": 1000}
    if status is not None:
        params["status"] = status
    if event_ticker:
        params["event_ticker"] = event_ticker
    market = "ladder"
    try:
        rows = _paged("markets", "markets", **params)
    except Exception as e:  # noqa: BLE001
        return None, SourceReport(BOOK, sport, market, 0, False,
                                  _failure_note(series, e))

    fees = fetch_series_fees(series) if rows else None
    out: list[LadderContract] = []
    bad: list[str] = []
    for m in rows:
        try:
            out.append(parse_contract(m, spread=spread, fees=fees))
        except StrikeParseError:
            if strict:
                raise
            bad.append(m.get("ticker") or "?")
    out.sort(key=lambda c: (c.event_ticker, c.team_code or "",
                            c.over_line if c.over_line is not None else 0.0,
                            c.outcome or ""))
    parts = []
    if bad:
        parts.append(f"{len(bad)} unparseable strikes skipped: {bad[:5]}"
                     f"{' ...' if len(bad) > 5 else ''}")
    if rows and fees is None:
        parts.append("series fee terms unread: no EV on these contracts")
    if with_sizes and out:
        out, size_note = _with_book_sizes(out)
        if size_note:
            parts.append(size_note)
    return out, SourceReport(BOOK, sport, market, len(out), True,
                             "; ".join(parts))


def fetch_market(ticker: str, *, sport: str, spread: bool | None = None,
                 ) -> tuple[LadderContract | None, SourceReport]:
    """One market by ticker, for settlement polling. Same parse as
    fetch_ladder, same three read outcomes, never merged:

      capture    -> (contract, ok=True)
      empty read -> (None, ok=True): the venue answered without a market
      failed     -> (None, ok=False): no answer, or a strike that cannot be
                    read; a ticker the venue reports NOT FOUND (HTTP 404) is
                    a failed read with the reason named, never an empty one

    Fee terms are a series read and are not made here (fee_type and
    fee_multiplier stay None): use fetch_series_fees when pricing. `spread`
    defaults to whether the ticker's series contains SPREAD. Capture scope
    stays permanently decoupled from publication scope.
    """
    if spread is None:
        spread = "SPREAD" in ticker.upper().split("-", 1)[0]
    market = "market"
    try:
        data = _get_page(f"markets/{urllib.parse.quote(ticker, safe='')}", {})
    except Exception as e:  # noqa: BLE001
        if getattr(e, "code", None) == 404:
            return None, SourceReport(BOOK, sport, market, 0, False,
                                      f"{ticker}: HTTP 404 not found")
        return None, SourceReport(BOOK, sport, market, 0, False,
                                  _failure_note(ticker, e))
    m = (data or {}).get("market")
    if not m:
        return None, SourceReport(BOOK, sport, market, 0, True,
                                  f"{ticker}: venue answered without a market")
    try:
        contract = parse_contract(m, spread=spread)
    except StrikeParseError as e:
        return None, SourceReport(BOOK, sport, market, 0, False, str(e))
    return contract, SourceReport(BOOK, sport, market, 1, True, "")
