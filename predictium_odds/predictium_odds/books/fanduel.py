"""FanDuel sbapi (public web-client key, keyless). Generalized from the four
per-repo copies (which had drifted: MLB was on the .mi host — unify on nj
unless a sport's markets are geo-gated).

Known fragility: FanDuel restructures lobby pages silently (the July 2026
win-total break). Every fetch returns a SourceReport; pipelines must treat
an empty FanDuel result as a health event, never as "no games today".
"""

from __future__ import annotations

from predictium_odds.books.base import SportConfig, get_json, utcnow_iso
from predictium_odds.health import SourceReport
from predictium_odds.schema import (
    MARKET_MONEYLINE, MARKET_SPREAD, MARKET_TOTAL,
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
