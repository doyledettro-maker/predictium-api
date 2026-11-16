-- Mobile app: push device registration and the account deletion audit trail.
-- Contract: predictium-app docs/API_PROPOSAL.md sections 1 and 5.
-- Numbered after Predictium_Front_End/database/migrations 001 to 008, the
-- canonical series. Apply 009 and then 010, in that order.
-- Idempotent. MUST run before deploying the API build that serves /devices
-- and DELETE /me: every first sign-in queries account_deletions. Needs the
-- users table to exist already.

BEGIN;

CREATE TABLE IF NOT EXISTS push_devices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expo_push_token TEXT NOT NULL,
    platform        VARCHAR(16) NOT NULL CHECK (platform IN ('ios', 'android')),
    app_version     VARCHAR(64),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT push_devices_expo_push_token_key UNIQUE (expo_push_token)
);

CREATE INDEX IF NOT EXISTS ix_push_devices_user_id ON push_devices (user_id);
CREATE INDEX IF NOT EXISTS ix_push_devices_last_seen_at ON push_devices (last_seen_at);

-- Only hashes (of the Cognito sub, and of the Stripe customer id so a late
-- Stripe webhook can be matched) and a timestamp. Nothing else about the person.
CREATE TABLE IF NOT EXISTS account_deletions (
    id                   BIGSERIAL PRIMARY KEY,
    sub_hash             VARCHAR(64) NOT NULL,
    stripe_customer_hash VARCHAR(64),
    deleted_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT account_deletions_sub_hash_key UNIQUE (sub_hash)
);

-- A database that ran an earlier draft of this file (numbered 001) has the
-- table without this column; CREATE TABLE IF NOT EXISTS would not add it.
ALTER TABLE account_deletions ADD COLUMN IF NOT EXISTS stripe_customer_hash VARCHAR(64);

CREATE INDEX IF NOT EXISTS ix_account_deletions_stripe_customer_hash
    ON account_deletions (stripe_customer_hash);

COMMIT;
