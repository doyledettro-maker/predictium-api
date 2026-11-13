# Billing contract (paid launch, 2026-11-13)

Doyle's rulings (2026-11-13, relayed by Portfolio):
- One **Pro** plan covers every sport except tennis.
- **Tennis** is a separate, higher tier, priced by Doyle.
- A **Pro + Tennis bundle**, monthly or annual.
- **No day pass and no season pass.**
- **Founders get 50% off for good.** A founder is anyone who signed up before paid launch.

Everything here runs in Stripe **TEST** mode until Doyle's word. Merging this repo's `main` builds and pushes the production image (`buildspec.yml`), so the PR waits for that word.

## Catalog (`app/billing_catalog.py`)

| plan | products | interval | price setting |
|---|---|---|---|
| `pro_monthly` | pro | month | `STRIPE_PRO_MONTHLY_PRICE_ID` |
| `pro_annual` | pro | year | `STRIPE_PRO_ANNUAL_PRICE_ID` |
| `tennis_monthly` | tennis | month | `STRIPE_TENNIS_MONTHLY_PRICE_ID` |
| `tennis_annual` | tennis | year | `STRIPE_TENNIS_ANNUAL_PRICE_ID` |
| `bundle_monthly` | pro, tennis | month | `STRIPE_BUNDLE_MONTHLY_PRICE_ID` |
| `bundle_annual` | pro, tennis | year | `STRIPE_BUNDLE_ANNUAL_PRICE_ID` |

- **One row per user.** A user holds one subscription row, so the bundle is its own plan, and moving between Pro, Tennis and Bundle goes through the Customer Portal.
- **Gates read products, not plan names.** The frontend gates on **products** (`pro`, `tennis`).
- **Legacy plan names** still read correctly:
  - `premium` (the beta auto-grant) unlocks no paid product;
  - `pro_weekly` and `pro_season` unlock Pro;
  - `data_api` unlocks no picks.

## Endpoints

All of these require the Cognito bearer token.

- **`POST /billing/create-checkout-session`**
  - **Request:** `{ "plan": "<catalog plan>" }`. Nothing else from the client is used: no price, no URL, no discount.
  - **Response:** `{ url, checkout_url }`, two keys carrying the same value.
  - **Refusals:**
    - 400: unknown plan.
    - 409: the user already holds an active paid subscription; change plans in the portal instead.
    - 503: the plan's price isn't configured.
    - 422: a request without `plan` (the old `{priceId, mode}` shape).
  - **What the session is built with:**
    - success URL `{FRONTEND_URL}/billing?success=true&session_id={CHECKOUT_SESSION_ID}`, cancel URL `{FRONTEND_URL}/billing?canceled=true`;
    - `client_reference_id` set to the user id, with plan metadata on the session and the subscription;
    - for founders, `discounts: [{coupon: STRIPE_FOUNDER_COUPON_ID}]`;
    - for everyone else, `allow_promotion_codes`. The two never stack.
- **`POST /billing/create-portal-session`**
  - **Request:** no body; any body is ignored. It returns to `{FRONTEND_URL}/billing`.
  - **Response:** `{ url, portal_url }`.
  - **Refusals:** 400 when the user has no Stripe customer yet.
- **`GET /billing/subscription`** returns the earlier fields plus `entitlements` (a products array), `founder` and `interval`.
- **`GET /billing/entitlements`** returns `{ products, paid, plan, status, founder, current_period_end }`. The frontend's claims routes call this with the user's own token before any signed read of paid data.

## Access windows (`Subscription.is_active_at`)

- **`trialing`** is active until `trial_ends_at`. Coupon trials no longer run forever.
- **`active`** with a `current_period_end` is active until that time plus `SUBSCRIPTION_GRACE_HOURS` (default 72), so a late renewal webhook doesn't lock out a payer.
- **`active`** with no period end (the beta auto-grant) keeps its status.
- **`past_due`, `canceled` and `expired`** grant nothing.

This also changes the legacy `has_premium_access`. A coupon trial whose `trial_ends_at` has passed now reads as inactive, which is the bug fix. That path only affects the NBA game-detail serializer.

## Webhooks (`POST /webhooks/stripe`)

**Events handled:**
- `checkout.session.completed` (subscription mode)
- `customer.subscription.created`, `.updated` and `.deleted`
- `invoice.paid` and `invoice.payment_failed`

**How each event is applied:**
- Every handler re-reads the subscription from Stripe and writes its current state, so replays and out-of-order events converge. That is the idempotency; no event table is needed.
- The current period end is read from the subscription, or from its first item on API 2025-03-31 and later.

**When an event can't be applied,** the route answers **500** so Stripe retries for up to three days. That covers an unknown price, no matching row, or a Stripe error. It never answers 200 and drops the event, and an unknown price is never a silent downgrade to free.

## Founders

- `FOUNDER_CUTOFF_UTC` is empty before launch, so every user is a founder.
- At launch, set it to the launch instant. Users created before then keep founder status; no migration and no new column are needed.
- Whether a founder who cancels and returns keeps 50% is Doyle's call. Today they do, because the flag comes from the sign-up date.

## Sign-up

`BETA_AUTO_PREMIUM` defaults to `true`, which is today's behaviour: new users get `premium`, and that unlocks no paid product. Set it to `false` at launch so new users start on `free`.

## Configuration to set (TEST mode first) — Claudia, P1

**Stripe objects, created in TEST mode:**
- **Products:** Pro and Tennis, plus a Bundle product.
- **Prices:**
  - Pro: $14.99/month and $119.99/year.
  - Tennis: Doyle's price.
  - Bundle: Doyle's price.
- **Coupon `FOUNDER50`:** percent_off 50, duration `forever`.
- **Webhook endpoint:** `https://<api>/webhooks/stripe`, subscribed to the six events listed under Webhooks.
- **Customer Portal:**
  - cancellation on, card updates on, invoices on;
  - plan switching between the six prices (Pro, Tennis and Bundle, monthly and annual).

**API environment variables:**
- the `STRIPE_*_PRICE_ID` values above;
- `STRIPE_FOUNDER_COUPON_ID=FOUNDER50`;
- `FRONTEND_URL`;
- `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`, in the same mode as the prices.

**Database check (read-only), before relying on new plan names:**
- The canonical DDL in `Predictium_Front_End/database/migrations/002_subscriptions.sql` declares `CHECK (plan IN ('free','pro','elite'))`. Production already stores `premium`, so the live constraint must differ.
- Run `\d subscriptions` in production. If a CHECK still restricts `plan`, it must allow the six catalog names before launch:

```sql
ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_plan_check;
ALTER TABLE subscriptions ADD CONSTRAINT subscriptions_plan_check CHECK (plan IN (
  'free','premium','pro_weekly','pro_monthly','pro_season','data_api',
  'pro_annual','tennis_monthly','tennis_annual','bundle_monthly','bundle_annual'));
```

## Not in this change

These are tracked in the FE session's build plan:
- the frontend's `/billing` page and claims routes;
- the free pick and the redaction in each model repo;
- the copy and the terms;
- the Data API product. Its self-serve tier is no longer sold through checkout.
