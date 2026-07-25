"""Book-health monitoring: make silent single-source failure impossible.

Every adapter fetch produces a SourceReport. A repo's pipeline evaluates its
reports against per-sport CoverageSpecs and gets back (summary_line,
failures). The contract with callers:

- ALWAYS print the summary line (one glance = which sources are alive).
- Any failure is at minimum printed with "WARNING"; in-season pipelines
  should exit non-zero when EVERY source for a required market failed
  (the WNBA capture_lines exit-code-2 pattern, now shared).

The spec is deliberately coarse — "in season, book B should list >= N
events with market M for sport S". That is exactly the check that would
have caught the FanDuel win-total break the day it happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceReport:
    book: str
    sport: str
    market: str          # canonical market key, or "*" for a whole feed
    n_markets: int       # how many priced markets/events came back
    ok: bool             # transport-level success (False = error/timeout)
    note: str = ""       # short diagnostic ("HTTP 403", "0 aligned strikes")


@dataclass
class CoverageSpec:
    book: str
    sport: str
    market: str
    expected_min: int    # in-season floor; use 0 offseason (still reports)
    required: bool = False  # True => counts toward the all-down exit signal


@dataclass
class HealthResult:
    summary_line: str
    warnings: list[str] = field(default_factory=list)
    all_required_down: bool = False


def evaluate(reports: list[SourceReport],
             specs: list[CoverageSpec]) -> HealthResult:
    by_key = {(r.book, r.sport, r.market): r for r in reports}
    parts, warnings = [], []
    required_seen, required_ok = 0, 0
    for spec in specs:
        r = by_key.get((spec.book, spec.sport, spec.market))
        n = r.n_markets if r else 0
        ok = bool(r and r.ok and n >= spec.expected_min)
        parts.append(f"{spec.book}:{spec.market}={n}")
        if spec.required:
            required_seen += 1
            required_ok += ok
        if not ok:
            why = (r.note if r and r.note else
                   "no report" if not r else
                   "fetch failed" if not r.ok else
                   f"{n} < expected {spec.expected_min}")
            warnings.append(
                f"WARNING book-health: {spec.book} {spec.sport} "
                f"{spec.market}: {why}")
    # reports with no spec still show up in the summary (new sources)
    for r in reports:
        if (r.book, r.sport, r.market) not in {
                (s.book, s.sport, s.market) for s in specs}:
            parts.append(f"{r.book}:{r.market}={r.n_markets}")
    sport = specs[0].sport if specs else (reports[0].sport if reports else "?")
    return HealthResult(
        summary_line=f"book-health [{sport}]: " + " ".join(parts),
        warnings=warnings,
        all_required_down=(required_seen > 0 and required_ok == 0),
    )
