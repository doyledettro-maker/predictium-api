"""Billing and subscription endpoints."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing_catalog import PLANS, PRODUCTS, is_founder, price_id_for
from app.config import get_settings
from app.db.database import get_db
from app.dependencies import get_current_user_with_db, get_subscription
from app.models.coupon import Coupon, CouponRedemption
from app.models.subscription import Subscription
from app.models.user import User
from app.services.stripe_service import stripe_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["Billing"])
settings = get_settings()


class SubscriptionResponse(BaseModel):
    """Response model for subscription info."""

    plan: str
    status: str
    is_active: bool
    has_premium_access: bool
    trial_ends_at: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    stripe_customer_id: Optional[str] = None
    # Added 2026-11-13 (paid launch). `entitlements` are products ("pro",
    # "tennis"), never plan names, so the frontend gates on what was bought.
    entitlements: List[str] = []
    founder: bool = False
    interval: Optional[str] = None


class EntitlementsResponse(BaseModel):
    """What the user may read right now. The frontend's claims routes call
    this with the user's own token before any signed read of paid data."""

    products: List[str]
    paid: bool
    plan: str
    status: str
    founder: bool
    current_period_end: Optional[datetime] = None


class RedeemCouponRequest(BaseModel):
    """Request model for redeeming a coupon."""
    
    code: str


class RedeemCouponResponse(BaseModel):
    """Response model for coupon redemption."""
    
    success: bool
    message: str
    plan: Optional[str] = None
    trial_ends_at: Optional[datetime] = None


class CheckoutSessionRequest(BaseModel):
    """Request model for creating a checkout session.

    Only the plan comes from the client. The price, the success and cancel
    URLs and any founder discount are decided here.
    """

    plan: str  # a key of app.billing_catalog.PLANS


class CheckoutSessionResponse(BaseModel):
    """Response model for checkout session."""

    url: str
    checkout_url: str  # same value; kept for older callers


class PortalSessionResponse(BaseModel):
    """Response model for portal session."""

    url: str
    portal_url: str  # same value; kept for older callers


ALREADY_SUBSCRIBED = "You already have a subscription. Change plans from Manage Subscription."


def _interval(plan: str) -> Optional[str]:
    return PLANS[plan].interval if plan in PLANS else None


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_user_subscription(
    subscription: Subscription = Depends(get_subscription),
    user: User = Depends(get_current_user_with_db),
) -> Dict[str, Any]:
    """
    Get the current user's subscription status.
    
    Requires authentication.
    
    Returns:
        SubscriptionResponse with current plan and status.
    """
    return {
        "plan": subscription.plan,
        "status": subscription.status,
        "is_active": subscription.is_active,
        "has_premium_access": subscription.has_premium_access,
        "trial_ends_at": subscription.trial_ends_at,
        "current_period_end": subscription.current_period_end,
        "stripe_customer_id": subscription.stripe_customer_id,
        "entitlements": sorted(subscription.entitlements),
        "founder": is_founder(user.created_at, settings),
        "interval": _interval(subscription.plan),
    }


@router.get("/entitlements", response_model=EntitlementsResponse)
async def get_entitlements(
    user: User = Depends(get_current_user_with_db),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    The paid products the current user may read right now.

    Requires authentication. A user with no subscription row has none. The
    frontend never decides access from a plan name: it asks for a product.
    """
    subscription = (
        await db.execute(select(Subscription).where(Subscription.user_id == user.id))
    ).scalar_one_or_none()
    products = sorted(subscription.entitlements) if subscription else []
    return {
        "products": [p for p in PRODUCTS if p in products],
        "paid": bool(products),
        "plan": subscription.plan if subscription else "free",
        "status": subscription.status if subscription else "none",
        "founder": is_founder(user.created_at, settings),
        "current_period_end": subscription.current_period_end if subscription else None,
    }


@router.post("/redeem-coupon", response_model=RedeemCouponResponse)
async def redeem_coupon(
    request: RedeemCouponRequest,
    user: User = Depends(get_current_user_with_db),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Validate and apply a coupon code.
    
    Coupons grant trial access to Pro or Elite plans.
    Each user can only redeem each coupon once.
    
    Requires authentication.
    
    Args:
        request: RedeemCouponRequest with coupon code.
        
    Returns:
        RedeemCouponResponse with success status and new plan details.
        
    Raises:
        HTTPException(400): If coupon is invalid or already redeemed.
    """
    code = request.code.strip().upper()
    
    # Find the coupon
    result = await db.execute(
        select(Coupon).where(Coupon.code == code)
    )
    coupon = result.scalar_one_or_none()
    
    if not coupon:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid coupon code",
        )
    
    # Check if coupon is valid
    if not coupon.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This coupon is no longer active",
        )
    
    if coupon.expires_at and datetime.now(timezone.utc) > coupon.expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This coupon has expired",
        )
    
    if coupon.max_uses and coupon.current_uses >= coupon.max_uses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This coupon has reached its maximum uses",
        )
    
    # Check if user already redeemed this coupon
    result = await db.execute(
        select(CouponRedemption).where(
            CouponRedemption.user_id == user.id,
            CouponRedemption.coupon_code == code,
        )
    )
    existing_redemption = result.scalar_one_or_none()
    
    if existing_redemption:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already redeemed this coupon",
        )
    
    # Get or create subscription
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    subscription = result.scalar_one_or_none()
    
    if not subscription:
        subscription = Subscription(user_id=user.id)
        db.add(subscription)
    elif subscription.has_live_stripe_subscription:
        # A coupon would overwrite the paying row: its entitlements would
        # vanish while Stripe kept charging.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a paid subscription, so this coupon cannot be applied.",
        )

    # Apply coupon
    trial_ends_at = datetime.now(timezone.utc) + timedelta(days=coupon.trial_days)
    subscription.plan = coupon.plan
    subscription.status = "trialing"
    subscription.trial_ends_at = trial_ends_at
    
    # Record redemption
    redemption = CouponRedemption(
        user_id=user.id,
        coupon_code=code,
    )
    db.add(redemption)
    
    # Increment coupon uses
    coupon.current_uses += 1
    
    await db.commit()
    
    return {
        "success": True,
        "message": f"Coupon applied! You now have {coupon.trial_days} days of {coupon.plan.title()} access.",
        "plan": coupon.plan,
        "trial_ends_at": trial_ends_at,
    }


@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    request: CheckoutSessionRequest,
    user: User = Depends(get_current_user_with_db),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, str]:
    """
    Create a Stripe Checkout Session for a catalog plan.

    Requires authentication. The client sends only `plan`; the price comes
    from configuration, the success and cancel URLs are built from
    `FRONTEND_URL`, and a founder's FOUNDER50 coupon is attached here.

    Raises:
        HTTPException(400): unknown plan.
        HTTPException(409): the user already holds a paid subscription
            (plan changes go through the customer portal, so nobody is
            billed twice).
        HTTPException(503): the plan's price is not configured.
    """
    if request.plan not in PLANS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid plan. Must be one of: {', '.join(PLANS)}",
        )
    price_id = price_id_for(request.plan, settings)
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This plan is not on sale yet",
        )

    subscription = (
        await db.execute(select(Subscription).where(Subscription.user_id == user.id))
    ).scalar_one_or_none()
    if subscription is None:
        subscription = Subscription(user_id=user.id, plan="free", status="active")
        db.add(subscription)
        await db.flush()
    elif subscription.has_live_stripe_subscription:
        # Keyed on a live Stripe subscription, not on access: a past_due card
        # is still billed, so a second checkout would charge twice.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_SUBSCRIBED)

    if not subscription.stripe_customer_id:
        subscription.stripe_customer_id = await stripe_service.create_customer(
            email=user.email,
            user_id=str(user.id),
        )
        await db.commit()
    elif stripe_service.live_subscription_ids(subscription.stripe_customer_id):
        # Our row can lag a webhook or have been overwritten; Stripe cannot.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ALREADY_SUBSCRIBED)

    url = await stripe_service.create_checkout_session(
        customer_id=subscription.stripe_customer_id,
        price_id=price_id,
        plan_id=request.plan,
        user_id=str(user.id),
        founder=is_founder(user.created_at, settings),
    )
    logger.info(f"Checkout created for plan={request.plan}")
    return {"url": url, "checkout_url": url}


@router.post("/create-portal-session", response_model=PortalSessionResponse)
async def create_portal_session(
    subscription: Subscription = Depends(get_subscription),
) -> Dict[str, str]:
    """
    Create a Stripe Customer Portal session (change plan, update card,
    invoices, cancel). It returns to /billing on our own site; the client
    sends no URL, and any body it sends is ignored.

    Raises:
        HTTPException(400): the user has no Stripe customer yet.
    """
    if not subscription.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Stripe customer found. Please subscribe first.",
        )
    url = await stripe_service.create_portal_session(customer_id=subscription.stripe_customer_id)
    return {"url": url, "portal_url": url}
