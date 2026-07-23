"""Book/exchange adapters. Each module exposes fetchers that return
(list[Quote], SourceReport) so health reporting can never be skipped.

Registry keys are the canonical book tags used in published JSON.
The Odds API is banned org-wide; no adapter for it will be accepted.
"""

from predictium_odds.books import bovada, espn, fanduel, kalshi, pinnacle

PROVIDERS = {
    "bovada": bovada,
    "fanduel": fanduel,
    "kalshi": kalshi,
    "espn_dk": espn,
    "pinnacle": pinnacle,
}
