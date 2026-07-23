"""Bovada public JSON (keyless). Generalized from the four per-repo copies.

Endpoints:
  game lines:  {BASE}{cfg.bovada_path}                (e.g. /football/nfl)
  futures:     {BASE}/<sport futures path>            (e.g.
               /football/nfl-regular-season-wins — per-team O/U boards)

Only "Game"/"Match"-period markets are kept for game lines (drops
halves/quarters). Odds "EVEN" -> +100.
"""

from __future__ import annotations

import re

from predictium_odds.books.base import SportConfig, get_json, utcnow_iso
from predictium_odds.health import SourceReport
from predictium_odds.schema import (
    MARKET_MONEYLINE, MARKET_SPREAD, MARKET_TOTAL, MARKET_WIN_TOTAL,
    SIDE_AWAY, SIDE_HOME, SIDE_OVER, SIDE_UNDER, Quote,
)

BASE = "https://www.bovada.lv/services/sports/event/v2/events/A/description"
BOOK = "bovada"
GAME_PERIODS = ("", "Game", "Match")


def _american(price: dict | None) -> int | None:
    a = (price or {}).get("american")
    if a in (None, ""):
        return None
    if a == "EVEN":
        return 100
    try:
        return int(a)
    except (TypeError, ValueError):
        return None


def _handicap(outcome: dict) -> float | None:
    try:
        return float((outcome.get("price") or {}).get("handicap"))
    except (TypeError, ValueError):
        return None


def fetch_game_lines(cfg: SportConfig) -> tuple[list[Quote], SourceReport]:
    ts = utcnow_iso()
    try:
        data = get_json(BASE + cfg.bovada_path)
    except Exception as e:  # noqa: BLE001 — degrade soft, report loud
        return [], SourceReport(BOOK, cfg.sport, "*", 0, False, f"{e}")

    quotes: list[Quote] = []
    unresolved = 0
    for grp in data:
        for ev in grp.get("events", []):
            comps = {c.get("home"): c.get("name")
                     for c in ev.get("competitors", [])}
            home = cfg.resolve_team((comps.get(True) or "").strip())
            away = cfg.resolve_team((comps.get(False) or "").strip())
            if not home or not away:
                unresolved += 1
                continue
            # normalize epoch-ms to UTC ISO so cfg.event_key sees the same
            # time format from every adapter
            start = ev.get("startTime")
            if isinstance(start, (int, float)):
                from datetime import datetime, timezone
                start = datetime.fromtimestamp(
                    start / 1000, tz=timezone.utc).isoformat()
            ekey = cfg.event_key(home, away, str(start))
            sids = {"event_link": ev.get("link")}
            for dg in ev.get("displayGroups", []):
                for mkt in dg.get("markets", []):
                    period = (mkt.get("period") or {}).get("description", "")
                    if period not in GAME_PERIODS:
                        continue
                    desc = (mkt.get("description") or "").lower()
                    outs = mkt.get("outcomes", [])
                    if desc == "moneyline":
                        for o in outs:
                            side = SIDE_HOME if o.get("description") == \
                                comps.get(True) else SIDE_AWAY
                            if (p := _american(o.get("price"))) is not None:
                                quotes.append(Quote(
                                    cfg.sport, ekey, BOOK, MARKET_MONEYLINE,
                                    side, p, ts=ts, source_ids=sids))
                    elif desc in ("point spread", "spread", "game spread",
                                  "runline", "run line", "puck line"):
                        for o in outs:
                            side = SIDE_HOME if o.get("description") == \
                                comps.get(True) else SIDE_AWAY
                            h, p = _handicap(o), _american(o.get("price"))
                            if h is not None and p is not None:
                                # betting convention: store home-framed line
                                quotes.append(Quote(
                                    cfg.sport, ekey, BOOK, MARKET_SPREAD,
                                    side, p,
                                    line=h if side == SIDE_HOME else -h,
                                    ts=ts, source_ids=sids))
                    elif desc == "total":
                        for o in outs:
                            side = SIDE_OVER if (o.get("description") or "") \
                                .lower() == "over" else SIDE_UNDER
                            h, p = _handicap(o), _american(o.get("price"))
                            if h is not None and p is not None:
                                quotes.append(Quote(
                                    cfg.sport, ekey, BOOK, MARKET_TOTAL,
                                    side, p, line=h, ts=ts, source_ids=sids))
    note = f"{unresolved} unresolved events" if unresolved else ""
    return quotes, SourceReport(BOOK, cfg.sport, "*", len(quotes), True, note)


def fetch_win_totals(cfg: SportConfig, path: str,
                     market_re: str = r"Regular Season Wins",
                     ) -> tuple[list[Quote], SourceReport]:
    """Per-team season O/U boards (one event per team, team in the link slug).

    Verified live for NFL at /football/nfl-regular-season-wins; Bovada lists
    CFB conference win totals the same way.
    """
    ts = utcnow_iso()
    try:
        data = get_json(BASE + path)
    except Exception as e:  # noqa: BLE001
        return [], SourceReport(BOOK, cfg.sport, MARKET_WIN_TOTAL, 0, False,
                                f"{e}")

    quotes: list[Quote] = []
    pat = re.compile(market_re)
    for grp in data:
        for ev in grp.get("events", []):
            parts = (ev.get("link") or "").split("/")
            slug = parts[4] if len(parts) > 4 else ""
            team = cfg.resolve_team(
                " ".join(w.capitalize() for w in slug.split("-")))
            if not team:
                continue
            for dg in ev.get("displayGroups", []):
                for mkt in dg.get("markets", []):
                    if not pat.search(mkt.get("description") or ""):
                        continue
                    for o in mkt.get("outcomes", []):
                        m = re.match(r"(Over|Under)\s+(\d+(?:\.\d+)?)",
                                     o.get("description") or "")
                        p = _american(o.get("price"))
                        if not m or p is None:
                            continue
                        quotes.append(Quote(
                            cfg.sport, team, BOOK, MARKET_WIN_TOTAL,
                            m.group(1).lower(), p, line=float(m.group(2)),
                            ts=ts, source_ids={"event_link": ev.get("link")}))
    return quotes, SourceReport(BOOK, cfg.sport, MARKET_WIN_TOTAL,
                                len(quotes), True)
