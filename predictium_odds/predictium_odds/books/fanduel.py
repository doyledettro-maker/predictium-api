"""FanDuel sbapi (public web-client key, keyless). Generalized from the four
per-repo copies (which had drifted: MLB was on the .mi host — unify on nj
unless a sport's markets are geo-gated).

Known fragility: FanDuel restructures lobby pages silently (the July 2026
win-total break). Every fetch returns a SourceReport; pipelines must treat
an empty FanDuel result as a health event, never as "no games today".
"""

from __future__ import annotations

import re
import urllib.request

from predictium_odds.books.base import UA, SportConfig, get_json, utcnow_iso
from predictium_odds.health import SourceReport
from predictium_odds.schema import (
    MARKET_MONEYLINE, MARKET_SPREAD, MARKET_TOTAL, MARKET_WIN_TOTAL,
    SIDE_AWAY, SIDE_HOME, SIDE_OVER, SIDE_UNDER, Quote,
)

BOOK = "fanduel"
APP_KEY = "FhMFpcPWXMeyZxOx"  # public web-client key, same across states

GAME_MARKET_TYPES = {
    "MONEY_LINE": MARKET_MONEYLINE,
    "MATCH_HANDICAP_(2-WAY)": MARKET_SPREAD,
    "TOTAL_POINTS_(OVER/UNDER)": MARKET_TOTAL,
    "MATCH_BETTING": MARKET_MONEYLINE,       # tennis
    "RUN_LINE": MARKET_SPREAD,               # mlb
    "TOTAL_RUNS_(OVER/UNDER)": MARKET_TOTAL, # mlb
}


WEB_BASE = "https://sportsbook.fanduel.com"
_BUILD_ID_RE = re.compile(r'"buildId":"([^"]+)"')
_OU_RE = re.compile(r"\b(Over|Under)\s+(\d+(?:\.\d+)?)\s+Wins")


def _get(cfg: SportConfig, path: str, **params):
    base = f"https://sbapi.{cfg.fanduel_state}.sportsbook.fanduel.com/api"
    return get_json(f"{base}/{path}", {**params, "_ak": APP_KEY})


def _runner_odds(runner: dict) -> int | None:
    try:
        return int(runner["winRunnerOdds"]["americanDisplayOdds"]
                   ["americanOdds"])
    except (KeyError, TypeError, ValueError):
        return None


def fetch_game_lines(cfg: SportConfig) -> tuple[list[Quote], SourceReport]:
    ts = utcnow_iso()
    try:
        data = _get(cfg, "content-managed-page", page="CUSTOM",
                    customPageId=cfg.fanduel_page_id, pbHorizontal="false",
                    timezone="America/New_York")
    except Exception as e:  # noqa: BLE001
        return [], SourceReport(BOOK, cfg.sport, "*", 0, False, f"{e}")

    att = data.get("attachments", {})
    events = att.get("events", {})
    quotes: list[Quote] = []
    unresolved = 0
    for mkt in att.get("markets", {}).values():
        market = GAME_MARKET_TYPES.get(mkt.get("marketType", ""))
        if not market:
            continue
        ev = events.get(str(mkt.get("eventId"))) or {}
        name = ev.get("name") or ""
        sep = " @ " if " @ " in name else " v " if " v " in name else None
        if not sep:
            continue
        away_raw, home_raw = name.split(sep, 1)
        home = cfg.resolve_team(home_raw.strip())
        away = cfg.resolve_team(away_raw.strip())
        if not home or not away:
            unresolved += 1
            continue
        ekey = cfg.event_key(home, away, str(ev.get("openDate")))
        sids = {"event_id": mkt.get("eventId"), "market_id": mkt.get("marketId")}
        for r in mkt.get("runners", []):
            price = _runner_odds(r)
            if price is None:
                continue
            rname = (r.get("runnerName") or "").strip()
            handicap = r.get("handicap")
            if market == MARKET_TOTAL:
                side = SIDE_OVER if rname.lower().startswith("over") \
                    else SIDE_UNDER
                quotes.append(Quote(cfg.sport, ekey, BOOK, market, side,
                                    price, line=handicap, ts=ts,
                                    source_ids=sids))
            else:
                side = SIDE_HOME if cfg.resolve_team(rname) == home \
                    else SIDE_AWAY
                line = None
                if market == MARKET_SPREAD and handicap is not None:
                    # store home-framed line (betting convention)
                    line = handicap if side == SIDE_HOME else -handicap
                quotes.append(Quote(cfg.sport, ekey, BOOK, market, side,
                                    price, line=line, ts=ts, source_ids=sids))
    note = f"{unresolved} unresolved events" if unresolved else ""
    return quotes, SourceReport(BOOK, cfg.sport, "*", len(quotes), True, note)


# ---------------------------------------------------------------- team pages
# The July 2026 lobby restructure moved season futures (win-total ladders
# etc.) off the customPageId lobbies onto per-team website pages, served as
# Next.js data routes (found by Claudia via devtools capture, 2026-07-23):
#   {WEB_BASE}/teams/_next/data/{buildId}/{sport}/{team-slug}/odds.json
# The build id is deployment-specific: discover it from a team page's HTML
# every run, never hard-code it.


def _get_web(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def discover_build_id(sport: str, slug: str) -> str | None:
    """Current Next.js build id from one team page's HTML. None on failure."""
    try:
        html = _get_web(f"{WEB_BASE}/teams/{sport}/{slug}/odds").decode(
            "utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"books.fanduel: build-id discovery failed: {e}")
        return None
    m = _BUILD_ID_RE.search(html)
    return m.group(1) if m else None


def fetch_win_totals(cfg: SportConfig, slugs: dict[str, str],
                     market_type: str = "REGULAR_SEASON_WINS_SGP",
                     ) -> tuple[list[Quote], SourceReport]:
    """Season win-total ladder quotes from the per-team pages.

    slugs: {team_abbr: url-slug}. One request per team plus one build-id
    discovery. Every over/under rung of the ladder is emitted as a Quote
    (event_key = team abbr, line = rung); main-line selection is downstream
    (lines.align_main_line against consensus, or closest-to-even).
    """
    ts = utcnow_iso()
    first_slug = next(iter(slugs.values()), None)
    build_id = first_slug and discover_build_id(cfg.sport, first_slug)
    if not build_id:
        return [], SourceReport(BOOK, cfg.sport, MARKET_WIN_TOTAL, 0, False,
                                "build-id discovery failed")
    quotes: list[Quote] = []
    for abbr, slug in slugs.items():
        url = (f"{WEB_BASE}/teams/_next/data/{build_id}/{cfg.sport}/{slug}"
               f"/odds.json?sport={cfg.sport}&team={slug}")
        try:
            rows = (get_json(url).get("pageProps", {})
                    .get("headerProps", {}).get("team", {})
                    .get("teamFutures") or [])
        except Exception as e:  # noqa: BLE001
            print(f"books.fanduel: team futures failed for {slug}: {e}")
            continue
        for row in rows:
            if row.get("marketType") != market_type:
                continue
            if " - Regular Season Wins" not in (row.get("description") or ""):
                continue  # skip H2H "To Have More Wins Than" variants
            m = _OU_RE.search(row.get("text") or "")
            if not m:
                continue
            try:
                price = int(str(row.get("odds")).replace("EVEN", "100"))
            except (TypeError, ValueError):
                continue
            quotes.append(Quote(
                cfg.sport, abbr, BOOK, MARKET_WIN_TOTAL,
                m.group(1).lower(), price, line=float(m.group(2)), ts=ts,
                source_ids={"team_slug": slug, "build_id": build_id}))
    return quotes, SourceReport(BOOK, cfg.sport, MARKET_WIN_TOTAL,
                                len(quotes), True)
