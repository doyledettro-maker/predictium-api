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
ladder_by_line() + lines.align_main_line(). "Over L" == "wins >= L + 0.5".
Integer book lines (push semantics) have no equivalent binary contract.
Never nearest-strike; a missing equivalent is a loud skip.

Liquidity: treat thin contracts like thin books — quotes with a spread wider
than MAX_SPREAD are dropped (report notes how many), the same posture the
repos take toward stale/pulled book lines.
"""

from __future__ import annotations

import re

from predictium_odds.books.base import SportConfig, get_json, utcnow_iso
from predictium_odds.health import SourceReport
from predictium_odds.oddsmath import prob_to_american
from predictium_odds.schema import MARKET_MONEYLINE, Quote

BASE = "https://api.elections.kalshi.com/trade-api/v2"
BOOK = "kalshi"
MAX_SPREAD = 0.10  # yes_ask - yes_bid above this = too thin to quote


def _get(path: str, **params):
    return get_json(f"{BASE}/{path}", params,
                    headers={"Accept": "application/json"})


def _paged(path: str, key: str, **params) -> list[dict]:
    out: list[dict] = []
    cursor = None
    for _ in range(50):  # hard stop — never loop forever on a bad cursor
        if cursor:
            params["cursor"] = cursor
        data = _get(path, **params)
        out.extend(data.get(key, []))
        cursor = data.get("cursor")
        if not cursor:
            break
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
                                f"{series}: {e}")

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
                                f"{series}: {e}")

    ladders: dict[str, list[dict]] = {}
    unmapped: set[str] = set()
    for m in markets:
        em = _LADDER_EVENT_RE.search(m.get("event_ticker") or "")
        strike = m.get("floor_strike")
        if not em or m.get("strike_type") != "greater_or_equal" \
                or strike is None:
            continue
        code = em.group("code")
        team = (team_code_map or {}).get(code, code)
        prices = _two_sided(m)
        if prices is None:
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
    note = f"unmapped codes {sorted(unmapped)}" if unmapped else ""
    return ladders, SourceReport(BOOK, cfg.sport, market_key,
                                 sum(len(v) for v in ladders.values()),
                                 True, note)


def ladder_by_line(rungs: list[dict]) -> dict[float, dict]:
    """Strike ladder re-keyed by book-equivalent line for align_main_line:
    contract "wins >= N" prices exactly the book's "Over N - 0.5"."""
    return {r["strike"] - 0.5: r for r in rungs}
