"""Webhook endpoints for external service integrations."""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.services.stripe_service import stripe_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/stripe")
async def handle_stripe_webhook(
    request: Request,
    stripe_signature: str = Header(..., alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, str]:
    """
    Handle Stripe webhook events.
    
    Processes subscription lifecycle events:
    - checkout.session.completed: Initial subscription setup
    - customer.subscription.updated: Plan changes, renewals
    - customer.subscription.deleted: Cancellations
    
    No authentication required (uses Stripe signature verification).
    
    Args:
        request: Raw HTTP request for payload access.
        stripe_signature: Stripe-Signature header for verification.
        
    Returns:
        Dict with received status.
        
    Raises:
        HTTPException(400): If signature verification fails.
    """
    # Get raw payload
    payload = await request.body()
    
    # Verify signature and construct event
    try:
        event = stripe_service.verify_webhook_signature(
            payload=payload,
            signature=stripe_signature,
        )
    except ValueError as e:
        logger.warning(f"Invalid webhook signature: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )
    
    event_type = event["type"]
    logger.info(f"Received Stripe webhook: {event_type}")
    
    # Handle events
    try:
        if event_type == "checkout.session.completed":
            await stripe_service.handle_checkout_completed(event, db)
        elif event_type == "customer.subscription.updated":
            await stripe_service.handle_subscription_updated(event, db)
        elif event_type == "customer.subscription.deleted":
            await stripe_service.handle_subscription_deleted(event, db)
        else:
            logger.debug(f"Unhandled webhook event type: {event_type}")
    except Exception as e:
        logger.error(f"Error processing webhook {event_type}: {e}")
        # Don't raise - return 200 to prevent Stripe retries
        # Log the error for investigation
    
    return {"received": "true"}
