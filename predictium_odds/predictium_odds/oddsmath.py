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
from dataclasses import dataclass


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


# ---------------------------------------------------------------------------
# Fee-inclusive EV at an exchange ask (Doyle, 2026-09-27). One implementation
# for every sport: tennis, WNBA, CFB, NFL and soccer all price Kalshi strikes
# through this, so a fee or settlement change is made once.
#
# Settlement is the org's: to win 1 unit at price q, risk q / (1 - q). At an
# exchange the price actually paid for a YES contract that pays $1 is the ask
# PLUS the fee, so q here is the fee-inclusive cost, never the bare ask. The
# de-vigged-price rule does not apply: an exchange ask carries no vig to
# remove, only a fee to add.
# ---------------------------------------------------------------------------

KALSHI_TAKER_FEE_COEFF = 0.07


def exchange_fee(price: float, contracts: int = 1, *,
                 coeff: float = KALSHI_TAKER_FEE_COEFF,
                 round_up: bool = False) -> float:
    """Taker fee in dollars on `contracts` contracts bought at `price`:
    coeff * contracts * price * (1 - price).

    round_up=True rounds the fee up to the next whole cent over the order.
    With contracts=1 that is a per-contract round-up, the most conservative
    reading (a 50c contract pays 2c, not 1.75c). How the venue rounds a given
    order is its schedule's call, not ours; this helper takes the order size
    so a caller can model whichever the venue applies. Returned as a total
    for the order; divide by `contracts` for per-contract.
    """
    if not 0.0 < price < 1.0:
        raise ValueError(f"price must be in (0, 1), got {price}")
    if contracts < 1:
        raise ValueError(f"contracts must be >= 1, got {contracts}")
    raw = coeff * contracts * price * (1.0 - price)
    if round_up:
        # round() first so 0.0175 * 100 = 1.7500000000000002 is not 2c by
        # float noise alone, while any real fraction of a cent still rounds up
        return math.ceil(round(raw * 100, 9)) / 100
    return raw


@dataclass(frozen=True)
class FeeInclusiveEV:
    """EV of buying one YES contract at `ask` when the model says `p`."""

    p: float
    ask: float
    fee: float                # per contract, dollars
    cost: float               # ask + fee: the price actually paid
    ev_per_contract: float    # p - cost, dollars per $1 contract
    ev_pct: float             # ev_per_contract / cost (return on cost)
    risk_to_win_1u: float     # cost / (1 - cost): units risked to win 1u
    ev_units: float           # expected units on a to-win-1u bet

    @property
    def breakeven(self) -> float:
        """The model probability at which this trade is worth nothing."""
        return self.cost


def ev_at_ask(p: float, ask: float, fee_per_contract: float = 0.0,
              ) -> FeeInclusiveEV:
    """EV at an executable ask plus a per-contract fee, venue-agnostic."""
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"model p must be in [0, 1], got {p}")
    if not 0.0 < ask < 1.0:
        raise ValueError(f"ask must be in (0, 1), got {ask}")
    if fee_per_contract < 0:
        raise ValueError("fee cannot be negative")
    cost = ask + fee_per_contract
    if cost >= 1.0:
        raise ValueError(f"ask {ask} + fee {fee_per_contract} >= 1: "
                         "the contract cannot pay out more than it costs")
    edge = p - cost
    return FeeInclusiveEV(
        p=p, ask=ask, fee=fee_per_contract, cost=cost, ev_per_contract=edge,
        ev_pct=edge / cost, risk_to_win_1u=cost / (1.0 - cost),
        ev_units=edge / (1.0 - cost))


def kalshi_ev_at_ask(p: float, ask: float, *, contracts: int = 1,
                     coeff: float = KALSHI_TAKER_FEE_COEFF,
                     round_up: bool = False) -> FeeInclusiveEV:
    """Fee-inclusive EV of buying Kalshi YES at `ask` with model prob `p`.

    To buy NO, pass p = 1 - p_yes and ask = the NO ask. The fee is the order
    total from exchange_fee spread over `contracts`, so round_up with a
    larger order shows the smaller per-contract drag of rounding once.
    """
    fee = exchange_fee(ask, contracts, coeff=coeff, round_up=round_up)
    return ev_at_ask(p, ask, fee / contracts)
