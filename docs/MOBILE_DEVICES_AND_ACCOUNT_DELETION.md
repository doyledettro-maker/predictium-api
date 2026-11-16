# Mobile app: push devices and account deletion

Built from predictium-app `docs/API_PROPOSAL.md` (main cd8413c), sections 1
and 5, plus the rate limit note in section 6. Sections 2 (alert preferences),
3 (sending) and 4 (entitlements) are not built here.

## Routes

| Route | Auth | Answers |
|---|---|---|
| `POST /devices` body `{ expo_push_token, platform, app_version }` | Cognito, `get_current_user_with_db` | `201 { device_id }`, 422 bad body (never echoes input), 401, 429 |
| `DELETE /devices/{device_id}` | same | 204 always for the caller (missing, or someone else's row: untouched), 401, 429 |
| `DELETE /me` | Cognito, verified on the request | `202 { status: "deleted" }`, repeat calls 202, 401, 429, 503 `{ detail, step }` with `Retry-After` |

Rate limits, per Cognito sub, in process memory (each API task counts on
its own): `/devices` 30 calls per 10 minutes (POST and DELETE share it),
`DELETE /me` 5 per 10 minutes.

The push token travels only in the `POST /devices` body. It is not in any
URL, and it is kept out of logs and errors: the engine sets
`hide_parameters=True`, so SQL echo and DB error text show no bound values,
and on `/devices` a 422 response drops the `input` field.

A daily task in the API process deletes devices not seen for 30 days.

## DELETE /me order

Each step can be repeated safely. If a step fails, the run stops there and
returns 503 with that step's name, and nothing a retry needs has been lost.

1. `billing`: every billable Stripe subscription on the customer is set to
   `cancel_at_period_end`. This step comes first because the Stripe ids live
   on the subscription row.
2. `store_entitlements`: RevenueCat `DELETE /v1/subscribers/{sub}`. A 404
   counts as done. If `REVENUECAT_SECRET_KEY` is unset, the step is skipped
   with a warning.
3. `data`: one transaction deletes the push devices, the page_views rows
   keyed by the sub, coupon redemptions, the subscription row and the user
   row. Coupons the user created are kept with `created_by` set to NULL. It
   also writes `account_deletions` (a SHA-256 of the sub and a timestamp).
4. `identity`: Cognito `AdminDeleteUser` with `cognito:username`.
   `UserNotFoundException` counts as done.

Once step 3 has run, a token that is still valid cannot re-create the user
row through any other route. Those routes answer 401 "This account was
deleted".

## Deploy steps (in order)

None of these have been run. Each needs a person with production access.

1. **Check foreign keys to `users`.** The canonical schema lives in
   Predictium_Front_End `database/migrations/`, which this change cannot
   see. Run
   `SELECT conrelid::regclass, conname, confdeltype FROM pg_constraint WHERE confrelid = 'users'::regclass;`.
   If any table other than subscriptions, coupons, coupon_redemptions or
   push_devices references users without `ON DELETE CASCADE`, the data step
   will fail safe with a 503. That table then needs adding to
   `delete_rows` before launch.
2. **Migration 001**: `psql -f database/migrations/001_push_devices_and_account_deletions.sql`.
   It is idempotent and runs in a transaction. Copy it into
   Predictium_Front_End's migrations too if that stays the canonical list.
3. **Migration 002**: `psql -f database/migrations/002_page_views_user_id_index.sql`.
   Run it on its own, outside a transaction (it uses CREATE INDEX
   CONCURRENTLY). It is safe while serving.
4. **IAM**: give the API task role `cognito-idp:AdminDeleteUser` on this
   user pool's ARN only.
5. **Env vars** (names only; values come from the secret store):
   - `COGNITO_CLIENT_IDS`: the website client ID AND `2du0mq32r8o5lua5m089hrsdeo`
     (predictium-mobile), comma-separated. Setting it replaces the
     `COGNITO_CLIENT_ID` default, so leaving the website client out would
     lock out every website user.
   - `REVENUECAT_SECRET_KEY`: needed before external TestFlight, because the
     app already logs in to RevenueCat with the Cognito sub. Without it,
     DELETE /me leaves the RevenueCat subscriber in place.
   - `STRIPE_SECRET_KEY`, `COGNITO_USER_POOL_ID`, `COGNITO_REGION`: these
     already exist and are unchanged.
6. **Deploy** the API build.
7. **Smoke test** with a throwaway test account in a non-production pool
   first: `POST /devices`, `DELETE /devices/{id}`, then `DELETE /me`.

## Where this departs from API_PROPOSAL.md

- **Step order in DELETE /me.** The proposal lists rows, Cognito, Stripe,
  RevenueCat. Here the order is Stripe, RevenueCat, rows, Cognito. Deleting
  the rows first would lose the Stripe ids, so a failed Stripe call could
  never be retried and the subscription would keep billing. Deleting Cognito
  before the other steps finish would stop the person from signing in to
  retry once their token expired.
- **Upsert key.** The brief said to upsert on (sub, token). The proposal
  makes the token unique on its own and moves it to the caller on a shared
  phone, and that is what is built. The effect for one user is the same.
  The difference is that one phone can never get two accounts' alerts.
- **Preferences.** The proposal's step 1 also deletes notification
  preferences. That table does not exist yet (section 2 is not built), so
  there is nothing to delete. Add it to `delete_rows` when section 2 lands.
- **Analytics rows.** page_views rows keyed by the sub are deleted, as the
  proposal says. They are not anonymised.
- **Migrations.** The SQL lives in this repo under `database/migrations/`,
  because Predictium_Front_End (the usual home) was out of scope.
- **Missing Authorization header.** This is now 401 on every route. It used
  to be FastAPI's 422.
