"""Paid-launch readiness, run by hand before Doyle's word (P12) and again at
launch: `python -m app.launch_check`.

It reads Stripe (in whatever mode the configured key is) and prints only ids,
counts and pass/fail lines, never a secret. Exit status is non-zero on any
failure, so it can gate a deploy script.
"""

import sys

import stripe

from app.billing_catalog import PLANS, price_id_for
from app.config import get_settings


def checks():
    s = get_settings()
    stripe.api_key = s.stripe_secret_key
    mode = "live" if s.stripe_secret_key.startswith("sk_live_") else "test"
    yield True, f"Stripe key mode: {mode}"

    for plan in PLANS:
        pid = price_id_for(plan, s)
        if not pid:
            yield False, f"{plan}: no price id configured"
            continue
        price = stripe.Price.retrieve(pid)
        ok = bool(price.get("active")) and price.get("livemode") == (mode == "live")
        yield ok, f"{plan}: {pid} active={price.get('active')} livemode={price.get('livemode')} " \
                  f"amount={price.get('unit_amount')} interval={(price.get('recurring') or {}).get('interval')}"

    coupon_id = s.stripe_founder_coupon_id
    if not coupon_id:
        yield False, "STRIPE_FOUNDER_COUPON_ID is not set"
    else:
        c = stripe.Coupon.retrieve(coupon_id)
        yield (c.get("percent_off") == 50 and c.get("duration") == "forever" and bool(c.get("valid"))), \
            f"{coupon_id}: percent_off={c.get('percent_off')} duration={c.get('duration')} valid={c.get('valid')}"
        codes = stripe.PromotionCode.list(coupon=coupon_id, active=True, limit=10)
        n = len(codes.get("data", []))
        yield n == 0, f"{coupon_id}: {n} active customer-facing promotion code(s) (must be 0)"

    yield bool(s.founder_cutoff_utc), f"FOUNDER_CUTOFF_UTC={s.founder_cutoff_utc or '(empty: everyone is a founder)'}"
    yield not s.beta_auto_premium, f"BETA_AUTO_PREMIUM={s.beta_auto_premium}"
    yield s.paid_launch, f"PAID_LAUNCH={s.paid_launch}"


def main() -> int:
    failed = 0
    for ok, line in checks():
        print(("PASS " if ok else "FAIL ") + line)
        failed += not ok
    print(f"{failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
