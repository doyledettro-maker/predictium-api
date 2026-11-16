"""Account deletion for DELETE /me (Apple 5.1.1(v)).

Contract: predictium-app docs/API_PROPOSAL.md section 5. The steps run in an
order chosen so that any failure stops the run with nothing lost that a
retry would need, and every step is safe to repeat:

1. billing: cancel the Stripe subscriptions at period end. This runs first
   because it needs the Stripe ids held on the subscription row.
2. store_entitlements: delete the RevenueCat subscriber keyed on the sub.
3. data: in one transaction, delete the push devices, analytics rows keyed
   by the sub, coupon redemptions, the subscription and user rows, and write
   the audit row (sub hash and timestamp only).
4. identity: Cognito AdminDeleteUser. This runs last, so until every other
   step has worked the person can still sign in and try again.

A step that fails raises DeletionStepFailed and the later steps do not run.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import boto3
import httpx
import stripe
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.account_deletion import AccountDeletion, hash_sub
from app.models.coupon import Coupon, CouponRedemption
from app.models.page_view import PageView
from app.models.push_device import PushDevice
from app.models.subscription import Subscription
from app.models.user import User

logger = logging.getLogger(__name__)

REVENUECAT_API = "https://api.revenuecat.com/v1"

# Stripe statuses that can still bill. "incomplete" lapses by itself and
# "canceled" / "incomplete_expired" are already over.
_BILLABLE = {"active", "trialing", "past_due", "unpaid", "paused"}


class DeletionStepFailed(Exception):
    def __init__(self, step: str):
        super().__init__(step)
        self.step = step


def _cancel_stripe_sync(customer_id: Optional[str], subscription_id: Optional[str]) -> int:
    """Sets cancel_at_period_end on every billable subscription. Returns how many changed."""
    subs: Dict[str, Any] = {}
    if customer_id:
        try:
            page = stripe.Subscription.list(customer=customer_id, status="all", limit=100)
            for s in page.auto_paging_iter():
                subs[s["id"]] = s
        except stripe.InvalidRequestError as e:
            # A customer already deleted in Stripe has nothing left to bill.
            if getattr(e, "code", None) != "resource_missing":
                raise
    if subscription_id and subscription_id not in subs:
        try:
            subs[subscription_id] = stripe.Subscription.retrieve(subscription_id)
        except stripe.InvalidRequestError as e:
            if getattr(e, "code", None) != "resource_missing":
                raise
    changed = 0
    for sid, s in subs.items():
        if s.get("status") in _BILLABLE and not s.get("cancel_at_period_end"):
            stripe.Subscription.modify(sid, cancel_at_period_end=True)
            changed += 1
    return changed


async def cancel_stripe(subscription: Optional[Subscription]) -> None:
    if subscription is None:
        return
    customer_id = subscription.stripe_customer_id
    subscription_id = subscription.stripe_subscription_id
    if not customer_id and not subscription_id:
        return
    try:
        changed = await asyncio.to_thread(_cancel_stripe_sync, customer_id, subscription_id)
    except stripe.StripeError as e:
        logger.warning("Account deletion: Stripe cancel failed (%s)", type(e).__name__)
        raise DeletionStepFailed("billing")
    logger.info("Account deletion: set %d Stripe subscription(s) to cancel at period end", changed)


async def delete_revenuecat_subscriber(sub: str, client: Optional[httpx.AsyncClient] = None) -> None:
    key = get_settings().revenuecat_secret_key
    if not key:
        logger.warning("Account deletion: REVENUECAT_SECRET_KEY not set, RevenueCat step skipped")
        return
    url = f"{REVENUECAT_API}/subscribers/{quote(sub, safe='')}"
    own = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        r = await client.delete(url, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as e:
        logger.warning("Account deletion: RevenueCat request failed (%s)", type(e).__name__)
        raise DeletionStepFailed("store_entitlements")
    finally:
        if own:
            await client.aclose()
    # 404: no subscriber for this sub, which is the end state we want.
    if r.status_code not in (200, 201, 204, 404):
        logger.warning("Account deletion: RevenueCat returned HTTP %d", r.status_code)
        raise DeletionStepFailed("store_entitlements")


async def delete_rows(db: AsyncSession, sub: str, user: Optional[User]) -> None:
    try:
        if user is not None:
            uid = user.id
            await db.execute(delete(PushDevice).where(PushDevice.user_id == uid))
            await db.execute(delete(CouponRedemption).where(CouponRedemption.user_id == uid))
            # coupons.created_by has no ON DELETE rule; keep the coupon, drop the link.
            await db.execute(update(Coupon).where(Coupon.created_by == uid).values(created_by=None))
            await db.execute(delete(Subscription).where(Subscription.user_id == uid))
            await db.execute(delete(User).where(User.id == uid))
        await db.execute(delete(PageView).where(PageView.user_id == sub))
        h = hash_sub(sub)
        seen = await db.execute(select(AccountDeletion.id).where(AccountDeletion.sub_hash == h))
        if seen.first() is None:
            db.add(AccountDeletion(sub_hash=h))
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logger.warning("Account deletion: database step failed (%s)", type(e).__name__)
        raise DeletionStepFailed("data")


def _cognito_client():
    return boto3.client("cognito-idp", region_name=get_settings().cognito_region)


def _admin_delete_sync(username: str) -> None:
    try:
        _cognito_client().admin_delete_user(
            UserPoolId=get_settings().cognito_user_pool_id, Username=username
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "UserNotFoundException":
            return  # already gone
        raise


async def delete_cognito_user(username: str) -> None:
    try:
        await asyncio.to_thread(_admin_delete_sync, username)
    except (ClientError, BotoCoreError) as e:
        logger.warning("Account deletion: Cognito AdminDeleteUser failed (%s)", type(e).__name__)
        raise DeletionStepFailed("identity")


def cognito_username(claims: Dict[str, Any]) -> str:
    # ID tokens carry cognito:username, access tokens carry username. For a
    # local user the sub is also accepted; a federated user needs the username.
    return claims.get("cognito:username") or claims.get("username") or claims["sub"]


async def delete_account(db: AsyncSession, claims: Dict[str, Any]) -> List[str]:
    """Runs every step. Returns the step names that ran. Raises DeletionStepFailed."""
    sub = claims["sub"]
    result = await db.execute(select(User).where(User.cognito_id == sub))
    user = result.scalar_one_or_none()
    subscription = None
    if user is not None:
        r = await db.execute(select(Subscription).where(Subscription.user_id == user.id))
        subscription = r.scalar_one_or_none()

    await cancel_stripe(subscription)
    await delete_revenuecat_subscriber(sub)
    await delete_rows(db, sub, user)
    await delete_cognito_user(cognito_username(claims))
    logger.info("Account deletion finished for sub hash %s", hash_sub(sub)[:12])
    return ["billing", "store_entitlements", "data", "identity"]
