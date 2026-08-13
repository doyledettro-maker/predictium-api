"""Book/exchange adapters. Each module exposes fetchers that return
(list[Quote], SourceReport) so health reporting can never be skipped.

Registry keys are the canonical book tags used in published JSON.
The Odds API is banned from ingestion org-wide; no adapter for it will be
accepted. Its 2026-08-02 historical-backfill carve-out is per-task, internal
only, and never authorizes an adapter here.
"""

from predictium_odds.books import bovada, espn, fanduel, kalshi, pinnacle

PROVIDERS = {
    "bovada": bovada,
    "fanduel": fanduel,
    "kalshi": kalshi,
    "espn_dk": espn,
    "pinnacle": pinnacle,
}
