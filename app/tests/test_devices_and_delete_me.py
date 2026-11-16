"""POST /devices, DELETE /devices/{id} and DELETE /me (predictium-app
docs/API_PROPOSAL.md sections 1 and 5).

Tokens go through the real Cognito verification code, signed with a
throwaway RSA key. Stripe, Cognito AdminDeleteUser and RevenueCat are mocks:
nothing here reaches a real service. The database is SQLite by default, or
the Postgres at TEST_PG_URL, where the schema comes from the SQL migration.
"""

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import stripe
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwk, jwt
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.database import Base, get_db
from app.main import app
from app.models import (
    AccountDeletion,
    Coupon,
    CouponRedemption,
    PageView,
    PushDevice,
    Subscription,
    User,
)
from app.models.account_deletion import hash_sub
from app.routers.devices import prune_stale_devices
from app.services import account_deletion
from app.services.cognito import cognito_service
from app.services.rate_limit import delete_me_limiter, devices_limiter

WEB = "web-client-id"
MOBILE = "2du0mq32r8o5lua5m089hrsdeo"  # predictium-mobile
POOL = "us-east-2_testpool"
REGION = "us-east-2"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
TOKEN_A = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"
TOKEN_B = "ExponentPushToken[bbbbbbbbbbbbbbbbbbbbbb]"
PG_URL = os.environ.get("TEST_PG_URL")
MIGRATIONS = Path(__file__).resolve().parents[2] / "database" / "migrations"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_private_pem = _key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
_public_jwk = {
    **jwk.construct(
        _key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode(),
        "RS256",
    ).to_dict(),
    "kid": "k1",
    "use": "sig",
}


def id_token(sub="sub-a", aud=MOBILE, **extra):
    claims = {
        "sub": sub,
        "iss": ISSUER,
        "exp": int(time.time()) + 600,
        "token_use": "id",
        "aud": aud,
        "email": f"{sub}@example.com",
        "cognito:username": f"user-{sub}",
        **extra,
    }
    return jwt.encode(claims, _private_pem, algorithm="RS256", headers={"kid": "k1"})


def auth(sub="sub-a", aud=MOBILE):
    return {"Authorization": f"Bearer {id_token(sub, aud)}"}


def reg(token=TOKEN_A, platform="ios", app_version="1.0.0"):
    return {"expo_push_token": token, "platform": platform, "app_version": app_version}


# --- fixtures -------------------------------------------------------------


_DROP = ("DROP TABLE IF EXISTS account_deletions, push_devices, coupon_redemptions, "
         "coupons, subscriptions, page_views, users CASCADE")


async def _create_schema(engine):
    async with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            await conn.execute(text(_DROP))
            base = [User.__table__, Subscription.__table__, Coupon.__table__,
                    CouponRedemption.__table__, PageView.__table__]
            await conn.run_sync(Base.metadata.create_all, tables=base)
            raw = (await conn.get_raw_connection()).driver_connection
            for f in sorted(MIGRATIONS.glob("*.sql")):
                sql = f.read_text()
                if "CONCURRENTLY" in sql:
                    continue  # needs autocommit; applied below
                await raw.execute(sql.replace("BEGIN;", "").replace("COMMIT;", ""))
        else:
            tables = [User.__table__, Subscription.__table__, Coupon.__table__,
                      CouponRedemption.__table__, PageView.__table__,
                      PushDevice.__table__, AccountDeletion.__table__]
            await conn.run_sync(Base.metadata.create_all, tables=tables)
    if engine.dialect.name == "postgresql":
        auto = engine.execution_options(isolation_level="AUTOCOMMIT")
        async with auto.connect() as conn:
            raw = (await conn.get_raw_connection()).driver_connection
            await raw.execute((MIGRATIONS / "010_page_views_user_id_index.sql").read_text())


async def _drop_schema(engine):
    if engine.dialect.name != "postgresql":
        return  # the SQLite file lives in tmp_path
    async with engine.begin() as conn:
        await conn.execute(text(_DROP))


@pytest.fixture
def env(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "cognito_user_pool_id", POOL)
    monkeypatch.setattr(s, "cognito_region", REGION)
    monkeypatch.setattr(s, "cognito_client_id", WEB)
    monkeypatch.setattr(s, "cognito_client_ids", f"{WEB},{MOBILE}")
    monkeypatch.setattr(s, "revenuecat_secret_key", "")

    async def jwks():
        return {"keys": [_public_jwk]}

    monkeypatch.setattr(cognito_service, "_fetch_jwks", jwks)

    url = PG_URL or f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    # echo on, as in development, with the same hide_parameters as the app
    # NullPool: TestClient and the helpers below run on different event loops.
    engine = create_async_engine(url, echo=True, hide_parameters=True, poolclass=NullPool)
    asyncio.run(_create_schema(engine))
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _db
    devices_limiter.reset()
    delete_me_limiter.reset()

    # Stripe: test-mode key only, every call a mock.
    monkeypatch.setattr(stripe, "api_key", "sk_test_dummy")
    calls = {"list": [], "modify": [], "retrieve": [], "cancel": [], "cognito": [], "revenuecat": []}
    stripe_subs = {}  # plain dicts here; every fake returns a real Stripe object

    def _obj(d):
        # stripe-python 15+ objects are not dicts: .get() raises on them.
        return stripe.Subscription.construct_from(dict(d), "sk_test_dummy")

    class _Page:
        def __init__(self, items):
            self.items = items

        def auto_paging_iter(self):
            return iter(self.items)

    def _list(**kw):
        assert stripe.api_key.startswith("sk_test_")
        calls["list"].append(kw)
        return _Page([_obj(s) for s in stripe_subs.values() if s["customer"] == kw["customer"]])

    def _modify(sid, **kw):
        assert stripe.api_key.startswith("sk_test_")
        calls["modify"].append((sid, kw))
        stripe_subs[sid]["cancel_at_period_end"] = kw.get("cancel_at_period_end")
        return _obj(stripe_subs[sid])

    def _retrieve(sid, **kw):
        calls["retrieve"].append(sid)
        if sid not in stripe_subs:
            raise stripe.InvalidRequestError("No such subscription", "id", code="resource_missing")
        return _obj(stripe_subs[sid])

    def _cancel(sid, **kw):
        assert stripe.api_key.startswith("sk_test_")
        calls["cancel"].append(sid)
        stripe_subs[sid]["status"] = "canceled"
        return _obj(stripe_subs[sid])

    monkeypatch.setattr(stripe.Subscription, "list", _list)
    monkeypatch.setattr(stripe.Subscription, "modify", _modify)
    monkeypatch.setattr(stripe.Subscription, "cancel", _cancel)
    monkeypatch.setattr(stripe.Subscription, "retrieve", _retrieve)

    # Cognito: a fake client; the session's real AWS credentials are never used.
    cognito_users = set()

    class _FakeCognito:
        fail = False

        def admin_delete_user(self, UserPoolId, Username):
            calls["cognito"].append((UserPoolId, Username))
            if _FakeCognito.fail:
                raise ClientError({"Error": {"Code": "InternalErrorException"}}, "AdminDeleteUser")
            if Username not in cognito_users:
                raise ClientError({"Error": {"Code": "UserNotFoundException"}}, "AdminDeleteUser")
            cognito_users.discard(Username)

    monkeypatch.setattr(account_deletion, "_cognito_client", lambda: _FakeCognito())

    rc_status = {"code": 200}

    def _rc(request: httpx.Request):
        calls["revenuecat"].append((request.method, str(request.url), request.headers.get("authorization")))
        return httpx.Response(rc_status["code"])

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        account_deletion.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(_rc), **kw),
    )

    def run(coro_fn):
        async def go():
            async with maker() as db:
                return await coro_fn(db)
        return asyncio.run(go())

    ctx = type("Env", (), dict(
        client=TestClient(app), calls=calls, stripe_subs=stripe_subs, cognito_users=cognito_users,
        cognito=_FakeCognito, rc_status=rc_status, run=staticmethod(run), engine=engine,
    ))
    yield ctx
    app.dependency_overrides.pop(get_db, None)
    asyncio.run(_drop_schema(engine))
    asyncio.run(engine.dispose())


def _devices(env):
    async def q(db):
        rows = (await db.execute(select(PushDevice))).scalars().all()
        return {str(r.id): r for r in rows}
    return env.run(q)


def _user(env, sub):
    async def q(db):
        return (await db.execute(select(User).where(User.cognito_id == sub))).scalar_one_or_none()
    return env.run(q)


# --- auth -----------------------------------------------------------------


def test_auth_required_on_every_route(env):
    c = env.client
    did = str(uuid.uuid4())
    for method, path, body in [("POST", "/devices", reg()), ("DELETE", f"/devices/{did}", None),
                               ("DELETE", "/me", None)]:
        assert c.request(method, path, json=body).status_code == 401
        assert c.request(method, path, json=body, headers={"Authorization": "Bearer junk"}).status_code == 401
        assert c.request(method, path, json=body, headers=auth(aud="some-other-client")).status_code == 401
    assert _devices(env) == {}
    assert env.calls["cognito"] == [] and env.calls["modify"] == []


def test_website_and_mobile_client_tokens_both_verify(env):
    a = env.client.post("/devices", json=reg(TOKEN_A), headers=auth("sub-web", WEB))
    b = env.client.post("/devices", json=reg(TOKEN_B), headers=auth("sub-app", MOBILE))
    assert a.status_code == 201 and b.status_code == 201


# --- POST /devices ----------------------------------------------------------


def test_register_returns_device_id_and_upserts_on_token(env):
    r = env.client.post("/devices", json=reg(), headers=auth())
    assert r.status_code == 201
    device_id = r.json()["device_id"]
    uuid.UUID(device_id)

    old = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def age(db):
        await db.execute(update(PushDevice).values(last_seen_at=old))
        await db.commit()
    env.run(age)

    r2 = env.client.post("/devices", json=reg(app_version="1.1.0"), headers=auth())
    assert r2.status_code == 201 and r2.json()["device_id"] == device_id
    rows = _devices(env)
    assert list(rows) == [device_id]
    row = rows[device_id]
    assert row.app_version == "1.1.0"
    seen = row.last_seen_at if row.last_seen_at.tzinfo else row.last_seen_at.replace(tzinfo=timezone.utc)
    assert seen > old


def test_token_moves_to_the_account_that_registers_it(env):
    first = env.client.post("/devices", json=reg(), headers=auth("sub-a")).json()["device_id"]
    second = env.client.post("/devices", json=reg(), headers=auth("sub-b")).json()["device_id"]
    assert first == second
    rows = _devices(env)
    assert len(rows) == 1
    assert rows[first].user_id == _user(env, "sub-b").id


def test_app_version_optional_and_android(env):
    r = env.client.post("/devices", json=reg(platform="android", app_version=None), headers=auth())
    assert r.status_code == 201


@pytest.mark.parametrize("body", [
    reg(token="not-a-token"),
    reg(token="ExponentPushToken[]"),
    reg(platform="windows"),
    {"platform": "ios"},
    {"expo_push_token": ["ExponentPushToken[zzzzzzzzzzzzzz]"], "platform": "ios"},
    reg(app_version="x" * 65),
])
def test_bad_bodies_are_422_and_never_echo_the_token(env, body):
    r = env.client.post("/devices", json=body, headers=auth())
    assert r.status_code == 422
    assert "ExponentPushToken" not in r.text and "not-a-token" not in r.text
    assert _devices(env) == {}


# --- DELETE /devices/{id} -------------------------------------------------


def test_one_user_cannot_delete_another_users_device(env):
    did = env.client.post("/devices", json=reg(), headers=auth("sub-a")).json()["device_id"]
    r = env.client.delete(f"/devices/{did}", headers=auth("sub-b"))
    assert r.status_code == 204  # same answer as a missing id: no oracle
    assert did in _devices(env)


def test_delete_device_is_idempotent(env):
    did = env.client.post("/devices", json=reg(), headers=auth()).json()["device_id"]
    other = env.client.post("/devices", json=reg(TOKEN_B), headers=auth()).json()["device_id"]
    for _ in range(2):
        r = env.client.delete(f"/devices/{did}", headers=auth())
        assert r.status_code == 204 and r.content == b""
    assert list(_devices(env)) == [other]
    assert env.client.delete(f"/devices/{uuid.uuid4()}", headers=auth()).status_code == 204


def test_bad_device_id_is_422_without_echo(env):
    r = env.client.delete(f"/devices/{TOKEN_A}", headers=auth())
    assert r.status_code == 422 and "ExponentPushToken" not in r.text


# --- logs -------------------------------------------------------------------


def test_push_token_never_appears_in_logs(env, caplog):
    import app.db.database as database

    assert database.engine.sync_engine.hide_parameters is True
    caplog.set_level(logging.DEBUG)
    c = env.client
    did = c.post("/devices", json=reg(), headers=auth("sub-a")).json()["device_id"]
    c.post("/devices", json=reg(), headers=auth("sub-a"))  # re-register
    c.post("/devices", json=reg(), headers=auth("sub-b"))  # shared phone
    c.post("/devices", json=reg(token="ExponentPushToken"), headers=auth())  # 422
    c.post("/devices", json=reg(platform="bad"), headers=auth())  # 422
    c.delete(f"/devices/{did}", headers=auth("sub-b"))
    c.delete("/me", headers=auth("sub-b"))
    # Every logger is captured, SQL echo included. The one exception is the
    # SQLite test driver's own DEBUG trace (aiosqlite), which exists only in
    # this test setup: production runs on asyncpg, which logs no queries.
    assert database.engine.dialect.driver == "asyncpg"
    records = [r for r in caplog.records if not r.name.startswith("aiosqlite")]
    assert any(r.name.startswith("sqlalchemy.engine") for r in records)
    for rec in records:
        msg = rec.getMessage() + (rec.exc_text or "")
        assert "ExponentPushToken[" not in msg
        assert "aaaaaaaaaaaaaaaaaaaaaa" not in msg


# --- rate limits ------------------------------------------------------------


def test_devices_rate_limited_per_sub(env):
    for _ in range(30):
        assert env.client.post("/devices", json=reg(), headers=auth("sub-a")).status_code == 201
    r = env.client.post("/devices", json=reg(), headers=auth("sub-a"))
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert env.client.delete(f"/devices/{uuid.uuid4()}", headers=auth("sub-a")).status_code == 429
    assert env.client.post("/devices", json=reg(TOKEN_B), headers=auth("sub-b")).status_code == 201


def test_delete_me_rate_limited_per_sub(env):
    for _ in range(5):
        assert env.client.delete("/me", headers=auth("sub-a")).status_code == 202
    assert env.client.delete("/me", headers=auth("sub-a")).status_code == 429
    assert env.client.delete("/me", headers=auth("sub-b")).status_code == 202


# --- DELETE /me -------------------------------------------------------------


def _seed_paying_user(env, sub="sub-a"):
    """A user with devices, a Stripe subscription, analytics rows and a coupon."""
    env.client.post("/devices", json=reg(TOKEN_A), headers=auth(sub))
    env.client.post("/devices", json=reg(TOKEN_B), headers=auth(sub))
    env.cognito_users.add(f"user-{sub}")
    env.stripe_subs["sub_active_1"] = {"id": "sub_active_1", "customer": "cus_1",
                                       "status": "active", "cancel_at_period_end": False}
    env.stripe_subs["sub_old"] = {"id": "sub_old", "customer": "cus_1",
                                  "status": "canceled", "cancel_at_period_end": False}

    async def seed(db):
        user = (await db.execute(select(User).where(User.cognito_id == sub))).scalar_one()
        await db.execute(update(Subscription).where(Subscription.user_id == user.id).values(
            stripe_customer_id="cus_1", stripe_subscription_id="sub_active_1"))
        db.add(Coupon(code=f"C-{sub}", plan="premium", created_by=user.id))
        await db.flush()
        db.add(CouponRedemption(user_id=user.id, coupon_code=f"C-{sub}"))
        for i in range(2):
            db.add(PageView(id=i + 1 if sub == "sub-a" else i + 100, path="/nfl", user_id=sub))
        db.add(PageView(id=9999 if sub == "sub-a" else 9998, path="/", user_id=None))
        await db.commit()
        return user.id
    return env.run(seed)


def _counts(env, sub, uid):
    async def q(db):
        n = lambda stmt: db.scalar(stmt)  # noqa: E731
        return {
            "users": await n(select(func.count()).select_from(User).where(User.cognito_id == sub)),
            "subscriptions": await n(select(func.count()).select_from(Subscription).where(Subscription.user_id == uid)),
            "devices": await n(select(func.count()).select_from(PushDevice).where(PushDevice.user_id == uid)),
            "page_views": await n(select(func.count()).select_from(PageView).where(PageView.user_id == sub)),
            "redemptions": await n(select(func.count()).select_from(CouponRedemption).where(CouponRedemption.user_id == uid)),
            "audit": await n(select(func.count()).select_from(AccountDeletion).where(AccountDeletion.sub_hash == hash_sub(sub))),
        }
    return env.run(q)


GONE = {"users": 0, "subscriptions": 0, "devices": 0, "page_views": 0, "redemptions": 0, "audit": 1}
KEPT = {"users": 1, "subscriptions": 1, "devices": 2, "page_views": 2, "redemptions": 1, "audit": 0}


def test_delete_me_removes_everything_and_cancels_stripe(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "revenuecat_secret_key", "sk_rc_test_dummy")
    uid = _seed_paying_user(env)
    _seed_paying_user(env, "sub-b")  # someone else, untouched

    r = env.client.delete("/me", headers=auth())
    assert r.status_code == 202 and r.json() == {"status": "deleted"}
    assert _counts(env, "sub-a", uid) == GONE
    # Stripe: only the live subscription, cancelled at period end, test key
    assert env.calls["modify"] == [("sub_active_1", {"cancel_at_period_end": True})]
    assert env.calls["list"][0]["customer"] == "cus_1"
    # Cognito user removed by its username, in this pool
    assert env.calls["cognito"] == [(POOL, "user-sub-a")]
    assert "user-sub-a" not in env.cognito_users
    # RevenueCat subscriber keyed on the sub
    method, url, authz = env.calls["revenuecat"][0]
    assert method == "DELETE" and url.endswith("/v1/subscribers/sub-a") and authz == "Bearer sk_rc_test_dummy"

    async def leftovers(db):
        coupon = (await db.execute(select(Coupon).where(Coupon.code == "C-sub-a"))).scalar_one()
        anon = await db.scalar(select(func.count()).select_from(PageView).where(PageView.user_id.is_(None)))
        audit = (await db.execute(select(AccountDeletion))).scalars().all()
        return coupon.created_by, anon, audit
    created_by, anon, audit = env.run(leftovers)
    assert created_by is None and anon == 2
    assert [a.sub_hash for a in audit] == [hash_sub("sub-a")]  # hash only, never the sub
    other = _user(env, "sub-b")
    assert _counts(env, "sub-b", other.id)["devices"] == 2


def test_delete_me_is_idempotent(env):
    uid = _seed_paying_user(env)
    assert env.client.delete("/me", headers=auth()).status_code == 202
    assert env.client.delete("/me", headers=auth()).status_code == 202
    assert _counts(env, "sub-a", uid) == GONE
    assert len(env.calls["modify"]) == 1  # Stripe not touched again
    assert len(env.calls["cognito"]) == 2  # second call sees UserNotFound, fine


def test_delete_me_for_a_user_with_no_row(env):
    r = env.client.delete("/me", headers=auth("sub-never-seen"))
    assert r.status_code == 202
    assert env.calls["modify"] == [] and env.calls["cognito"] == [(POOL, "user-sub-never-seen")]


def test_customer_already_deleted_in_stripe_does_not_block_deletion(env, monkeypatch):
    uid = _seed_paying_user(env)
    env.stripe_subs.clear()

    def missing(**kw):
        raise stripe.InvalidRequestError("No such customer", "customer", code="resource_missing")
    monkeypatch.setattr(stripe.Subscription, "list", missing)
    assert env.client.delete("/me", headers=auth()).status_code == 202
    assert _counts(env, "sub-a", uid) == GONE


def test_stripe_already_cancelling_is_not_modified(env):
    _seed_paying_user(env)
    env.stripe_subs["sub_active_1"]["cancel_at_period_end"] = True
    assert env.client.delete("/me", headers=auth()).status_code == 202
    assert env.calls["modify"] == []


def test_stripe_failure_deletes_nothing_and_retry_succeeds(env, monkeypatch):
    uid = _seed_paying_user(env)
    working = stripe.Subscription.list
    state = {"fail": True}

    def flaky(**kw):
        if state["fail"]:
            raise stripe.APIConnectionError("network down")
        return working(**kw)
    monkeypatch.setattr(stripe.Subscription, "list", flaky)

    r = env.client.delete("/me", headers=auth())
    assert r.status_code == 503 and r.json()["step"] == "billing"
    assert "Retry-After" in r.headers
    assert _counts(env, "sub-a", uid) == KEPT  # nothing deleted
    assert env.calls["cognito"] == []
    state["fail"] = False
    assert env.client.delete("/me", headers=auth()).status_code == 202
    assert _counts(env, "sub-a", uid) == GONE
    assert env.calls["modify"] == [("sub_active_1", {"cancel_at_period_end": True})]


def test_revenuecat_failure_keeps_data_and_identity(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "revenuecat_secret_key", "sk_rc_test_dummy")
    uid = _seed_paying_user(env)
    env.rc_status["code"] = 500
    r = env.client.delete("/me", headers=auth())
    assert r.status_code == 503 and r.json()["step"] == "store_entitlements"
    assert _counts(env, "sub-a", uid) == KEPT
    assert env.calls["cognito"] == []
    env.rc_status["code"] = 404  # already gone counts as done
    assert env.client.delete("/me", headers=auth()).status_code == 202
    assert _counts(env, "sub-a", uid) == GONE


def test_database_failure_rolls_back_and_keeps_identity(env, monkeypatch):
    uid = _seed_paying_user(env)
    real_commit = AsyncSession.commit
    state = {"fail": True}

    async def flaky_commit(self):
        if state["fail"]:
            state["fail"] = False  # the data step's commit fails once
            raise OperationalError("COMMIT", {}, Exception("connection lost"))
        return await real_commit(self)
    monkeypatch.setattr(AsyncSession, "commit", flaky_commit)

    r = env.client.delete("/me", headers=auth())
    assert r.status_code == 503 and r.json()["step"] == "data"
    assert _counts(env, "sub-a", uid) == KEPT
    assert env.calls["cognito"] == []
    assert env.client.delete("/me", headers=auth()).status_code == 202
    assert _counts(env, "sub-a", uid) == GONE


def test_cognito_failure_is_retryable_and_the_user_does_not_come_back(env):
    uid = _seed_paying_user(env)
    env.cognito.fail = True
    r = env.client.delete("/me", headers=auth())
    assert r.status_code == 503 and r.json()["step"] == "identity"
    assert _counts(env, "sub-a", uid) == GONE  # data is gone, sign-in still works
    # The still-valid token cannot re-create the user through another route.
    assert env.client.post("/devices", json=reg(), headers=auth()).status_code == 401
    assert _user(env, "sub-a") is None
    env.cognito.fail = False
    assert env.client.delete("/me", headers=auth()).status_code == 202
    assert "user-sub-a" not in env.cognito_users


def test_app_reads_the_responses_as_designed(env):
    """delete-account.ts: 2xx deleted, 401 signedOut, 404/405/501 notReady,
    anything else failed (retry). DELETE /me must never answer 404/405/501."""
    assert env.client.delete("/me").status_code == 401
    assert env.client.delete("/me", headers=auth()).status_code == 202
    env.cognito.fail = True
    assert env.client.delete("/me", headers=auth("sub-z")).status_code == 503


# --- expiry -----------------------------------------------------------------


def test_prune_removes_only_devices_unseen_for_30_days(env):
    env.client.post("/devices", json=reg(TOKEN_A), headers=auth())
    keep = env.client.post("/devices", json=reg(TOKEN_B), headers=auth()).json()["device_id"]
    now = datetime.now(timezone.utc)

    async def age_and_prune(db):
        await db.execute(update(PushDevice).where(PushDevice.expo_push_token == TOKEN_A)
                         .values(last_seen_at=now - timedelta(days=31)))
        await db.execute(update(PushDevice).where(PushDevice.expo_push_token == TOKEN_B)
                         .values(last_seen_at=now - timedelta(days=29)))
        await db.commit()
        return await prune_stale_devices(db, now=now)
    assert env.run(age_and_prune) == 1
    assert list(_devices(env)) == [keep]


def test_missing_page_views_table_is_skipped_not_a_failure(env):
    uid = _seed_paying_user(env)

    async def drop(db):
        await db.execute(text("DROP TABLE page_views"))
        await db.commit()
    env.run(drop)
    assert env.client.delete("/me", headers=auth()).status_code == 202
    assert _user(env, "sub-a") is None

    async def audit(db):
        return await db.scalar(select(func.count()).select_from(AccountDeletion))
    assert env.run(audit) == 1 and uid


# --- Stripe objects and webhooks -------------------------------------------
# Real stripe.StripeObject instances (construct_from), never plain dicts:
# from stripe-python 15 they have no .get().

from app.services.stripe_fields import field, period_end  # noqa: E402
from app.services.stripe_service import stripe_service  # noqa: E402


STRIPE_MAJOR = int(stripe.VERSION.split(".")[0])


def _event(kind, obj):
    return stripe.Event.construct_from(
        {"id": "evt_1", "type": kind, "data": {"object": obj}}, "sk_test_dummy"
    )


def test_field_and_period_end_on_real_stripe_objects():
    old_shape = stripe.Subscription.construct_from(
        {"id": "sub_1", "status": "active", "current_period_end": 1800000000}, "sk_test_dummy")
    new_shape = stripe.Subscription.construct_from(
        {"id": "sub_2", "status": "active",
         "items": {"object": "list", "data": [{"id": "si_1", "current_period_end": 1800000500}]}},
        "sk_test_dummy")
    if STRIPE_MAJOR >= 15:
        with pytest.raises(AttributeError):
            old_shape.get("status")  # the trap this helper exists for
    else:
        assert old_shape.get("status") == "active"  # still a dict before 15
    assert field(old_shape, "status") == "active"
    assert field(old_shape, "trial_end") is None and field(None, "x", 3) == 3
    assert period_end(old_shape) == datetime.fromtimestamp(1800000000, tz=timezone.utc)
    assert period_end(new_shape) == datetime.fromtimestamp(1800000500, tz=timezone.utc)


def _website_user(env, sub="sub-w"):
    env.client.post("/devices", json=reg(TOKEN_A), headers=auth(sub))  # creates the user rows

    async def link(db):
        user = (await db.execute(select(User).where(User.cognito_id == sub))).scalar_one()
        await db.execute(update(Subscription).where(Subscription.user_id == user.id).values(
            stripe_customer_id="cus_w", stripe_subscription_id=None, plan="free", status="trialing"))
        await db.commit()
        return user.id
    return env.run(link)


def _sub_row(env, uid):
    async def q(db):
        return (await db.execute(select(Subscription).where(Subscription.user_id == uid))).scalar_one()
    return env.run(q)


def test_checkout_completed_on_current_stripe_objects(env, monkeypatch):
    uid = _website_user(env)
    monkeypatch.setattr(stripe_service, "_price_to_plan", lambda price: "pro_monthly")
    env.stripe_subs["sub_w"] = {
        "id": "sub_w", "customer": "cus_w", "status": "active", "cancel_at_period_end": False,
        "items": {"object": "list", "data": [
            {"id": "si_1", "price": {"id": "price_m"}, "current_period_end": 1800000000}]},
    }
    session = stripe.checkout.Session.construct_from(
        {"id": "cs_1", "customer": "cus_w", "subscription": "sub_w", "mode": "subscription"},
        "sk_test_dummy")
    env.run(lambda db: stripe_service.handle_checkout_completed(_event("checkout.session.completed", session), db))
    row = _sub_row(env, uid)
    assert row.stripe_subscription_id == "sub_w" and row.plan == "pro_monthly" and row.status == "active"
    end = row.current_period_end if row.current_period_end.tzinfo else row.current_period_end.replace(tzinfo=timezone.utc)
    assert end == datetime.fromtimestamp(1800000000, tz=timezone.utc)
    assert env.calls["cancel"] == []


def test_subscription_updated_on_current_stripe_objects(env, monkeypatch):
    uid = _website_user(env)
    monkeypatch.setattr(stripe_service, "_price_to_plan", lambda price: "pro_weekly")

    async def link(db):
        await db.execute(update(Subscription).where(Subscription.user_id == uid).values(
            stripe_subscription_id="sub_w"))
        await db.commit()
    env.run(link)
    obj = stripe.Subscription.construct_from({
        "id": "sub_w", "customer": "cus_w", "status": "past_due", "trial_end": 1790000000,
        "items": {"object": "list", "data": [
            {"id": "si_1", "price": {"id": "price_w"}, "current_period_end": 1800000000}]},
    }, "sk_test_dummy")
    env.run(lambda db: stripe_service.handle_subscription_updated(_event("customer.subscription.updated", obj), db))
    row = _sub_row(env, uid)
    assert row.plan == "pro_weekly" and row.status == "past_due" and row.trial_ends_at is not None


def _deleted_customer(env):
    """A paying user who ran DELETE /me; cus_1 is now a deleted account's customer."""
    _seed_paying_user(env)
    assert env.client.delete("/me", headers=auth()).status_code == 202

    async def audit(db):
        return (await db.execute(select(AccountDeletion))).scalar_one()
    row = env.run(audit)
    assert row.stripe_customer_hash == hash_sub("cus_1") and row.sub_hash == hash_sub("sub-a")


def test_checkout_after_deletion_cancels_immediately(env):
    _deleted_customer(env)
    env.stripe_subs["sub_late"] = {"id": "sub_late", "customer": "cus_1", "status": "active",
                                   "cancel_at_period_end": False}
    session = stripe.checkout.Session.construct_from(
        {"id": "cs_2", "customer": "cus_1", "subscription": "sub_late", "mode": "subscription"},
        "sk_test_dummy")
    env.run(lambda db: stripe_service.handle_checkout_completed(_event("checkout.session.completed", session), db))
    assert env.calls["cancel"] == ["sub_late"]
    assert _user(env, "sub-a") is None  # no row is re-created


def test_subscription_update_after_deletion(env):
    _deleted_customer(env)

    def update_event(status, cancelling):
        return _event("customer.subscription.updated", stripe.Subscription.construct_from({
            "id": "sub_active_1", "customer": "cus_1", "status": status,
            "cancel_at_period_end": cancelling,
            "items": {"object": "list", "data": [{"id": "si_1", "price": {"id": "p"}}]},
        }, "sk_test_dummy"))

    # The event our own cancel_at_period_end fires: left to end at period end.
    env.run(lambda db: stripe_service.handle_subscription_updated(update_event("active", True), db))
    assert env.calls["cancel"] == []
    # Already over: nothing to do.
    env.run(lambda db: stripe_service.handle_subscription_updated(update_event("canceled", False), db))
    assert env.calls["cancel"] == []
    # Billing again and not set to end (reactivated): cancelled now.
    env.run(lambda db: stripe_service.handle_subscription_updated(update_event("active", False), db))
    assert env.calls["cancel"] == ["sub_active_1"]


def test_webhook_for_an_unknown_customer_does_nothing(env):
    session = stripe.checkout.Session.construct_from(
        {"id": "cs_3", "customer": "cus_nobody", "subscription": "sub_x", "mode": "subscription"},
        "sk_test_dummy")
    env.run(lambda db: stripe_service.handle_checkout_completed(_event("checkout.session.completed", session), db))
    assert env.calls["cancel"] == [] and env.calls["retrieve"] == []


# --- webhook retries for a deleted account ---------------------------------


def _late_checkout_event():
    session = stripe.checkout.Session.construct_from(
        {"id": "cs_9", "customer": "cus_1", "subscription": "sub_late", "mode": "subscription"},
        "sk_test_dummy")
    return _event("checkout.session.completed", session)


def _late_sub(env, status="active"):
    env.stripe_subs["sub_late"] = {"id": "sub_late", "customer": "cus_1", "status": status,
                                   "cancel_at_period_end": False}


def _post_webhook(env, monkeypatch, event):
    monkeypatch.setattr(stripe_service, "verify_webhook_signature", lambda payload, signature: event)
    return env.client.post("/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "t=1,v1=x"})


@pytest.mark.parametrize("error", [
    stripe.APIConnectionError("network down"),
    stripe.InvalidRequestError("Subscription is locked", "id", code="resource_locked"),
])
def test_failed_cancel_for_deleted_account_answers_5xx_so_stripe_retries(env, monkeypatch, error):
    _deleted_customer(env)
    _late_sub(env)

    def refuse(sid, **kw):
        env.calls["cancel"].append(sid)
        raise error
    monkeypatch.setattr(stripe.Subscription, "cancel", refuse)
    r = _post_webhook(env, monkeypatch, _late_checkout_event())
    assert r.status_code == 503
    assert env.calls["cancel"] == ["sub_late"]


def test_cancel_of_an_already_gone_subscription_answers_200(env, monkeypatch):
    """Gone between our read and the cancel (a race): still done."""
    _deleted_customer(env)
    _late_sub(env)

    def gone(sid, **kw):
        env.calls["cancel"].append(sid)
        raise stripe.InvalidRequestError("No such subscription", "id", code="resource_missing")
    monkeypatch.setattr(stripe.Subscription, "cancel", gone)
    r = _post_webhook(env, monkeypatch, _late_checkout_event())
    assert r.status_code == 200 and r.json() == {"received": "true"}
    assert env.calls["cancel"] == ["sub_late"]


def test_subscription_unknown_to_stripe_answers_200_without_cancel(env, monkeypatch):
    _deleted_customer(env)  # sub_late was never created in the fake Stripe
    r = _post_webhook(env, monkeypatch, _late_checkout_event())
    assert r.status_code == 200
    assert env.calls["retrieve"][-1] == "sub_late" and env.calls["cancel"] == []


@pytest.mark.parametrize("status", ["canceled", "incomplete_expired"])
def test_already_ended_subscription_is_not_cancelled_again(env, monkeypatch, status):
    _deleted_customer(env)
    _late_sub(env, status=status)
    r = _post_webhook(env, monkeypatch, _late_checkout_event())
    assert r.status_code == 200 and env.calls["cancel"] == []


def test_lost_cancel_reply_then_retry_answers_200(env, monkeypatch):
    """The cancel reaches Stripe but the reply is lost: 503 once, then the
    retried event reads "canceled" and answers 200 without cancelling again."""
    _deleted_customer(env)
    _late_sub(env)

    def cancel_then_drop(sid, **kw):
        env.calls["cancel"].append(sid)
        env.stripe_subs[sid]["status"] = "canceled"  # Stripe did it
        raise stripe.APIConnectionError("connection reset")
    monkeypatch.setattr(stripe.Subscription, "cancel", cancel_then_drop)
    first = _post_webhook(env, monkeypatch, _late_checkout_event())
    retry = _post_webhook(env, monkeypatch, _late_checkout_event())
    assert (first.status_code, retry.status_code) == (503, 200)
    assert env.calls["cancel"] == ["sub_late"]


def test_failed_read_before_cancel_answers_5xx(env, monkeypatch):
    _deleted_customer(env)
    _late_sub(env)

    def down(sid, **kw):
        raise stripe.APIConnectionError("network down")
    monkeypatch.setattr(stripe.Subscription, "retrieve", down)
    r = _post_webhook(env, monkeypatch, _late_checkout_event())
    assert r.status_code == 503 and env.calls["cancel"] == []


def test_late_cancel_that_works_answers_200(env, monkeypatch):
    _deleted_customer(env)
    env.stripe_subs["sub_late"] = {"id": "sub_late", "customer": "cus_1", "status": "active",
                                   "cancel_at_period_end": False}
    r = _post_webhook(env, monkeypatch, _late_checkout_event())
    assert r.status_code == 200 and env.calls["cancel"] == ["sub_late"]


def test_other_webhook_errors_still_answer_200(env, monkeypatch):
    """Unchanged behaviour for every other failure: logged, 200, no retry."""
    async def boom(event, db):
        raise RuntimeError("unrelated bug")
    monkeypatch.setattr(stripe_service, "handle_checkout_completed", boom)
    r = _post_webhook(env, monkeypatch, _late_checkout_event())
    assert r.status_code == 200


# --- migration 009 on a database that ran the earlier 001 --------------------


@pytest.mark.skipif(not PG_URL, reason="TEST_PG_URL not set")
def test_migration_009_adds_the_column_to_an_old_account_deletions_table():
    async def go():
        engine = create_async_engine(PG_URL, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(_DROP))
                await conn.run_sync(Base.metadata.create_all, tables=[User.__table__])
                # account_deletions exactly as the earlier 001 created it
                await conn.execute(text(
                    "CREATE TABLE account_deletions (id BIGSERIAL PRIMARY KEY, "
                    "sub_hash VARCHAR(64) NOT NULL, deleted_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                    "CONSTRAINT account_deletions_sub_hash_key UNIQUE (sub_hash))"))
                raw = (await conn.get_raw_connection()).driver_connection
                sql = (MIGRATIONS / "009_push_devices_and_account_deletions.sql").read_text()
                sql = sql.replace("BEGIN;", "").replace("COMMIT;", "")
                await raw.execute(sql)
                await raw.execute(sql)  # idempotent
                cols = (await conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'account_deletions'"))).scalars().all()
                idx = (await conn.execute(text(
                    "SELECT indexname FROM pg_indexes WHERE tablename = 'account_deletions'"))).scalars().all()
            return set(cols), set(idx)
        finally:
            async with engine.begin() as conn:
                await conn.execute(text(_DROP))
            await engine.dispose()

    cols, idx = asyncio.run(go())
    assert "stripe_customer_hash" in cols
    assert "ix_account_deletions_stripe_customer_hash" in idx


# --- first login fails closed -------------------------------------------------


def test_first_login_refused_by_the_schema_answers_503_not_500(env, monkeypatch):
    """The 2026-11-16 smoke-test failure: the subscription insert broke a
    CHECK constraint and the request ended in a raw 500."""
    import app.dependencies as deps

    monkeypatch.setattr(deps, "BETA_DEFAULT", "not_a_plan")  # the model's CHECK refuses it
    r = env.client.post("/devices", json=reg(), headers=auth("sub-new"))
    assert r.status_code == 503
    assert r.json()["detail"] == "Your account could not be set up right now. Please try again later."
    assert _user(env, "sub-new") is None and _devices(env) == {}  # nothing half-created


def test_first_login_with_an_email_already_on_another_account_answers_503(env):
    async def seed(db):
        db.add(User(cognito_id="sub-old", email="sub-new@example.com"))
        await db.commit()
    env.run(seed)
    r = env.client.post("/devices", json=reg(), headers=auth("sub-new"))
    assert r.status_code == 503
    assert _user(env, "sub-new") is None


def test_first_login_writes_the_beta_default_plan(env):
    env.client.post("/devices", json=reg(), headers=auth("sub-new"))

    async def plan(db):
        user = (await db.execute(select(User).where(User.cognito_id == "sub-new"))).scalar_one()
        return (await db.execute(select(Subscription).where(Subscription.user_id == user.id))).scalar_one().plan
    assert env.run(plan) == "premium"
