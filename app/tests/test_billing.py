"""Paid launch billing (2026-11-13): the catalog, access windows, checkout,
portal, entitlements and webhooks.

Everything runs against an in-memory SQLite database and stubbed Stripe calls:
nothing here reaches Stripe, Cognito or a real database. The contract pinned
here is the one the frontend's /billing page and claims routes will call.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import billing_catalog as catalog
from app.config import get_settings
from app.db.database import Base, get_db
from app.dependencies import get_current_user_with_db
from app.models.coupon import Coupon, CouponRedemption  # noqa: F401  (tables)
from app.models.subscription import Subscription
from app.models.user import User
from app.routers import billing as billing_router
from app.routers import webhooks as webhooks_router
import importlib
svc_module = importlib.import_module("app.services.stripe_service")
from app.services.stripe_service import STATUS_MAP, stripe_service, subscription_period_end

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
PRICES = {
    "stripe_pro_monthly_price_id": "price_pro_m",
    "stripe_pro_annual_price_id": "price_pro_y",
    "stripe_tennis_monthly_price_id": "price_ten_m",
    "stripe_tennis_annual_price_id": "price_ten_y",
    "stripe_bundle_monthly_price_id": "price_bun_m",
    "stripe_bundle_annual_price_id": "price_bun_y",
}


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()
    for k, v in PRICES.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(s, "stripe_founder_coupon_id", "FOUNDER50")
    monkeypatch.setattr(s, "founder_cutoff_utc", "")
    monkeypatch.setattr(s, "frontend_url", "https://www.predictium.ai")
    for k in ("stripe_weekly_price_id", "stripe_monthly_price_id", "stripe_season_price_id",
              "stripe_premium_price_id", "stripe_data_api_price_id"):
        monkeypatch.setattr(s, k, "")
    return s


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def test_catalog_products(settings):
    assert catalog.plan_products("pro_monthly") == {"pro"}
    assert catalog.plan_products("tennis_annual") == {"tennis"}
    assert catalog.plan_products("bundle_monthly") == {"pro", "tennis"}
    # The beta auto-grant is not a purchase and unlocks nothing paid.
    assert catalog.plan_products("premium") == frozenset()
    assert catalog.plan_products("free") == frozenset()
    assert catalog.plan_products("data_api") == frozenset()
    assert catalog.plan_products("nonsense") == frozenset()
    assert catalog.plan_products(None) == frozenset()


def test_no_day_or_season_pass_is_sold(settings):
    assert set(catalog.PLANS) == {
        "pro_monthly", "pro_annual", "tennis_monthly", "tennis_annual", "bundle_monthly", "bundle_annual",
    }
    assert {p.interval for p in catalog.PLANS.values()} == {"month", "year"}


def test_price_mapping_round_trips_and_empty_never_matches(settings):
    for plan in catalog.PLANS:
        assert catalog.plan_for_price(catalog.price_id_for(plan, settings), settings) == plan
    assert catalog.plan_for_price("", settings) is None
    assert catalog.plan_for_price(None, settings) is None
    assert catalog.plan_for_price("price_unknown", settings) is None


def test_founder_rule(settings, monkeypatch):
    # Before launch (no cutoff), everyone is a founder.
    assert catalog.is_founder(NOW, settings) is True
    monkeypatch.setattr(settings, "founder_cutoff_utc", "2026-12-07T00:00:00Z")
    assert catalog.is_founder(datetime(2026, 12, 6, 23, 59, tzinfo=timezone.utc), settings) is True
    assert catalog.is_founder(datetime(2026, 12, 7, 0, 0, tzinfo=timezone.utc), settings) is False
    assert catalog.is_founder(datetime(2026, 12, 1), settings) is True  # naive reads as UTC
    assert catalog.is_founder(None, settings) is False


# ---------------------------------------------------------------------------
# Access windows
# ---------------------------------------------------------------------------

def sub(**kw):
    base = dict(plan="pro_monthly", status="active", trial_ends_at=None, current_period_end=None,
                stripe_subscription_id="sub_1")
    base.update(kw)
    return Subscription(**base)


def test_trial_ends_at_its_time(settings):
    s = sub(status="trialing", trial_ends_at=NOW + timedelta(hours=1))
    assert s.is_active_at(NOW) is True
    assert s.is_active_at(NOW + timedelta(hours=2)) is False
    # A coupon trial used to run forever; now it ends.
    assert sub(status="trialing", trial_ends_at=NOW - timedelta(days=1)).is_active_at(NOW) is False


def test_paid_period_has_a_grace_window(settings):
    s = sub(current_period_end=NOW)
    assert s.is_active_at(NOW + timedelta(hours=71)) is True
    assert s.is_active_at(NOW + timedelta(hours=73)) is False


def test_beta_grant_without_a_period_keeps_its_status(settings):
    s = sub(plan="premium", stripe_subscription_id=None)
    assert s.is_active_at(NOW) is True
    assert s.entitlements == frozenset()  # active, but nothing paid
    assert sub(status="past_due").is_active_at(NOW) is False
    assert sub(status="canceled").is_active_at(NOW) is False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class World:
    """An app with the billing and webhook routers over in-memory SQLite."""

    def __init__(self, monkeypatch, settings, user_created_at=NOW):
        self.engine = create_async_engine("sqlite+aiosqlite://")
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.run(self._create())
        self.user = self.run(self._user(user_created_at))
        self.calls = []

        app = FastAPI()
        app.include_router(billing_router.router)
        app.include_router(webhooks_router.router)

        async def _db():
            async with self.maker() as session:
                yield session

        async def _me():
            async with self.maker() as session:
                return (await session.execute(select(User).where(User.id == self.user.id))).scalar_one()

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user_with_db] = _me
        self.client = TestClient(app)

        async def create_customer(email, user_id):
            self.calls.append(("customer", email, user_id))
            return "cus_1"

        def session_create(**params):
            self.calls.append(("checkout", params))
            return SimpleNamespace(id="cs_1", url="https://checkout.stripe.com/c/pay/cs_1")

        def portal_create(**params):
            self.calls.append(("portal", params))
            return SimpleNamespace(url="https://billing.stripe.com/p/session/1")

        monkeypatch.setattr(stripe_service, "create_customer", create_customer)
        monkeypatch.setattr(svc_module.stripe.checkout.Session, "create", staticmethod(session_create))
        monkeypatch.setattr(svc_module.stripe.billing_portal.Session, "create", staticmethod(portal_create))
        monkeypatch.setattr(stripe_service, "settings", settings)
        # Stripe's own view of the customer: no live subscriptions and no
        # open checkouts unless a test says otherwise.
        self.stripe_live = []
        self.expired = []
        monkeypatch.setattr(stripe_service, "live_subscription_ids", lambda cid: list(self.stripe_live))
        monkeypatch.setattr(stripe_service, "expire_open_checkouts", lambda cid: self.expired.append(cid) or 0)

    def run(self, coro):
        return asyncio.run(coro)

    async def _create(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _user(self, created_at):
        async with self.maker() as session:
            user = User(cognito_id="c1", email="a@example.com", role="subscriber", created_at=created_at)
            session.add(user)
            await session.flush()
            session.add(Subscription(user_id=user.id, plan="premium", status="active"))
            await session.commit()
            return user

    def row(self):
        async def _row():
            async with self.maker() as session:
                return (await session.execute(select(Subscription))).scalar_one()
        return self.run(_row())

    def set_row(self, **kw):
        async def _set():
            async with self.maker() as session:
                r = (await session.execute(select(Subscription))).scalar_one()
                for k, v in kw.items():
                    setattr(r, k, v)
                await session.commit()
        self.run(_set())


@pytest.fixture
def world(monkeypatch, settings):
    return World(monkeypatch, settings)


def checkout_params(world):
    return [c[1] for c in world.calls if c[0] == "checkout"][-1]


def test_checkout_contract(world):
    r = world.client.post("/billing/create-checkout-session", json={"plan": "pro_monthly"})
    assert r.status_code == 200, r.text
    assert r.json() == {"url": "https://checkout.stripe.com/c/pay/cs_1",
                        "checkout_url": "https://checkout.stripe.com/c/pay/cs_1"}
    p = checkout_params(world)
    assert p["mode"] == "subscription"
    assert p["line_items"] == [{"price": "price_pro_m", "quantity": 1}]
    assert p["success_url"] == "https://www.predictium.ai/billing?success=true&session_id={CHECKOUT_SESSION_ID}"
    assert p["cancel_url"] == "https://www.predictium.ai/billing?canceled=true"
    assert p["client_reference_id"] == str(world.user.id)
    assert p["subscription_data"]["metadata"] == {"user_id": str(world.user.id), "plan": "pro_monthly"}
    assert world.row().stripe_customer_id == "cus_1"


def test_client_urls_and_prices_are_ignored(world):
    r = world.client.post("/billing/create-checkout-session", json={
        "plan": "tennis_annual", "success_url": "https://evil.example/x", "priceId": "price_evil",
    })
    assert r.status_code == 200
    p = checkout_params(world)
    assert p["line_items"][0]["price"] == "price_ten_y"
    assert "evil" not in p["success_url"] and "evil" not in p["cancel_url"]


def test_founder_gets_the_coupon_server_side_and_no_promo_codes(world):
    world.client.post("/billing/create-checkout-session", json={"plan": "bundle_annual"})
    p = checkout_params(world)
    assert p["discounts"] == [{"coupon": "FOUNDER50"}]
    assert "allow_promotion_codes" not in p


def test_non_founder_pays_full_price_and_may_use_promo_codes(monkeypatch, settings):
    monkeypatch.setattr(settings, "founder_cutoff_utc", "2026-12-07T00:00:00Z")
    w = World(monkeypatch, settings, user_created_at=datetime(2026, 12, 8, tzinfo=timezone.utc))
    w.client.post("/billing/create-checkout-session", json={"plan": "pro_monthly"})
    p = checkout_params(w)
    assert "discounts" not in p
    assert p["allow_promotion_codes"] is True


def test_checkout_refusals(world, settings, monkeypatch):
    assert world.client.post("/billing/create-checkout-session", json={"plan": "pro_season"}).status_code == 400
    assert world.client.post("/billing/create-checkout-session", json={"plan": "day"}).status_code == 400
    assert world.client.post("/billing/create-checkout-session", json={"priceId": "x", "mode": "payment"}).status_code == 422
    monkeypatch.setattr(settings, "stripe_tennis_monthly_price_id", "")
    assert world.client.post("/billing/create-checkout-session", json={"plan": "tennis_monthly"}).status_code == 503
    world.set_row(plan="pro_monthly", status="active", stripe_subscription_id="sub_9",
                  current_period_end=datetime.now(timezone.utc) + timedelta(days=10))
    r = world.client.post("/billing/create-checkout-session", json={"plan": "bundle_monthly"})
    assert r.status_code == 409


def test_portal_needs_no_body_and_returns_to_billing(world):
    world.set_row(stripe_customer_id="cus_1")
    r = world.client.post("/billing/create-portal-session")
    assert r.status_code == 200, r.text
    assert r.json()["url"] == r.json()["portal_url"] == "https://billing.stripe.com/p/session/1"
    portal = [c[1] for c in world.calls if c[0] == "portal"][-1]
    assert portal == {"customer": "cus_1", "return_url": "https://www.predictium.ai/billing"}


def test_portal_without_a_customer_is_400(world):
    assert world.client.post("/billing/create-portal-session").status_code == 400


def test_entitlements(world):
    r = world.client.get("/billing/entitlements").json()
    assert r["products"] == [] and r["paid"] is False and r["plan"] == "premium" and r["founder"] is True
    world.set_row(plan="bundle_annual", status="active", stripe_subscription_id="sub_1",
                  current_period_end=datetime.now(timezone.utc) + timedelta(days=300))
    r = world.client.get("/billing/entitlements").json()
    assert r["products"] == ["pro", "tennis"] and r["paid"] is True
    world.set_row(plan="tennis_monthly", current_period_end=datetime.now(timezone.utc) - timedelta(days=10))
    assert world.client.get("/billing/entitlements").json()["products"] == []


def test_subscription_read_carries_entitlements(world):
    world.set_row(plan="pro_annual", status="active", stripe_subscription_id="sub_1",
                  current_period_end=datetime.now(timezone.utc) + timedelta(days=30))
    r = world.client.get("/billing/subscription").json()
    assert r["entitlements"] == ["pro"] and r["interval"] == "year" and r["founder"] is True
    assert r["plan"] == "pro_annual" and r["is_active"] is True


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def stripe_sub(status="active", price="price_pro_m", sid="sub_1", customer="cus_1", end=None,
               end_on_item=False, trial_end=None, user_id=None):
    end = end or int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
    item = {"price": {"id": price}}
    obj = {"id": sid, "customer": customer, "status": status, "trial_end": trial_end,
           "metadata": {"user_id": user_id} if user_id else {}, "items": {"data": [item]}}
    if end_on_item:
        item["current_period_end"] = end  # API 2025-03-31 and later
    else:
        obj["current_period_end"] = end
    return obj


def post_event(world, monkeypatch, event, current):
    monkeypatch.setattr(stripe_service, "verify_webhook_signature", lambda payload, signature: event)
    monkeypatch.setattr(stripe_service, "retrieve_subscription", lambda sid: current)
    return world.client.post("/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "t=1,v1=x"})


def test_checkout_completed_activates_the_plan(world, monkeypatch):
    world.set_row(stripe_customer_id="cus_1")
    event = {"id": "evt_1", "type": "checkout.session.completed",
             "data": {"object": {"id": "cs_1", "mode": "subscription", "subscription": "sub_1",
                                 "customer": "cus_1", "client_reference_id": str(world.user.id)}}}
    r = post_event(world, monkeypatch, event, stripe_sub(price="price_bun_y"))
    assert r.status_code == 200, r.text
    row = world.row()
    assert (row.plan, row.status, row.stripe_subscription_id) == ("bundle_annual", "active", "sub_1")
    assert row.entitlements == {"pro", "tennis"}


def test_replay_is_idempotent_and_period_end_reads_from_items(world, monkeypatch):
    world.set_row(stripe_customer_id="cus_1")
    event = {"id": "evt_2", "type": "customer.subscription.updated",
             "data": {"object": {"id": "sub_1", "customer": "cus_1"}}}
    current = stripe_sub(price="price_ten_m", end_on_item=True)
    assert post_event(world, monkeypatch, event, current).status_code == 200
    first = world.row()
    assert post_event(world, monkeypatch, event, current).status_code == 200
    second = world.row()
    utc = lambda d: d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d  # SQLite drops the zone
    assert (first.plan, utc(first.current_period_end)) == (second.plan, utc(second.current_period_end)) == (
        "tennis_monthly", subscription_period_end(current))


def test_cancellation_returns_the_user_to_free(world, monkeypatch):
    world.set_row(stripe_customer_id="cus_1", stripe_subscription_id="sub_1", plan="pro_monthly")
    event = {"id": "evt_3", "type": "customer.subscription.deleted",
             "data": {"object": {"id": "sub_1", "customer": "cus_1"}}}
    assert post_event(world, monkeypatch, event, stripe_sub(status="canceled")).status_code == 200
    row = world.row()
    assert (row.plan, row.status) == ("free", "canceled") and row.entitlements == frozenset()


def test_payment_failure_marks_past_due_and_locks(world, monkeypatch):
    world.set_row(stripe_customer_id="cus_1", stripe_subscription_id="sub_1", plan="pro_monthly")
    event = {"id": "evt_4", "type": "invoice.payment_failed",
             "data": {"object": {"id": "in_1", "customer": "cus_1", "subscription": "sub_1"}}}
    assert post_event(world, monkeypatch, event, stripe_sub(status="past_due")).status_code == 200
    assert world.row().status == "past_due" and world.row().entitlements == frozenset()


def test_failure_is_not_acknowledged(world, monkeypatch):
    # An unknown price is a configuration error: 500 so Stripe retries, and
    # the row is left as it was (never silently downgraded to free).
    world.set_row(stripe_customer_id="cus_1")
    event = {"id": "evt_5", "type": "customer.subscription.updated",
             "data": {"object": {"id": "sub_1", "customer": "cus_1"}}}
    r = post_event(world, monkeypatch, event, stripe_sub(price="price_unknown"))
    assert r.status_code == 500
    assert world.row().plan == "premium"
    # No row at all for this customer: a retry can never fix it, so it is
    # acknowledged (an ALERT in the log), not a three-day 500 loop.
    event["data"]["object"]["customer"] = "cus_other"
    r = post_event(world, monkeypatch, event, stripe_sub(customer="cus_other", sid="sub_x"))
    assert r.status_code == 200
    assert world.row().plan == "premium"


def test_unhandled_events_are_acknowledged(world, monkeypatch):
    event = {"id": "evt_6", "type": "charge.refunded", "data": {"object": {}}}
    assert post_event(world, monkeypatch, event, None).status_code == 200


def test_bad_signature_is_400(world, monkeypatch):
    def bad(payload, signature):
        raise ValueError("bad")
    monkeypatch.setattr(stripe_service, "verify_webhook_signature", bad)
    r = world.client.post("/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "x"})
    assert r.status_code == 400


def test_every_stripe_status_maps_to_an_internal_one():
    allowed = {"trialing", "active", "past_due", "canceled", "expired"}
    assert set(STATUS_MAP.values()) <= allowed
    # incomplete means the first payment has not succeeded: never access.
    assert STATUS_MAP["incomplete"] == "expired"


def test_signup_auto_premium_flag(monkeypatch, settings):
    import inspect
    from app import dependencies
    src = inspect.getsource(dependencies.get_current_user_with_db)
    assert 'plan="premium" if get_settings().beta_auto_premium else "free"' in src
    assert settings.beta_auto_premium is True  # unchanged until paid launch


# ---------------------------------------------------------------------------
# Portfolio review of #20 (85879a0): the three blockers
# ---------------------------------------------------------------------------

def live_row(world, sid="sub_2", plan="bundle_annual", status="active"):
    world.set_row(stripe_customer_id="cus_1", stripe_subscription_id=sid, plan=plan, status=status,
                  current_period_end=datetime.now(timezone.utc) + timedelta(days=300))


def test_stale_event_for_an_old_subscription_never_overwrites_the_live_one(world, monkeypatch):
    # The user canceled sub_1, then bought a bundle as sub_2. A late
    # `deleted` (and an `updated`) for sub_1 must leave sub_2's row alone.
    live_row(world)
    for kind in ("customer.subscription.deleted", "customer.subscription.updated"):
        event = {"id": "evt_s", "type": kind, "data": {"object": {"id": "sub_1", "customer": "cus_1"}}}
        r = post_event(world, monkeypatch, event, stripe_sub(status="canceled", sid="sub_1"))
        assert r.status_code == 200
        row = world.row()
        assert (row.stripe_subscription_id, row.plan, row.status) == ("sub_2", "bundle_annual", "active")
        assert row.entitlements == {"pro", "tennis"}


def test_a_new_live_subscription_takes_over_a_dead_row(world, monkeypatch):
    world.set_row(stripe_customer_id="cus_1", stripe_subscription_id="sub_1", plan="free", status="canceled")
    event = {"id": "evt_n", "type": "customer.subscription.created",
             "data": {"object": {"id": "sub_2", "customer": "cus_1"}}}
    assert post_event(world, monkeypatch, event, stripe_sub(sid="sub_2", price="price_pro_y")).status_code == 200
    row = world.row()
    assert (row.stripe_subscription_id, row.plan, row.status) == ("sub_2", "pro_annual", "active")


def test_the_409_holds_for_past_due_and_for_what_stripe_says(world):
    live_row(world, plan="pro_monthly", status="past_due")
    r = world.client.post("/billing/create-checkout-session", json={"plan": "bundle_monthly"})
    assert r.status_code == 409
    # The row says nothing is live (lagged or overwritten); Stripe says sub_7 is.
    world.set_row(stripe_subscription_id=None, plan="premium", status="active")
    world.stripe_live = ["sub_7"]
    assert world.client.post("/billing/create-checkout-session", json={"plan": "pro_monthly"}).status_code == 409
    assert not [c for c in world.calls if c[0] == "checkout"]


def test_a_new_checkout_expires_the_older_open_one(world):
    world.set_row(stripe_customer_id="cus_1")
    assert world.client.post("/billing/create-checkout-session", json={"plan": "pro_monthly"}).status_code == 200
    assert world.expired == ["cus_1"]


def test_coupon_cannot_overwrite_a_paying_row(world):
    async def add_coupon():
        async with world.maker() as session:
            session.add(Coupon(code="BETA7", plan="premium", trial_days=7, current_uses=0, is_active=True))
            await session.commit()
    world.run(add_coupon())
    live_row(world)
    r = world.client.post("/billing/redeem-coupon", json={"code": "BETA7"})
    assert r.status_code == 409
    row = world.row()
    assert (row.plan, row.status) == ("bundle_annual", "active")


def test_malformed_founder_cutoff_fails_at_settings_load():
    from app.config import Settings
    with pytest.raises(Exception):
        Settings(founder_cutoff_utc="next tuesday")
    assert Settings(founder_cutoff_utc=" 2026-12-07T00:00:00Z ").founder_cutoff_utc == "2026-12-07T00:00:00Z"


def test_paid_launch_refuses_a_fail_open_configuration():
    from app.config import Settings
    with pytest.raises(Exception) as e:
        Settings(paid_launch=True)
    for name in ("FOUNDER_CUTOFF_UTC", "STRIPE_FOUNDER_COUPON_ID", "BETA_AUTO_PREMIUM=false"):
        assert name in str(e.value)
    ok = Settings(paid_launch=True, founder_cutoff_utc="2026-12-07T00:00:00Z",
                  stripe_founder_coupon_id="FOUNDER50", beta_auto_premium=False)
    assert ok.paid_launch is True


def test_past_due_access_is_a_deliberate_setting(settings, monkeypatch):
    s = sub(status="past_due", current_period_end=NOW + timedelta(days=5))
    assert s.is_active_at(NOW) is False
    monkeypatch.setattr(settings, "past_due_grants_access", True)
    assert s.is_active_at(NOW) is True
    assert s.has_live_stripe_subscription is True


def test_duplicate_customer_rows_do_not_raise(world, monkeypatch):
    async def dup():
        async with world.maker() as session:
            other = User(cognito_id="c2", email="b@example.com", role="subscriber", created_at=NOW)
            session.add(other)
            await session.flush()
            session.add(Subscription(user_id=other.id, plan="free", status="active", stripe_customer_id="cus_1"))
            await session.commit()
    world.set_row(stripe_customer_id="cus_1")
    world.run(dup())
    event = {"id": "evt_d", "type": "customer.subscription.updated",
             "data": {"object": {"id": "sub_9", "customer": "cus_1"}}}
    assert post_event(world, monkeypatch, event, stripe_sub(sid="sub_9")).status_code == 200

