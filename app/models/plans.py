"""Every value the `plan` column may hold, on subscriptions and on coupons.

The database CHECK constraints (migration 011) allow exactly ALL_PLANS.
app/tests/test_plan_constraint.py derives the plans the code can write
(checkout, the Stripe webhooks, first login, coupons) and fails if one is
missing here or from the migration, so a new plan cannot ship without
the schema accepting it.

This module only names values the code already writes. It changes no
pricing or entitlement: paid plans stay on hold.
"""

FREE = "free"
# Written at first login while the paid plan is on hold (every feature open).
BETA_DEFAULT = "premium"
# Written by checkout and the Stripe webhooks.
PAID = ("pro_weekly", "pro_monthly", "pro_season", "data_api")
# From the website's original schema (Predictium_Front_End migration 002).
# No code writes them now; existing rows may still hold them.
LEGACY = ("pro", "elite")

ALL_PLANS = (FREE, BETA_DEFAULT, *PAID, *LEGACY)


def check_sql(column: str = "plan") -> str:
    values = ", ".join(f"'{p}'" for p in ALL_PLANS)
    return f"{column} IN ({values})"
