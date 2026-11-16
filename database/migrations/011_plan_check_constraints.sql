-- Let the plan CHECK constraints accept every plan the API writes.
--
-- Predictium_Front_End migrations 002 (subscriptions) and 003 (coupons)
-- created CHECK (plan IN ('free','pro','elite')). The API writes 'premium'
-- at first login and 'pro_weekly', 'pro_monthly', 'pro_season' and
-- 'data_api' from checkout and the Stripe webhooks, so those inserts fail.
-- This allows the union and keeps the legacy values, so existing rows stay
-- valid. It changes no pricing or entitlement. The list must match
-- app/models/plans.py ALL_PLANS (a test checks this).
--
-- Idempotent. Safe to run while the API is serving:
--   Part 1 swaps the constraint inside one short transaction, so there is
--   never a moment with no check. The new one is added NOT VALID, which
--   skips the table scan and holds the ACCESS EXCLUSIVE lock only briefly.
--   lock_timeout makes it give up, rather than queue behind a long
--   transaction and block traffic; just run it again if it times out.
--   Part 2 validates existing rows under a lighter lock
--   (SHARE UPDATE EXCLUSIVE, reads and writes continue).
-- Run the whole file with: psql -v ON_ERROR_STOP=1 -f 011_plan_check_constraints.sql

-- Part 1
BEGIN;
SET LOCAL lock_timeout = '5s';

DO $$
DECLARE
    t   text;
    c   record;
BEGIN
    FOREACH t IN ARRAY ARRAY['subscriptions', 'coupons'] LOOP
        IF to_regclass('public.' || t) IS NULL THEN
            CONTINUE;
        END IF;
        -- Drop every CHECK on the plan column alone, whatever it is named,
        -- so a differently named live constraint cannot survive.
        FOR c IN
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_attribute att
              ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
            WHERE con.conrelid = ('public.' || t)::regclass
              AND con.contype = 'c'
              AND att.attname = 'plan'
              AND array_length(con.conkey, 1) = 1
        LOOP
            EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I', t, c.conname);
        END LOOP;
        EXECUTE format(
            'ALTER TABLE public.%I ADD CONSTRAINT %I CHECK (plan IN ('
            '''free'', ''premium'', ''pro_weekly'', ''pro_monthly'', ''pro_season'', '
            '''data_api'', ''pro'', ''elite'')) NOT VALID',
            t, t || '_plan_check');
    END LOOP;
END $$;

COMMIT;

-- Part 2
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['subscriptions', 'coupons'] LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I VALIDATE CONSTRAINT %I', t, t || '_plan_check');
        END IF;
    END LOOP;
END $$;

-- Check afterwards (both rows should show convalidated = true):
--   SELECT conrelid::regclass, conname, convalidated, pg_get_constraintdef(oid)
--   FROM pg_constraint WHERE conname IN ('subscriptions_plan_check', 'coupons_plan_check');
