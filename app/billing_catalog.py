"""The paid catalog: plans, the products they unlock, and their Stripe prices.

Doyle, 2026-11-13: one Pro plan for every sport except tennis, a standalone
Tennis tier, and a Pro + Tennis bundle, each monthly or annual. There is no
day pass and no season pass. A user holds one subscription row, so a plan
names the whole set of products it unlocks; the bundle is its own plan.

Entitlements are products, never plan names: the frontend's claims routes ask
"does this user hold `pro`" or "`tennis`", so a new plan never needs a
frontend change. Legacy plan names written before this catalog are mapped
here, so a row written by the old code still reads correctly.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, FrozenSet, Optional

from app.config import Settings

PRODUCT_PRO = "pro"
PRODUCT_TENNIS = "tennis"
PRODUCTS = (PRODUCT_PRO, PRODUCT_TENNIS)


@dataclass(frozen=True)
class Plan:
    """A purchasable plan: the products it unlocks and its billing interval."""

    id: str
    products: FrozenSet[str]
    interval: str  # "month" | "year"
    price_setting: str  # the Settings attribute holding its Stripe price id


PLANS: Dict[str, Plan] = {
    p.id: p
    for p in (
        Plan("pro_monthly", frozenset({PRODUCT_PRO}), "month", "stripe_pro_monthly_price_id"),
        Plan("pro_annual", frozenset({PRODUCT_PRO}), "year", "stripe_pro_annual_price_id"),
        Plan("tennis_monthly", frozenset({PRODUCT_TENNIS}), "month", "stripe_tennis_monthly_price_id"),
        Plan("tennis_annual", frozenset({PRODUCT_TENNIS}), "year", "stripe_tennis_annual_price_id"),
        Plan("bundle_monthly", frozenset({PRODUCT_PRO, PRODUCT_TENNIS}), "month", "stripe_bundle_monthly_price_id"),
        Plan("bundle_annual", frozenset({PRODUCT_PRO, PRODUCT_TENNIS}), "year", "stripe_bundle_annual_price_id"),
    )
}

# Plan names the old code wrote. `premium` is the beta auto-grant every user
# received at sign-up: it is not a purchase and unlocks no paid product.
# `pro_weekly` and `pro_season` were never purchasable in production (the old
# checkout contract always failed), but a row carrying them unlocks Pro rather
# than nothing. `data_api` is a separate product and unlocks no picks.
LEGACY_PLAN_PRODUCTS: Dict[str, FrozenSet[str]] = {
    "free": frozenset(),
    "premium": frozenset(),
    "pro_weekly": frozenset({PRODUCT_PRO}),
    "pro_season": frozenset({PRODUCT_PRO}),
    "data_api": frozenset(),
}


def plan_products(plan: Optional[str]) -> FrozenSet[str]:
    """The products a stored plan name unlocks; an unknown name unlocks none."""
    if not plan:
        return frozenset()
    if plan in PLANS:
        return PLANS[plan].products
    return LEGACY_PLAN_PRODUCTS.get(plan, frozenset())


def price_id_for(plan_id: str, settings: Settings) -> str:
    """The configured Stripe price id for a catalog plan, or "" when unset."""
    return getattr(settings, PLANS[plan_id].price_setting, "") or ""


def plan_for_price(price_id: Optional[str], settings: Settings) -> Optional[str]:
    """The catalog plan whose configured price is `price_id`, or None.

    Unconfigured (empty) prices never match, so an empty price id can never
    resolve to a plan.
    """
    if not price_id:
        return None
    for plan in PLANS.values():
        if price_id_for(plan.id, settings) == price_id:
            return plan.id
    legacy = {
        settings.stripe_weekly_price_id: "pro_weekly",
        settings.stripe_monthly_price_id: "pro_monthly",
        settings.stripe_premium_price_id: "pro_monthly",
        settings.stripe_season_price_id: "pro_season",
        settings.stripe_data_api_price_id: "data_api",
    }
    legacy.pop("", None)
    return legacy.get(price_id)


def founder_cutoff(settings: Settings) -> Optional[datetime]:
    """The founder cutoff instant, or None before paid launch."""
    raw = (settings.founder_cutoff_utc or "").strip()
    if not raw:
        return None
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_founder(user_created_at: Optional[datetime], settings: Settings) -> bool:
    """A founder signed up before paid launch (Doyle: 50% off, for good).

    Before a cutoff is configured every user is a founder, which is exactly
    "everyone who signs up before paid launch".
    """
    cutoff = founder_cutoff(settings)
    if cutoff is None:
        return True
    if user_created_at is None:
        return False
    created = user_created_at if user_created_at.tzinfo else user_created_at.replace(tzinfo=timezone.utc)
    return created < cutoff
