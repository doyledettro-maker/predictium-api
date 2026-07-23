"""Predictium shared odds layer.

One normalized Quote shape, one implementation of the odds math, one
line-shopping/consensus/main-line-alignment toolkit, and one health-report
contract that every book adapter emits — replacing the per-repo copies that
had already diverged (9 de-vig variants, 4 pasted FanDuel configs, one repo
still on a banned aggregator).

Install from the monorepo path:
    pip install "git+https://github.com/doyledettro-maker/predictium-api.git@<tag>#subdirectory=predictium_odds"

Rules encoded here (org invariants, do not weaken):
- The Odds API is banned. No adapter for it will ever be accepted.
- Alt-line ladders (Kalshi strikes, book alt lines) are ONLY comparable at
  the entry aligned to the consensus main line: lines.align_main_line.
  It fails loud (returns None + report) rather than picking a near strike.
- Every fetch emits a SourceReport; empty results are a signal, never
  silence.
- Quotes flagged redistributable=False (e.g. Pinnacle) must never reach the
  public S3 bucket or any published artifact.
"""

from predictium_odds.schema import Quote  # noqa: F401

__version__ = "0.1.0"
