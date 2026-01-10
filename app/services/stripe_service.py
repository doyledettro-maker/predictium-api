"""
Stripe service for customer and subscription management.

Handles:
- Creating Stripe customers
- Creating checkout sessions
- Creating customer portal sessions
- Processing webhooks
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.subscription import Subscription
from app.models.user import User

logger = logging.getLogger(__name__)


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
        """
        Create a Stripe customer.
        
        Args:
            email: Customer's email address.
            user_id: Internal user ID for metadata.
            
        Returns:
            Stripe customer ID.
        """
        try:
            customer = stripe.Customer.create(
                email=email,
                metadata={"user_id": user_id},
            )
            logger.info(f"Created Stripe customer: {customer.id}")
            return customer.id
        except stripe.StripeError as e:
            logger.error(f"Failed to create Stripe customer: {e}")
            raise

    async def create_checkout_session(
        self,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        trial_days: Optional[int] = None,
    ) -> str:
        """
        Create a Stripe checkout session.
        
        Args:
            customer_id: Stripe customer ID.
            price_id: Stripe price ID for the subscription.
            success_url: URL to redirect to on success.
            cancel_url: URL to redirect to on cancellation.
            trial_days: Optional number of trial days.
            
        Returns:
            Checkout session URL.
        """
        try:
            session_params: Dict[str, Any] = {
                "customer": customer_id,
                "payment_method_types": ["card"],
                "line_items": [
                    {
                        "price": price_id,
                        "quantity": 1,
                    },
                ],
                "mode": "subscription",
                "success_url": success_url,
                "cancel_url": cancel_url,
            }
            
            if trial_days and trial_days > 0:
                session_params["subscription_data"] = {
                    "trial_period_days": trial_days,
                }
            
            session = stripe.checkout.Session.create(**session_params)
            logger.info(f"Created checkout session: {session.id}")
            return session.url
        except stripe.StripeError as e:
            logger.error(f"Failed to create checkout session: {e}")
            raise

    async def create_portal_session(
        self,
        customer_id: str,
        return_url: str,
    ) -> str:
        """
        Create a Stripe customer portal session.
        
        Args:
            customer_id: Stripe customer ID.
            return_url: URL to redirect to when leaving the portal.
            
        Returns:
            Portal session URL.
        """
        try:
            session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url,
            )
            logger.info(f"Created portal session for customer: {customer_id}")
            return session.url
        except stripe.StripeError as e:
            logger.error(f"Failed to create portal session: {e}")
            raise

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> Dict[str, Any]:
        """
        Verify Stripe webhook signature and return the event.
        
        Args:
            payload: Raw webhook payload bytes.
            signature: Stripe-Signature header value.
            
        Returns:
            Verified Stripe event object.
            
        Raises:
            ValueError: If signature verification fails.
        """
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature,
                self.settings.stripe_webhook_secret,
            )
            return event
        except stripe.SignatureVerificationError as e:
            logger.warning(f"Webhook signature verification failed: {e}")
            raise ValueError("Invalid webhook signature")

    async def handle_checkout_completed(
        self,
        event: Dict[str, Any],
        db: AsyncSession,
    ) -> None:
        """
        Handle checkout.session.completed webhook event.
        
        Updates the user's subscription with Stripe IDs and status.
        
        Args:
            event: Stripe webhook event data.
            db: Database session.
        """
        session = event["data"]["object"]
        customer_id = session["customer"]
        subscription_id = session.get("subscription")
        
        if not subscription_id:
            logger.warning("Checkout completed without subscription ID")
            return
        
        # Fetch the subscription to get plan details
        stripe_sub = stripe.Subscription.retrieve(subscription_id)
        
        # Determine plan from price ID
        price_id = stripe_sub["items"]["data"][0]["price"]["id"]
        plan = self._price_to_plan(price_id)
        
        # Find user by Stripe customer ID
        result = await db.execute(
            select(Subscription).where(
                Subscription.stripe_customer_id == customer_id
            )
        )
        subscription = result.scalar_one_or_none()
        
        if subscription:
            subscription.stripe_subscription_id = subscription_id
            subscription.plan = plan
            subscription.status = self._stripe_status_to_internal(stripe_sub["status"])
            subscription.current_period_end = datetime.fromtimestamp(
                stripe_sub["current_period_end"],
                tz=timezone.utc,
            )
            if stripe_sub.get("trial_end"):
                subscription.trial_ends_at = datetime.fromtimestamp(
                    stripe_sub["trial_end"],
                    tz=timezone.utc,
                )
            
            await db.commit()
            logger.info(f"Updated subscription for customer: {customer_id}")
        else:
            logger.warning(f"No subscription found for customer: {customer_id}")

    async def handle_subscription_updated(
        self,
        event: Dict[str, Any],
        db: AsyncSession,
    ) -> None:
        """
        Handle customer.subscription.updated webhook event.
        
        Syncs subscription status and period dates.
        
        Args:
            event: Stripe webhook event data.
            db: Database session.
        """
        stripe_sub = event["data"]["object"]
        subscription_id = stripe_sub["id"]
        
        result = await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == subscription_id
            )
        )
        subscription = result.scalar_one_or_none()
        
        if subscription:
            price_id = stripe_sub["items"]["data"][0]["price"]["id"]
            subscription.plan = self._price_to_plan(price_id)
            subscription.status = self._stripe_status_to_internal(stripe_sub["status"])
            subscription.current_period_end = datetime.fromtimestamp(
                stripe_sub["current_period_end"],
                tz=timezone.utc,
            )
            if stripe_sub.get("trial_end"):
                subscription.trial_ends_at = datetime.fromtimestamp(
                    stripe_sub["trial_end"],
                    tz=timezone.utc,
                )
            
            await db.commit()
            logger.info(f"Updated subscription: {subscription_id}")
        else:
            logger.warning(f"No subscription found for Stripe sub: {subscription_id}")

    async def handle_subscription_deleted(
        self,
        event: Dict[str, Any],
        db: AsyncSession,
    ) -> None:
        """
        Handle customer.subscription.deleted webhook event.
        
        Marks the subscription as canceled.
        
        Args:
            event: Stripe webhook event data.
            db: Database session.
        """
        stripe_sub = event["data"]["object"]
        subscription_id = stripe_sub["id"]
        
        result = await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == subscription_id
            )
        )
        subscription = result.scalar_one_or_none()
        
        if subscription:
            subscription.status = "canceled"
            subscription.plan = "free"
            
            await db.commit()
            logger.info(f"Canceled subscription: {subscription_id}")
        else:
            logger.warning(f"No subscription found for Stripe sub: {subscription_id}")

    def _price_to_plan(self, price_id: str) -> str:
        """Map Stripe price ID to internal plan name."""
        if price_id == self.settings.stripe_pro_price_id:
            return "pro"
        elif price_id == self.settings.stripe_elite_price_id:
            return "elite"
        return "free"

    def _stripe_status_to_internal(self, stripe_status: str) -> str:
        """Map Stripe subscription status to internal status."""
        status_map = {
            "trialing": "trialing",
            "active": "active",
            "past_due": "past_due",
            "canceled": "canceled",
            "unpaid": "past_due",
            "incomplete": "trialing",
            "incomplete_expired": "expired",
        }
        return status_map.get(stripe_status, "expired")


# Global service instance
stripe_service = StripeService()
