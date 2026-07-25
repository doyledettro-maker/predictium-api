"""DraftKings game lines via ESPN's public core API (keyless).

ESPN redistributes sportsbook odds on its own public, unauthenticated API —
the zero-bot-wall way to a third traditional book (DK direct 403s from cloud
egress). Verified live 2026-07-23: provider "DraftKings" (id 100) with
open/current spread, total, and moneylines per event.

Endpoints:
  events:  https://sports.core.api.espn.com/v2/sports/{league}/events
  odds:    .../events/{id}/competitions/{id}/odds

Book tag is "espn_dk" — the numbers are DK's, the transport is ESPN's; keep
that visible in published artifacts rather than claiming a direct DK feed.
"""

from __future__ import annotations

from predictium_odds.books.base import SportConfig, get_json, utcnow_iso
from predictium_odds.health import SourceReport
from predictium_odds.schema import (
    MARKET_MONEYLINE, MARKET_SPREAD, MARKET_TOTAL,
    SIDE_AWAY, SIDE_HOME, SIDE_OVER, SIDE_UNDER, Quote,
)

CORE = "https://sports.core.api.espn.com/v2/sports"
BOOK = "espn_dk"
DK_PROVIDER_ID = "100"


def _team_abbr(cfg: SportConfig, team_odds: dict) -> str | None:
    ref = ((team_odds.get("team") or {}).get("$ref") or "")
    # team $ref ends /teams/{id}; resolve via the odds item's cached fields
    # when present, else fetch the team object (rare path)
    try:
        team = get_json(ref.replace("http://", "https://"))
        return cfg.resolve_team(team.get("displayName") or "")
    except Exception:  # noqa: BLE001
        return None


def fetch_game_lines(cfg: SportConfig, limit: int = 100,
                     ) -> tuple[list[Quote], SourceReport]:
    ts = utcnow_iso()
    sport_slug, league_slug = cfg.espn_league.split("/", 1)
    try:
        events = get_json(
            f"{CORE}/{sport_slug}/leagues/{league_slug}/events",
            {"limit": limit}).get("items", [])
    except Exception as e:  # noqa: BLE001
        return [], SourceReport(BOOK, cfg.sport, "*", 0, False, f"{e}")

    quotes: list[Quote] = []
    errors = 0
    for item in events:
        ref = (item.get("$ref") or "").split("?")[0].replace("http://",
                                                             "https://")
        event_id = ref.rstrip("/").rsplit("/", 1)[-1]
        try:
            ev = get_json(ref)
            comp = (ev.get("competitions") or [{}])[0]
            comps = comp.get("competitors") or []
            home_c = next((c for c in comps if c.get("homeAway") == "home"), {})
            away_c = next((c for c in comps if c.get("homeAway") == "away"), {})
            home = cfg.resolve_team(
                ((home_c.get("team") or {}).get("displayName"))
                or get_json((home_c.get("team") or {}).get("$ref", "")
                            .replace("http://", "https://"))
                .get("displayName", ""))
            away = cfg.resolve_team(
                ((away_c.get("team") or {}).get("displayName"))
                or get_json((away_c.get("team") or {}).get("$ref", "")
                            .replace("http://", "https://"))
                .get("displayName", ""))
            if not home or not away:
                errors += 1
                continue
            ekey = cfg.event_key(home, away, str(ev.get("date")))
            odds_items = get_json(f"{ref}/competitions/{event_id}/odds") \
                .get("items", [])
        except Exception:  # noqa: BLE001
            errors += 1
            continue
        for oi in odds_items:
            if str((oi.get("provider") or {}).get("id")) != DK_PROVIDER_ID:
                continue
            sids = {"espn_event_id": event_id}
            hto, ato = oi.get("homeTeamOdds") or {}, oi.get("awayTeamOdds") or {}
            spread = oi.get("spread")  # ESPN spread is home-framed already
            for side, todds in ((SIDE_HOME, hto), (SIDE_AWAY, ato)):
                ml = todds.get("moneyLine")
                if isinstance(ml, (int, float)):
                    quotes.append(Quote(cfg.sport, ekey, BOOK,
                                        MARKET_MONEYLINE, side, int(ml),
                                        ts=ts, source_ids=sids))
                sp = todds.get("spreadOdds")
                if isinstance(sp, (int, float)) and spread is not None:
                    # ESPN's spread is already home-framed; both sides carry
                    # it as-is (org convention: negative = home favored)
                    quotes.append(Quote(cfg.sport, ekey, BOOK, MARKET_SPREAD,
                                        side, int(sp), line=spread, ts=ts,
                                        source_ids=sids))
            ou = oi.get("overUnder")
            for side, price in ((SIDE_OVER, oi.get("overOdds")),
                                (SIDE_UNDER, oi.get("underOdds"))):
                if isinstance(price, (int, float)) and ou is not None:
                    quotes.append(Quote(cfg.sport, ekey, BOOK, MARKET_TOTAL,
                                        side, int(price), line=ou, ts=ts,
                                        source_ids=sids))
    note = f"{errors} events skipped" if errors else ""
    return quotes, SourceReport(BOOK, cfg.sport, "*", len(quotes), True, note)
