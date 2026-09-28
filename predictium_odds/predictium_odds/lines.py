"""Line shopping, multi-book consensus, and main-line alignment.

align_main_line is the correctness rule of the whole layer: an alt-line
ladder (Kalshi strikes, book alt lines) is ONLY comparable to the market at
the entry equivalent to the consensus main line. Comparing any other rung
against main-line prices silently corrupts every EV/CLV number downstream —
so a missing equivalent returns None, loudly, and never the nearest rung.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from itertools import pairwise
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
    Kalshi ladder keyed by its over line. The over line depends on the strike
    type: a ">=" contract on integer N is over N - 0.5
    (books.kalshi.ladder_by_line), but a "greater" contract's half-point
    floor already IS the over line. Key by LadderContract.over_line from
    books.kalshi.fetch_ladder, which reads the type, rather than subtracting
    0.5 by habit. Returns the value at main_line within
    tol. No equivalent entry -> None; callers MUST surface that (health
    report / printed warning), never substitute a nearby line.
    """
    if not ladder or main_line is None:
        return None
    for line, entry in ladder.items():
        if abs(line - main_line) <= tol:
            return entry
    return None


# ---------------------------------------------------------------------------
# Pricing an offered strike from a model's own ladder (Doyle, 2026-09-27):
# every model prices the strike that is actually offered, whether a Kalshi
# "k+" rung or a Novig x.5 line, and settles at that strike.
#
# A ladder here is the MODEL's published distribution in over-line form:
# {over_line: P(outcome > over_line)}. It is not a market ladder. Keys are
# half-point over lines ("k+" is over k - 0.5; use kplus_to_over_line), so
# every key is a line that cannot push.
#
# No interpolation between grid points, by design. Scoring distributions are
# lumpy: in the NFL the mass sitting on 3 and 7 is exactly what separates
# -2.5 from -3.5, and a straight line drawn between the published neighbours
# of an unpublished strike smears that mass across it. A model that wants a
# strike priced publishes that strike. The one exception is a model that
# publishes a continuous form (its own CDF or survival function): the
# caller passes it as `continuous`, and it is evaluated only INSIDE the
# published grid's range. Nothing is ever extrapolated past the ends.
# ---------------------------------------------------------------------------

def kplus_to_over_line(k: float) -> float:
    """Kalshi "k+" (k or more) is the book line "over k - 0.5"."""
    return float(k) - 0.5


def _check_monotone(ladder: Mapping[float, float]) -> None:
    pts = sorted(ladder.items())
    for _, p in pts:
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"ladder probability out of [0, 1]: {p}")
    diffs = [b[1] - a[1] for a, b in pairwise(pts)]
    if any(d > 1e-12 for d in diffs) and any(d < -1e-12 for d in diffs):
        raise ValueError("ladder is not monotone in strike; refusing to "
                         "price from an incoherent distribution")


def price_at_strike(ladder: Mapping[float, float], strike: float, *,
                    continuous: Callable[[float], float] | None = None,
                    tol: float = 1e-9) -> float | None:
    """The model's probability at an offered strike, or None.

    ladder: {over_line: p}, monotone in strike (either direction, so a
    team-oriented spread ladder keyed by spread line works as well as an
    over ladder). A non-monotone or out-of-range ladder raises ValueError:
    that is a broken model output, not a missing price.

    Returns the ladder's own p when `strike` is on the grid (within tol).
    Off the grid: None, unless `continuous` is given and the strike lies
    within the grid's range, in which case continuous(strike). Outside the
    range: None, always. A None is a skip the caller must surface; it is
    never a reason to price the nearest rung instead.
    """
    if not ladder:
        raise ValueError("empty ladder")
    _check_monotone(ladder)
    for line, p in ladder.items():
        if abs(line - strike) <= tol:
            return float(p)
    if continuous is None:
        return None
    lo, hi = min(ladder), max(ladder)
    if not lo - tol <= strike <= hi + tol:
        return None
    p = float(continuous(strike))
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"continuous form returned {p} at {strike}")
    return p
