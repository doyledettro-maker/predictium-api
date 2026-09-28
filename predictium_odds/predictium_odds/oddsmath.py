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
# for every sport: tennis, WNBA, CFB, NFL, soccer and golf all price Kalshi
# strikes through this, so a fee or settlement change is made once.
#
# Settlement is the org's: to win 1 unit at price q, risk q / (1 - q). At an
# exchange the price actually paid for a YES contract that pays $1 is the ask
# PLUS the fee, so q here is the fee-inclusive cost, never the bare ask. The
# de-vigged-price rule does not apply: an exchange ask carries no vig to
# remove, only a fee to add.
#
# THE FEE COMES FROM THE SERIES, NEVER FROM A CONSTANT (Portfolio/Risk,
# 2026-09-28, from golf spec 0038 §6). Kalshi's /series/{ticker} returns
# `fee_type` and `fee_multiplier` per series; the caller reads them at capture
# time (books.kalshi.fetch_series_fees) and passes them in. What is known, and
# from where (docs/KALSHI_FEE_EVIDENCE.md has the captures):
#
# - fee_type enum, per Kalshi's API reference (docs.kalshi.com, get-series,
#   read 2026-09-28): "quadratic" (General Trading Fees Table),
#   "quadratic_with_maker_fees" (same table, plus maker fees),
#   "quadratic_with_combo_maker_fees" (same, 0.5 maker multiplier instead of
#   0.25), "flat" (Specific Trading Fees Table). All three quadratic types
#   share the taker formula; they differ only in what MAKERS pay. We take.
# - fee_multiplier: "a floating point multiplier applied to the fee
#   calculations". Live values 2026-09-28 across all 14,399 series: 1, 0.5
#   (every MLB game-day series) and 0 (14 non-sports series).
# - The 0.07 base is NOT stated in any document we have read: the fee
#   schedule PDF that carries the General Trading Fees Table answers HTTP
#   429. It is corroborated, not quoted: Kalshi's fee-rounding page works an
#   example of a 5.5c buy with "a model fee of $0.00363825", which is
#   0.07 x 0.055 x 0.945 to the last digit.
# - Rounding, per the same page: trade fee = ceil_6dp(model fee); a
#   non-direct member's balance change is then aligned to the cent, and an
#   order-level accumulator refunds the overpayment so the order converges on
#   what one equivalent fill would cost.
#
# "flat", any fee type not listed above, and a missing or invalid multiplier
# return None: no fee, and therefore no EV claim. Never the quadratic formula
# by default.
# ---------------------------------------------------------------------------

KALSHI_GENERAL_FEE_COEFF = 0.07
KALSHI_QUADRATIC_FEE_TYPES = frozenset({
    "quadratic", "quadratic_with_maker_fees",
    "quadratic_with_combo_maker_fees"})


def _ceil_dp(x: float, dp: int) -> float:
    """Round UP to `dp` decimals, immune to float noise below 1e-9 of a unit
    (0.0175 * 100 is 1.7500000000000002, which is not a reason to add 1c)."""
    scale = 10 ** dp
    return math.ceil(round(x * scale, 9 - min(dp, 6))) / scale


def kalshi_taker_fee(ask: float, fee_type: str | None,
                     fee_multiplier: float | None, contracts: int = 1,
                     ) -> float | None:
    """Kalshi taker fee in dollars for an order of `contracts` at `ask`, from
    the series' own fee_type and fee_multiplier, or None if we cannot know it.

    fee_multiplier x 0.07 x contracts x ask x (1 - ask), ceil to $0.000001
    (Kalshi's trade-fee precision). Returns None for "flat", for any unknown
    fee_type, and for a missing, non-finite or negative multiplier.
    """
    if not 0.0 < ask < 1.0:
        raise ValueError(f"ask must be in (0, 1), got {ask}")
    if contracts < 1:
        raise ValueError(f"contracts must be >= 1, got {contracts}")
    if fee_type not in KALSHI_QUADRATIC_FEE_TYPES:
        return None
    try:
        mult = float(fee_multiplier)          # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(mult) or mult < 0:
        return None
    raw = mult * KALSHI_GENERAL_FEE_COEFF * contracts * ask * (1.0 - ask)
    return _ceil_dp(raw, 6)


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
    """EV at an executable ask plus a KNOWN per-contract fee, venue-agnostic.

    For Kalshi use kalshi_ev_at_ask, which derives the fee from the series
    and refuses to price when it cannot."""
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


def kalshi_ev_at_ask(p: float, ask: float, *, fee_type: str | None,
                     fee_multiplier: float | None, contracts: int = 1,
                     cent_aligned: bool = False) -> FeeInclusiveEV | None:
    """Fee-inclusive EV of buying Kalshi YES at `ask` with model prob `p`,
    or None when the series' fee cannot be determined (no EV claim).

    fee_type / fee_multiplier: the series' own values from /series/{ticker},
    read at capture time. There is no default; pass what the venue said.

    cent_aligned=True models a non-direct member (our account, via an FCM):
    the order's balance change is aligned up to the cent, so the effective
    cost per contract is ceil_cent(contracts x ask + fee) / contracts. With
    contracts=1 that is the conservative single-fill cost; a larger order
    pays the rounding once. False gives the exact model fee.

    To buy NO pass p = 1 - p_yes and the NO ask.
    """
    fee = kalshi_taker_fee(ask, fee_type, fee_multiplier, contracts)
    if fee is None:
        return None
    if cent_aligned:
        total = _ceil_dp(contracts * ask + fee, 2)
        per_contract_fee = total / contracts - ask
    else:
        per_contract_fee = fee / contracts
    return ev_at_ask(p, ask, max(per_contract_fee, 0.0))
