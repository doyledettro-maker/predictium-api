-- Mobile app: push device registration and the account deletion audit trail.
-- Contract: predictium-app docs/API_PROPOSAL.md sections 1 and 5.
-- Idempotent. Run once per database, before deploying the API build that
-- serves /devices and DELETE /me. Needs the users table to exist already.

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

-- Only a hash of the Cognito sub and a timestamp. Nothing else about the person.
CREATE TABLE IF NOT EXISTS account_deletions (
    id         BIGSERIAL PRIMARY KEY,
    sub_hash   VARCHAR(64) NOT NULL,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT account_deletions_sub_hash_key UNIQUE (sub_hash)
);

COMMIT;
