"""Book/exchange adapters. Each module exposes fetchers that return
(list[Quote], SourceReport) so health reporting can never be skipped.

Registry keys are the canonical book tags used in published JSON.
The Odds API ("theoddsapi") is authorized fleet-wide for live, ongoing and
historical reads (Doyle, 2026-11-21; handover §3). It is the one keyed
source: every read needs a CreditBudget, the key comes from the environment
only, and every row carries a license_class. Pinnacle rows and all historical
rows are internal-only (redistributable=False). Spec:
docs/THE_ODDS_API_CLIENT_SPEC.md.
"""

from predictium_odds.books import (
    bovada,
    espn,
    fanduel,
    kalshi,
    pinnacle,
    theoddsapi,
)

PROVIDERS = {
    "bovada": bovada,
    "fanduel": fanduel,
    "kalshi": kalshi,
    "espn_dk": espn,
    "pinnacle": pinnacle,
    "theoddsapi": theoddsapi,
}
