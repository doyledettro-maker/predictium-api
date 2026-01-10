"""Billing and subscription endpoints."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.database import get_db
from app.dependencies import get_current_user_with_db, get_subscription
from app.models.coupon import Coupon, CouponRedemption
from app.models.subscription import Subscription
from app.models.user import User
from app.services.stripe_service import stripe_service

router = APIRouter(prefix="/billing", tags=["Billing"])
settings = get_settings()


class SubscriptionResponse(BaseModel):
    """Response model for subscription info."""
    
    plan: str
    status: str
    is_active: bool
    has_pro_access: bool
    has_elite_access: bool
    trial_ends_at: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    stripe_customer_id: Optional[str] = None


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
    """Request model for creating a checkout session."""
    
    plan: str  # "pro" or "elite"
    success_url: str
    cancel_url: str


class CheckoutSessionResponse(BaseModel):
    """Response model for checkout session."""
    
    checkout_url: str


class PortalSessionRequest(BaseModel):
    """Request model for creating a portal session."""
    
    return_url: str


class PortalSessionResponse(BaseModel):
    """Response model for portal session."""
    
    portal_url: str


@router.get("/subscription", response_model=SubscriptionResponse)
async def get_user_subscription(
    subscription: Subscription = Depends(get_subscription),
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
        "has_pro_access": subscription.has_pro_access,
        "has_elite_access": subscription.has_elite_access,
        "trial_ends_at": subscription.trial_ends_at,
        "current_period_end": subscription.current_period_end,
        "stripe_customer_id": subscription.stripe_customer_id,
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
    Create a Stripe checkout session for subscription purchase.
    
    Creates or retrieves the Stripe customer for the user,
    then creates a checkout session for the selected plan.
    
    Requires authentication.
    
    Args:
        request: CheckoutSessionRequest with plan and URLs.
        
    Returns:
        CheckoutSessionResponse with checkout URL.
        
    Raises:
        HTTPException(400): If plan is invalid.
    """
    # Validate plan
    if request.plan not in ("pro", "elite"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid plan. Must be 'pro' or 'elite'.",
        )
    
    # Get price ID for plan
    price_id = (
        settings.stripe_pro_price_id
        if request.plan == "pro"
        else settings.stripe_elite_price_id
    )
    
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stripe price ID not configured for this plan",
        )
    
    # Get or create subscription record
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    subscription = result.scalar_one_or_none()
    
    if not subscription:
        subscription = Subscription(user_id=user.id)
        db.add(subscription)
        await db.flush()
    
    # Get or create Stripe customer
    if not subscription.stripe_customer_id:
        customer_id = await stripe_service.create_customer(
            email=user.email,
            user_id=str(user.id),
        )
        subscription.stripe_customer_id = customer_id
        await db.commit()
    else:
        customer_id = subscription.stripe_customer_id
    
    # Create checkout session
    checkout_url = await stripe_service.create_checkout_session(
        customer_id=customer_id,
        price_id=price_id,
        success_url=request.success_url,
        cancel_url=request.cancel_url,
    )
    
    return {"checkout_url": checkout_url}


@router.post("/create-portal-session", response_model=PortalSessionResponse)
async def create_portal_session(
    request: PortalSessionRequest,
    subscription: Subscription = Depends(get_subscription),
) -> Dict[str, str]:
    """
    Create a Stripe customer portal session.
    
    Allows the user to manage their subscription,
    update payment methods, and view invoices.
    
    Requires authentication and an existing Stripe customer.
    
    Args:
        request: PortalSessionRequest with return URL.
        
    Returns:
        PortalSessionResponse with portal URL.
        
    Raises:
        HTTPException(400): If user has no Stripe customer ID.
    """
    if not subscription.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Stripe customer found. Please subscribe first.",
        )
    
    portal_url = await stripe_service.create_portal_session(
        customer_id=subscription.stripe_customer_id,
        return_url=request.return_url,
    )
    
    return {"portal_url": portal_url}
