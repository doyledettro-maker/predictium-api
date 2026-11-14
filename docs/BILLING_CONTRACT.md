# Billing contract (paid launch, 2026-11-13)

> **ON HOLD (Doyle, 2026-11-14, relayed by Portfolio).** The paid plan is paused while Doyle works through it, and he may add a higher tier. Nothing here merges or deploys: no paywall, locked rendering, paid route or pricing page, and no pick is redacted anywhere. The code stays on this branch in Stripe TEST mode. The decisions below, including that founders keep 50% after cancelling, stand as recorded decisions until he picks it back up.

Doyle's rulings (2026-11-13, relayed by Portfolio):
- One **Pro** plan covers every sport except tennis.
- **Tennis** is a separate, higher tier, priced by Doyle.
- A **Pro + Tennis bundle**, monthly or annual.
- **No day pass and no season pass.**
- **Founders get 50% off for good.** A founder is anyone who signed up before paid launch.

Everything here runs in Stripe **TEST** mode until Doyle's word. Merging this repo's `main` builds and pushes the production image (`buildspec.yml`), so the PR waits for that word.

## Catalog (`app/billing_catalog.py`)

Prices are Doyle's, ruled 2026-11-13. Founders pay half through FOUNDER50.

| plan | products | interval | price | founder | price setting |
|---|---|---|---|---|---|
| `pro_monthly` | pro | month | $14.99 | $7.49 | `STRIPE_PRO_MONTHLY_PRICE_ID` |
| `pro_annual` | pro | year | $119.99 | $59.99 | `STRIPE_PRO_ANNUAL_PRICE_ID` |
| `tennis_monthly` | tennis | month | $39.99 | $19.99 | `STRIPE_TENNIS_MONTHLY_PRICE_ID` |
| `tennis_annual` | tennis | year | $349.99 | $174.99 | `STRIPE_TENNIS_ANNUAL_PRICE_ID` |
| `bundle_monthly` | pro, tennis | month | $44.99 | $22.49 | `STRIPE_BUNDLE_MONTHLY_PRICE_ID` |
| `bundle_annual` | pro, tennis | year | $399.99 | $199.99 | `STRIPE_BUNDLE_ANNUAL_PRICE_ID` |

**Founder cents need checking in TEST.** Every price ends in 99 cents, so 50% falls on a half cent. The founder column assumes the founder pays the lower cent (for example $7.49 on $14.99). Confirm what Stripe actually charges with FOUNDER50 in the P12 run. If it charges the other cent ($7.50), there are two options, and Doyle chooses:
- accept the cent;
- use six founder prices instead of the coupon. That means a `founder` price per plan, chosen server-side, which costs one more setting per plan.

`Plan.amount_cents` holds these amounts, and `app.launch_check` fails any configured price that charges a different amount, currency or interval.

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
    - 409: a **live Stripe subscription** exists (active, trialing or past_due), per our row or per Stripe's own list for the customer. Change plans in the portal instead.
    - 503: the plan's price isn't configured.
    - 422: a request without `plan` (the old `{priceId, mode}` shape).
  - **What the session is built with:**
    - success URL `{FRONTEND_URL}/billing?success=true&session_id={CHECKOUT_SESSION_ID}`, cancel URL `{FRONTEND_URL}/billing?canceled=true`;
    - `client_reference_id` set to the user id, with plan metadata on the session and the subscription;
    - for founders, `discounts: [{coupon: STRIPE_FOUNDER_COUPON_ID}]`;
    - for everyone else, `allow_promotion_codes`. The two never stack.
    - any older open Checkout Session for the same customer is expired first, so two tabs cannot both pay.
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
- **`past_due`** grants nothing by default, so a failing card loses access at once. This is a deliberate product setting, `PAST_DUE_GRANTS_ACCESS` (default false); turning it on keeps access while Stripe retries the card, up to the period end plus grace.
- **`canceled` and `expired`** grant nothing.

This also changes the legacy `has_premium_access`. A coupon trial whose `trial_ends_at` has passed now reads as inactive, which is the bug fix. That path only affects the NBA game-detail serializer.

## Webhooks (`POST /webhooks/stripe`)

**Events handled:**
- `checkout.session.completed` (subscription mode)
- `customer.subscription.created`, `.updated` and `.deleted`
- `invoice.paid` and `invoice.payment_failed`

**How each event is applied:**
- Every handler re-reads the subscription from Stripe and writes its current state, so replays and out-of-order events converge. That is the idempotency; no event table is needed.
- The current period end is read from the subscription, or from its first item on API 2025-03-31 and later.

**One row, many subscriptions.** An event for a subscription that isn't the row's own may take over the row only in two cases: when that subscription is live itself, or when the row's own subscription isn't live. So a late `deleted` for a canceled sub_1 never locks out the sub_2 payer. Two live subscriptions for one user log an `ALERT`.

**An event that matches no row** is acknowledged with a 200 and an `ALERT` log line. Retrying can never fix it.

**When an event can't be applied,** the route answers **500** so Stripe retries for up to three days. That covers an unknown price, no matching row, or a Stripe error. It never answers 200 and drops the event, and an unknown price is never a silent downgrade to free.

## Coupons

`/billing/redeem-coupon` answers 409 while a live Stripe subscription exists, so a coupon can never overwrite a paying row. A coupon's plan is usually `premium`, which unlocks no paid product after launch. Doyle should confirm that is intended, or say which product a coupon should grant.

## Founders

- `FOUNDER_CUTOFF_UTC` is empty before launch, so every user is a founder.
- At launch, set it to the launch instant. Users created before then keep founder status; no migration and no new column are needed.
- **A founder keeps 50% after cancelling** (Doyle, 2026-11-14: "a founder should keep the 50% even if they cancel"). Founder status belongs to the user, not to the subscription. It is computed from the sign-up date on every checkout, so a founder who cancels and comes back resubscribes with FOUNDER50 on the same Stripe customer. `test_founder_keeps_50_percent_after_cancel_and_resubscribe` runs checkout, activation, cancellation and a second checkout through the real routes and webhook handler. Its twin test shows a non-founder still pays full price.
- **Never move `FOUNDER_CUTOFF_UTC` once it is set.** It is the founder record. Moving it earlier would quietly take 50% away from founders, including founders who are between subscriptions.
- **Agreed follow-up, not built (Portfolio, 2026-11-14):** when billing resumes, store a permanent founder flag on the user at their first checkout. After that, a later edit to the cutoff can never take founder status away from anyone. It is one column plus a migration. The flag is set once and never cleared, and `is_founder` reads it first.

`FOUNDER_CUTOFF_UTC` is validated when settings load: a malformed value stops startup, rather than failing every billing read.

## Launch gate

- **`PAID_LAUNCH=true`** makes startup refuse to run unless `FOUNDER_CUTOFF_UTC` and `STRIPE_FOUNDER_COUPON_ID` are set and `BETA_AUTO_PREMIUM=false`. Each of those defaults fails open: everyone counts as a founder, founders get promo codes instead of 50%, and new users get the beta grant.
- **`python -m app.launch_check`** prints PASS/FAIL lines, ids only, and exits non-zero on any failure. It checks:
  - every catalog price exists, is active and is in the key's mode;
  - FOUNDER50 is 50% off with duration forever, and has **no active customer-facing promotion code**;
  - the three settings above.

  Run it at P12 and again at launch.
- **Before deploying, run these read-only production counts.** Deploying changes two things:
  - coupon trials past `trial_ends_at` lose premium;
  - `active` rows more than 72 hours past `current_period_end` go inactive.

  Count both first:
  ```sql
  SELECT count(*) FROM subscriptions WHERE status='trialing' AND trial_ends_at < now();
  SELECT count(*) FROM subscriptions WHERE status='active' AND current_period_end < now() - interval '72 hours';
  SELECT stripe_customer_id, count(*) FROM subscriptions WHERE stripe_customer_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1;
  ```

## Sign-up

`BETA_AUTO_PREMIUM` defaults to `true`, which is today's behaviour: new users get `premium`, and that unlocks no paid product. Set it to `false` at launch so new users start on `free`.

## The Stripe account

The Stripe account belongs to Predictium LLC (Doyle, 2026-11-13). Firmcraft is moving to its own LLC and accounts and is not part of Predictium billing.

- **Never modify, disable or reuse the existing live Firmcraft webhook endpoint or portal configuration.** Doyle is migrating them himself.
- **At launch, Predictium adds its own** live webhook endpoint, pointing at this API, and its own portal configuration. Both use their own signing secret.

## Configuration to set (TEST mode first) — Claudia, P1

**Stripe objects, created in TEST mode:**
- **Products:** Pro and Tennis, plus a Bundle product.
- **Prices:**
  - Pro: $14.99/month and $119.99/year.
  - Tennis: $39.99/month and $349.99/year.
  - Bundle: $44.99/month and $399.99/year.
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

**Hard launch gate: the database check (read-only). Under the canonical DDL a `pro_monthly` write fails, the webhook retries, and the payer stays locked out until the ALTER runs:**
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
