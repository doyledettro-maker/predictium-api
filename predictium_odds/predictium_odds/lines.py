"""Line shopping, multi-book consensus, and main-line alignment.

align_main_line is the correctness rule of the whole layer: an alt-line
ladder (Kalshi strikes, book alt lines) is ONLY comparable to the market at
the entry equivalent to the consensus main line. Comparing any other rung
against main-line prices silently corrupts every EV/CLV number downstream —
so a missing equivalent returns None, loudly, and never the nearest rung.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median

from predictium_odds.oddsmath import american_to_decimal, decimal_to_american
from predictium_odds.schema import Quote


def best_line(quotes: list[Quote]) -> dict[tuple, Quote]:
    """(event_key, market, side, line) -> the best-priced Quote across books.

    "Best" = highest decimal payout for the bettor. Quotes at different
    lines are different keys — line shopping across lines is a modeling
    decision, not a price max.
    """
    best: dict[tuple, Quote] = {}
    for q in quotes:
        key = (q.event_key, q.market, q.side, q.line)
        cur = best.get(key)
        if cur is None or (american_to_decimal(q.price_american) or 0) > \
                (american_to_decimal(cur.price_american) or 0):
            best[key] = q
    return best


def consensus(quotes: list[Quote]) -> dict[tuple, dict]:
    """(event_key, market, side) -> {line, price_american, n_books}.

    Line = median across books; price = decimal-space mean converted back
    (the org's one correct consensus price impl, from MLB's exporter),
    computed only over books quoting the median line. Exchange quotes are
    included (their price is already fair; they sharpen the mean).
    """
    grouped: dict[tuple, list[Quote]] = defaultdict(list)
    for q in quotes:
        grouped[(q.event_key, q.market, q.side)].append(q)
    out: dict[tuple, dict] = {}
    for key, qs in grouped.items():
        lines = [q.line for q in qs if q.line is not None]
        line = median(lines) if lines else None
        at_line = [q for q in qs if q.line == line] or qs
        decs = [d for q in at_line
                if (d := american_to_decimal(q.price_american))]
        if not decs:
            continue
        out[key] = {
            "line": line,
            "price_american": decimal_to_american(sum(decs) / len(decs)),
            "n_books": len({q.book for q in at_line}),
        }
    return out


def align_main_line(ladder: dict[float, object], main_line: float | None,
                    tol: float = 0.01) -> object | None:
    """The one ladder entry priced at the consensus main line, or None.

    ladder: {line: anything} — book alt lines keyed by their line, or a
    Kalshi strike ladder pre-keyed by equivalent line (strike N -> N - 0.5;
    see books.kalshi.ladder_by_line). Returns the value at main_line within
    tol. No equivalent entry -> None; callers MUST surface that (health
    report / printed warning), never substitute a nearby line.
    """
    if not ladder or main_line is None:
        return None
    for line, entry in ladder.items():
        if abs(line - main_line) <= tol:
            return entry
    return None
