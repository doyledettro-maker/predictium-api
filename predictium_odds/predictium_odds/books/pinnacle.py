"""Pinnacle guest API — INTERNAL-ONLY consensus anchor.

Approved by Doyle 2026-07-23 with eyes open. What this is: the private
backend of pinnacle.com's own web app, called with the public x-api-key
embedded in their JS — NOT their commercial B2B API product (which is what
"an API for automated access" would be; this guest surface exists to serve
their site, and their site ToS prohibits scraping). Same gray posture as our
Bovada/FanDuel scraping, one notch sharper because the key is explicit.

Risk containment, non-negotiable in code review:
- every Quote from this module is redistributable=False — Pinnacle numbers
  must NEVER appear in a published artifact or on the public S3 bucket.
  They feed internal consensus/fair-value only.
- low volume (one lobby call per sport per run), standard UA, no retries —
  if they block us, we log the health event and live without it.

League ids (guest API): NFL 889, NBA 487, WNBA 578, MLB 246, NCAAF 880,
ATP/WTA under sport 33 (per-tournament league ids).
"""

from __future__ import annotations

from predictium_odds.books.base import SportConfig, get_json, utcnow_iso
from predictium_odds.health import SourceReport
from predictium_odds.schema import (
    MARKET_MONEYLINE, MARKET_SPREAD, MARKET_TOTAL,
    SIDE_AWAY, SIDE_HOME, SIDE_OVER, SIDE_UNDER, Quote,
)

BASE = "https://guest.api.arcadia.pinnacle.com/0.1"
BOOK = "pinnacle"
# public key embedded in pinnacle.com's web bundle
API_KEY = "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi2R"

LEAGUE_IDS = {"nfl": 889, "nba": 487, "wnba": 578, "mlb": 246, "cfb": 880}


def _get(path: str, **params):
    return get_json(f"{BASE}/{path}", params or None,
                    headers={"x-api-key": API_KEY})


def fetch_game_lines(cfg: SportConfig) -> tuple[list[Quote], SourceReport]:
    league = LEAGUE_IDS.get(cfg.sport) or cfg.extras.get("pinnacle_league_id")
    if not league:
        return [], SourceReport(BOOK, cfg.sport, "*", 0, False,
                                "no league id configured")
    ts = utcnow_iso()
    try:
        matchups = _get(f"leagues/{league}/matchups")
        straight = _get(f"leagues/{league}/markets/straight")
    except Exception as e:  # noqa: BLE001
        return [], SourceReport(BOOK, cfg.sport, "*", 0, False, f"{e}")

    games: dict[int, dict] = {}
    for m in matchups:
        if m.get("type") != "matchup" or m.get("parentId"):
            continue
        sides = {p.get("alignment"): p.get("name")
                 for p in m.get("participants", [])}
        home = cfg.resolve_team(sides.get("home") or "")
        away = cfg.resolve_team(sides.get("away") or "")
        if home and away:
            games[m["id"]] = {
                "ekey": cfg.event_key(home, away, str(m.get("startTime"))),
            }

    quotes: list[Quote] = []
    for mkt in straight:
        g = games.get(mkt.get("matchupId"))
        if not g or mkt.get("period") != 0 or mkt.get("isAlternate"):
            continue
        mtype = mkt.get("type")
        sids = {"matchup_id": mkt.get("matchupId")}
        common = dict(sport=cfg.sport, event_key=g["ekey"], book=BOOK,
                      ts=ts, source_ids=sids, redistributable=False)
        for p in mkt.get("prices", []):
            price = p.get("price")
            if not isinstance(price, (int, float)):
                continue
            if mtype == "moneyline":
                side = SIDE_HOME if p.get("designation") == "home" \
                    else SIDE_AWAY
                quotes.append(Quote(market=MARKET_MONEYLINE, side=side,
                                    price_american=int(price), **common))
            elif mtype == "spread":
                side = SIDE_HOME if p.get("designation") == "home" \
                    else SIDE_AWAY
                pts = p.get("points")
                if pts is None:
                    continue
                line = pts if side == SIDE_HOME else -pts
                quotes.append(Quote(market=MARKET_SPREAD, side=side,
                                    price_american=int(price), line=line,
                                    **common))
            elif mtype == "total":
                side = SIDE_OVER if p.get("designation") == "over" \
                    else SIDE_UNDER
                pts = p.get("points")
                if pts is None:
                    continue
                quotes.append(Quote(market=MARKET_TOTAL, side=side,
                                    price_american=int(price), line=pts,
                                    **common))
    return quotes, SourceReport(BOOK, cfg.sport, "*", len(quotes), True)
