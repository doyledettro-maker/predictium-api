"""The normalized quote: one priced side of one market from one source.

Conventions (org-wide, already enforced at every repo's ingestion boundary):
- American prices as ints; exchange probability prices are converted at the
  adapter boundary (the raw exchange fields ride along in `extras`).
- Spread/handicap lines use betting convention: negative = home/favorite.
- implied_prob is the raw vig-inclusive probability of THIS side at THIS
  price; de-vig is a downstream choice (oddsmath), never baked into a Quote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SPORTS = ("nfl", "nba", "mlb", "wnba", "cfb", "tennis", "worldcup")

# canonical market keys; sport-specific prop keys are allowed as free strings
MARKET_MONEYLINE = "moneyline"
MARKET_SPREAD = "spread"
MARKET_TOTAL = "total"
MARKET_WIN_TOTAL = "win_total"
MARKET_OUTRIGHT = "outright"

SIDE_HOME, SIDE_AWAY = "home", "away"
SIDE_OVER, SIDE_UNDER = "over", "under"
SIDE_A, SIDE_B = "a", "b"  # tennis framing


@dataclass(frozen=True)
class Quote:
    sport: str
    event_key: str          # canonical, sport-local (e.g. "BUF@KC_2026-09-13")
    book: str               # "bovada" | "fanduel" | "kalshi" | "espn_dk" | ...
    market: str
    side: str               # home/away/over/under/a/b or runner name
    price_american: int
    line: float | None = None
    is_exchange: bool = False
    redistributable: bool = True   # False => never in published artifacts/S3
    ts: str = ""            # fetch time, UTC ISO
    source_ids: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def implied_prob(self) -> float:
        from predictium_odds.oddsmath import american_to_implied
        return american_to_implied(self.price_american)
