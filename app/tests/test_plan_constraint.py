"""The plan CHECK constraints accept every plan the code can write.

The 2026-11-16 smoke test failed on the first POST /devices: the website's
schema allowed only 'free', 'pro' and 'elite', while first login writes
'premium'. Here the written plans are derived from the code itself:
- checkout's PLAN_PRICE_MAP keys;
- every value _price_to_plan returns;
- the first-login default;
- the Stripe cancel path;
- the coupon default.

They are then checked against app/models/plans.py and against migration 011
on a real Postgres. Adding a plan to the code without the schema fails here.
"""

import asyncio
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import plans
from app.models.coupon import Coupon
from app.models.subscription import Subscription

PG_URL = os.environ.get("TEST_PG_URL")
MIGRATION = Path(__file__).resolve().parents[2] / "database" / "migrations" / "011_plan_check_constraints.sql"


# --- what the code writes ----------------------------------------------------


class _FakeResult:
    def scalar_one_or_none(self):
        return None

    def first(self):
        return None


class _CaptureSession:
    """Just enough of an AsyncSession to run the first-login path."""

    def __init__(self):
        self.added = []

    async def execute(self, *a, **kw):
        return _FakeResult()

    def add(self, obj):
        if getattr(obj, "id", None) is None and hasattr(obj, "cognito_id"):
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass

    async def rollback(self):
        pass


def _first_login_plans():
    from app.dependencies import get_current_user_with_db

    db = _CaptureSession()
    asyncio.run(get_current_user_with_db({"sub": "sub-new", "email": "n@example.com"}, db))
    return {o.plan for o in db.added if isinstance(o, Subscription)}


def _price_to_plan_values(monkeypatch):
    from app.services.stripe_service import stripe_service

    s = stripe_service.settings
    fields = [f for f in type(s).model_fields if f.startswith("stripe_") and f.endswith("_price_id")]
    for i, f in enumerate(fields):
        monkeypatch.setattr(s, f, f"price_test_{i}")
    probes = [f"price_test_{i}" for i in range(len(fields))] + ["price_unknown", ""]
    return {stripe_service._price_to_plan(p) for p in probes}


def _cancel_path_plans():
    from app.services.stripe_service import stripe_service

    row = SimpleNamespace(status="active", plan="pro_monthly")

    class _Res:
        def scalar_one_or_none(self):
            return row

    class _Db:
        async def execute(self, *a, **kw):
            return _Res()

        async def commit(self):
            pass

    event = {"data": {"object": {"id": "sub_x"}}}
    asyncio.run(stripe_service.handle_subscription_deleted(event, _Db()))
    return {row.plan}


def written_plans(monkeypatch):
    from app.routers.billing import PLAN_PRICE_MAP

    return (
        set(PLAN_PRICE_MAP)
        | _price_to_plan_values(monkeypatch)
        | _first_login_plans()
        | _cancel_path_plans()
        | {Coupon.__table__.c.plan.default.arg, Subscription.__table__.c.plan.default.arg}
    )


def migration_plans():
    sql = MIGRATION.read_text()
    m = re.search(r"CHECK \(plan IN \((.*?)\)\) NOT VALID", sql, re.S)
    assert m, "011 must build its CHECK (plan IN (...)) NOT VALID"
    return set(re.findall(r"''([a-z_]+)''", m.group(1)))


def test_every_written_plan_is_allowed(monkeypatch):
    written = written_plans(monkeypatch)
    # sanity: the derivation found the paths it is meant to find
    assert {"premium", "free", "pro_weekly", "pro_monthly", "pro_season", "data_api"} <= written
    missing = written - set(plans.ALL_PLANS)
    assert not missing, f"plans the code writes but the schema refuses: {sorted(missing)}"


def test_migration_011_allows_exactly_all_plans():
    assert migration_plans() == set(plans.ALL_PLANS)


def test_model_constraints_match_all_plans():
    for table, name in ((Subscription.__table__, "subscriptions_plan_check"), (Coupon.__table__, "coupons_plan_check")):
        checks = [c for c in table.constraints if c.name == name]
        assert len(checks) == 1
        assert set(re.findall(r"'([a-z_]+)'", str(checks[0].sqltext))) == set(plans.ALL_PLANS)


def test_legacy_values_are_kept():
    assert {"free", "pro", "elite"} <= set(plans.ALL_PLANS)


# --- the real constraint on Postgres -----------------------------------------


@pytest.mark.skipif(not PG_URL, reason="TEST_PG_URL not set")
def test_migration_011_on_the_website_schema(monkeypatch):
    import asyncpg

    written = sorted(written_plans(monkeypatch))
    dsn = PG_URL.replace("postgresql+asyncpg://", "postgresql://")

    async def go():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DROP TABLE IF EXISTS plan_t_redemptions, subscriptions, coupons CASCADE")
            # As the website's migrations 002 and 003 made them: an inline
            # (auto-named) check on subscriptions, and on coupons a check
            # with another name, to prove 011 finds it by column, not name.
            await conn.execute(
                "CREATE TABLE subscriptions (id serial PRIMARY KEY, "
                "plan text NOT NULL DEFAULT 'free' CHECK (plan IN ('free','pro','elite')), "
                "status text NOT NULL DEFAULT 'active' CHECK (status <> ''))")
            await conn.execute(
                "CREATE TABLE coupons (code text PRIMARY KEY, plan text NOT NULL, "
                "CONSTRAINT legacy_coupon_plan CHECK (plan IN ('free','pro','elite')))")
            # Existing rows in every legacy value
            for p in ("free", "pro", "elite"):
                await conn.execute("INSERT INTO subscriptions (plan) VALUES ($1)", p)
                await conn.execute("INSERT INTO coupons VALUES ($1, $2)", f"C-{p}", p)

            # The production failure, reproduced
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute("INSERT INTO subscriptions (plan) VALUES ('premium')")

            sql = MIGRATION.read_text()
            await conn.execute(sql)
            await conn.execute(sql)  # idempotent

            for p in written:
                await conn.execute("INSERT INTO subscriptions (plan) VALUES ($1)", p)
                await conn.execute("INSERT INTO coupons VALUES ($1, $2)", f"N-{p}", p)
            for bad in ("bogus", "PREMIUM", ""):
                with pytest.raises(asyncpg.CheckViolationError):
                    await conn.execute("INSERT INTO subscriptions (plan) VALUES ($1)", bad)
                with pytest.raises(asyncpg.CheckViolationError):
                    await conn.execute("INSERT INTO coupons VALUES ($1, $2)", f"B-{bad}", bad)

            rows = await conn.fetch(
                "SELECT conrelid::regclass::text AS t, conname, convalidated FROM pg_constraint "
                "WHERE contype = 'c' AND conrelid IN ('subscriptions'::regclass, 'coupons'::regclass) "
                "ORDER BY 1, 2")
            return [tuple(r) for r in rows]
        finally:
            await conn.execute("DROP TABLE IF EXISTS subscriptions, coupons CASCADE")
            await conn.close()

    rows = asyncio.run(go())
    # the old checks are gone, the new ones are validated, other checks untouched
    assert ("coupons", "coupons_plan_check", True) in rows
    assert ("subscriptions", "subscriptions_plan_check", True) in rows
    assert not any(name == "legacy_coupon_plan" for _, name, _ in rows)
    assert ("subscriptions", "subscriptions_status_check", True) in rows


@pytest.mark.skipif(not PG_URL, reason="TEST_PG_URL not set")
def test_migration_011_skips_missing_tables():
    import asyncpg

    dsn = PG_URL.replace("postgresql+asyncpg://", "postgresql://")

    async def go():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DROP TABLE IF EXISTS subscriptions, coupons CASCADE")
            await conn.execute(MIGRATION.read_text())
        finally:
            await conn.close()

    asyncio.run(go())
