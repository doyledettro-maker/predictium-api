"""
Stripe service for customer and subscription management.

Handles:
- Creating Stripe customers
- Creating checkout sessions (catalog plans only, URLs built server-side,
  the founder coupon applied server-side)
- Creating customer portal sessions
- Processing webhooks

Webhook handling is idempotent by construction: every handler re-reads the
subscription from Stripe and writes its current state, so a replayed or
out-of-order event converges on the same row. A handler that cannot apply an
event RAISES, and the webhook route answers non-200 so Stripe retries; a
failed upgrade is never acknowledged and dropped.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing_catalog import plan_for_price
from app.config import get_settings
from app.models.subscription import Subscription

logger = logging.getLogger(__name__)


class WebhookNotApplied(Exception):
    """An event could not be applied; Stripe must retry it."""


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a key from a Stripe object or a plain dict."""
    try:
        value = obj[key]
    except (KeyError, TypeError, IndexError):
        return default
    return default if value is None else value


def _ts(value: Any) -> Optional[datetime]:
    return datetime.fromtimestamp(int(value), tz=timezone.utc) if value else None


def subscription_price_id(stripe_sub: Any) -> Optional[str]:
    items = _get(_get(stripe_sub, "items", {}), "data", [])
    if not items:
        return None
    return _get(_get(items[0], "price", {}), "id")


def subscription_period_end(stripe_sub: Any) -> Optional[datetime]:
    """current_period_end, from the subscription or (API 2025-03-31 and
    later, where it moved) from its first item."""
    end = _get(stripe_sub, "current_period_end")
    if not end:
        items = _get(_get(stripe_sub, "items", {}), "data", [])
        end = _get(items[0], "current_period_end") if items else None
    return _ts(end)


STATUS_MAP = {
    "trialing": "trialing",
    "active": "active",
    "past_due": "past_due",
    "unpaid": "past_due",
    "canceled": "canceled",
    "incomplete": "expired",
    "incomplete_expired": "expired",
    "paused": "expired",
}


class StripeService:
    """
    Service for Stripe payment integration.

    Manages customer creation, checkout sessions, portal sessions,
    and webhook processing for subscription lifecycle events.
    """

    def __init__(self):
        self.settings = get_settings()
        stripe.api_key = self.settings.stripe_secret_key

    async def create_customer(self, email: str, user_id: str) -> str:
        """Create a Stripe customer and return its id."""
        customer = stripe.Customer.create(email=email, metadata={"user_id": user_id})
        logger.info(f"Created Stripe customer: {customer.id}")
        return customer.id

    def checkout_urls(self) -> Dict[str, str]:
        """Success and cancel URLs, built from configuration, never from the
        client, so checkout can only return to our own billing page."""
        base = self.settings.frontend_url.rstrip("/")
        return {
            "success_url": f"{base}/billing?success=true&session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{base}/billing?canceled=true",
        }

    def checkout_session_params(
        self,
        *,
        customer_id: str,
        price_id: str,
        plan_id: str,
        user_id: str,
        founder: bool,
    ) -> Dict[str, Any]:
        """The Checkout Session parameters (pure, so tests pin them)."""
        params: Dict[str, Any] = {
            "customer": customer_id,
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "client_reference_id": user_id,
            "metadata": {"user_id": user_id, "plan": plan_id},
            "subscription_data": {"metadata": {"user_id": user_id, "plan": plan_id}},
            **self.checkout_urls(),
        }
        coupon = self.settings.stripe_founder_coupon_id
        if founder and coupon:
            # Stripe refuses `discounts` together with allow_promotion_codes,
            # which is also the rule: a founder's 50% never stacks.
            params["discounts"] = [{"coupon": coupon}]
        else:
            params["allow_promotion_codes"] = True
        return params

    async def create_checkout_session(self, **kwargs: Any) -> str:
        """Create a Checkout Session and return its URL."""
        session = stripe.checkout.Session.create(**self.checkout_session_params(**kwargs))
        logger.info(f"Created checkout session: {session.id} (plan={kwargs.get('plan_id')})")
        return session.url

    async def create_portal_session(self, customer_id: str) -> str:
        """Create a Customer Portal session returning to /billing."""
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{self.settings.frontend_url.rstrip('/')}/billing",
        )
        logger.info(f"Created portal session for customer: {customer_id}")
        return session.url

    def verify_webhook_signature(self, payload: bytes, signature: str) -> Dict[str, Any]:
        """Verify the Stripe signature and return the event, or raise ValueError."""
        try:
            return stripe.Webhook.construct_event(payload, signature, self.settings.stripe_webhook_secret)
        except stripe.SignatureVerificationError as e:
            logger.warning(f"Webhook signature verification failed: {e}")
            raise ValueError("Invalid webhook signature")

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def retrieve_subscription(self, subscription_id: str) -> Any:
        """Read the subscription's current state from Stripe (the source of
        truth; isolated so tests can stub it)."""
        return stripe.Subscription.retrieve(subscription_id)

    async def _row_for(
        self,
        db: AsyncSession,
        *,
        subscription_id: Optional[str],
        customer_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[Subscription]:
        if subscription_id:
            row = (
                await db.execute(select(Subscription).where(Subscription.stripe_subscription_id == subscription_id))
            ).scalar_one_or_none()
            if row:
                return row
        if customer_id:
            row = (
                await db.execute(select(Subscription).where(Subscription.stripe_customer_id == customer_id))
            ).scalar_one_or_none()
            if row:
                return row
        if user_id:
            try:
                import uuid

                uid = uuid.UUID(str(user_id))
            except ValueError:
                return None
            return (await db.execute(select(Subscription).where(Subscription.user_id == uid))).scalar_one_or_none()
        return None

    async def sync_subscription(
        self,
        db: AsyncSession,
        subscription_id: str,
        *,
        customer_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Subscription:
        """Write a Stripe subscription's current state onto the user's row."""
        stripe_sub = self.retrieve_subscription(subscription_id)
        customer_id = customer_id or _get(stripe_sub, "customer")
        user_id = user_id or _get(_get(stripe_sub, "metadata", {}), "user_id")
        row = await self._row_for(db, subscription_id=subscription_id, customer_id=customer_id, user_id=user_id)
        if row is None:
            raise WebhookNotApplied(f"no subscription row for Stripe subscription {subscription_id}")

        stripe_status = _get(stripe_sub, "status", "")
        status = STATUS_MAP.get(stripe_status, "expired")
        if status in ("trialing", "active", "past_due"):
            price_id = subscription_price_id(stripe_sub)
            plan = plan_for_price(price_id, self.settings)
            if plan is None:
                # A price we do not sell is a configuration error, never a
                # silent downgrade: raise so Stripe retries while it is fixed.
                raise WebhookNotApplied(f"unknown price id {price_id} on {subscription_id}")
            row.plan = plan
        else:
            row.plan = "free"
        row.status = status
        row.stripe_subscription_id = subscription_id
        if customer_id:
            row.stripe_customer_id = customer_id
        row.current_period_end = subscription_period_end(stripe_sub)
        row.trial_ends_at = _ts(_get(stripe_sub, "trial_end"))
        await db.commit()
        logger.info(f"Synced subscription {subscription_id}: plan={row.plan} status={row.status}")
        return row

    async def handle_event(self, event: Dict[str, Any], db: AsyncSession) -> bool:
        """Apply one webhook event. Returns False for an event type we do not
        handle; raises WebhookNotApplied (or a Stripe error) on failure."""
        event_type = _get(event, "type")
        obj = _get(_get(event, "data", {}), "object", {})

        if event_type == "checkout.session.completed":
            if _get(obj, "mode") != "subscription":
                logger.warning(f"Ignoring non-subscription checkout {_get(obj, 'id')}")
                return True
            subscription_id = _get(obj, "subscription")
            if not subscription_id:
                raise WebhookNotApplied(f"checkout {_get(obj, 'id')} completed without a subscription")
            await self.sync_subscription(
                db,
                subscription_id,
                customer_id=_get(obj, "customer"),
                user_id=_get(obj, "client_reference_id") or _get(_get(obj, "metadata", {}), "user_id"),
            )
            return True

        if event_type in (
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        ):
            await self.sync_subscription(db, _get(obj, "id"), customer_id=_get(obj, "customer"))
            return True

        if event_type in ("invoice.payment_failed", "invoice.paid"):
            subscription_id = _get(obj, "subscription") or _get(
                _get(_get(obj, "parent", {}), "subscription_details", {}), "subscription"
            )
            if subscription_id:
                await self.sync_subscription(db, subscription_id, customer_id=_get(obj, "customer"))
            return True

        logger.debug(f"Unhandled webhook event type: {event_type}")
        return False


# Global service instance
stripe_service = StripeService()
