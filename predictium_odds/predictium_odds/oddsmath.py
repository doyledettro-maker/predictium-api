"""Odds conversions and de-vig — the single implementation.

De-vig method choice (org convention, from the repos' gated results):
- moneyline / two-way with favorite-longshot bias: devig_shin
- spreads/totals (near-symmetric two-way): devig_multiplicative or
  devig_proportional (identical for two-way implied probs)
- n-way outright/futures boards: devig_power (overround concentrated in the
  longshot tail; proportional over-credits longshots)
Kalshi quotes carry no vig — never de-vig an exchange mid.
"""

from __future__ import annotations

import math


def american_to_decimal(odds: float | int | None) -> float | None:
    if odds is None:
        return None
    odds = float(odds)
    if odds == 0:
        return None
    return 1 + (odds / 100 if odds > 0 else 100 / -odds)


def american_to_implied(odds: float | int | None) -> float | None:
    d = american_to_decimal(odds)
    return None if d is None else 1 / d


def decimal_to_american(dec: float | None) -> int | None:
    if dec is None or dec <= 1.0:
        return None
    return int(round((dec - 1) * 100)) if dec >= 2.0 \
        else int(round(-100 / (dec - 1)))


def prob_to_american(prob: float) -> int:
    """American odds at fair probability `prob` (also: exchange price -> odds)."""
    prob = min(max(float(prob), 1e-6), 1 - 1e-6)
    if prob >= 0.5:
        return int(round(-100 * prob / (1 - prob)))
    return int(round(100 * (1 - prob) / prob))


def devig_multiplicative(odds_a: float, odds_b: float) -> tuple[float, float]:
    """Two-way multiplicative vig removal from American prices."""
    pa, pb = american_to_implied(odds_a), american_to_implied(odds_b)
    s = pa + pb
    return pa / s, pb / s


def devig_proportional(p_a: float, p_b: float) -> tuple[float, float]:
    """Two-way proportional de-vig on implied probabilities."""
    s = p_a + p_b
    return p_a / s, p_b / s


def devig_shin(p_a: float, p_b: float) -> tuple[float, float]:
    """Shin (1993) two-outcome de-vig (ported from wnba_model.betting.ev —
    the org's canonical ML de-vig): solves for insider mass z, correcting
    the favorite-longshot bias proportional de-vig leaves in. Falls back to
    proportional when the market has no overround."""
    s = p_a + p_b
    if s <= 1.0:
        return devig_proportional(p_a, p_b)
    z = ((s - 1.0) * (p_a * p_b * 4 / s - (s - 1.0))
         / (p_a * p_b * 4 / s - 1.0)) if p_a * p_b * 4 / s != 1.0 else 0.0
    z = max(0.0, min(z, 0.2))

    def shin_prob(pi: float) -> float:
        return (math.sqrt(z ** 2 + 4 * (1 - z) * pi ** 2 / s) - z) \
            / (2 * (1 - z))

    q_a, q_b = shin_prob(p_a), shin_prob(p_b)
    t = q_a + q_b
    return q_a / t, q_b / t


def devig_power(decimals: dict[str, float]) -> dict[str, float]:
    """n-outcome power-method de-vig (ported from tennis_model.models.devig —
    the org's canonical futures de-vig). Fits p_i = (1/odds_i)^k with k
    chosen by bisection so probs sum to 1; longshots shrink harder than
    favorites, matching how books load outright vig. Requires >= 2 runners
    with decimal > 1; handles under-round boards with k < 1."""
    imp = {r: 1.0 / d for r, d in decimals.items() if d and d > 1.0}
    if len(imp) < 2:
        raise ValueError("need >= 2 priced runners to de-vig")

    def total(k: float) -> float:
        return sum(q ** k for q in imp.values())

    lo, hi = 0.25, 1.0
    if total(1.0) >= 1.0:            # normal overround: k in [1, 40+]
        lo, hi = 1.0, 40.0
        while total(hi) > 1.0 and hi < 4096:
            hi *= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if total(mid) > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2
    return {r: q ** k for r, q in imp.items()}


def expected_value_pct(model_prob: float, odds: float | int) -> float:
    """EV per unit staked, in percent, at the quoted American odds."""
    d = american_to_decimal(odds)
    return 100 * (model_prob * (d - 1) - (1 - model_prob))


def kelly_fraction(model_prob: float, odds: float | int,
                   multiplier: float = 0.25) -> float:
    """Fractional Kelly (quarter-Kelly default). 0 when no edge."""
    d = american_to_decimal(odds)
    b = (d or 1.0) - 1
    if b <= 0:
        return 0.0
    f = (model_prob * (b + 1) - 1) / b
    return max(0.0, f * multiplier)
