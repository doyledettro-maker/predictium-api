"""
FastAPI dependencies for authentication and authorization.
"""

import logging
from typing import Dict, Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.subscription import Subscription
from app.models.user import User
from app.services.cognito import cognito_service

logger = logging.getLogger(__name__)


async def get_current_user(
    authorization: str = Header(..., description="Bearer token"),
) -> Dict[str, str]:
    """
    Validate JWT token and return user info.
    
    Extracts the Bearer token from the Authorization header,
    validates it with Cognito, and returns user claims.
    
    Args:
        authorization: Authorization header value (Bearer <token>).
        
    Returns:
        Dict with 'sub' (Cognito user ID) and 'email' keys.
        
    Raises:
        HTTPException(401): If token is missing, invalid, or expired.
    """
    # Extract Bearer token
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = authorization[7:]  # Remove "Bearer " prefix
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        user_info = await cognito_service.get_user_info(token)
        return user_info
    except ValueError as e:
        logger.warning(f"Token validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user_with_db(
    user_info: Dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Get the current authenticated user from the database.
    
    Creates the user record if it doesn't exist (first login).
    
    Args:
        user_info: Validated user info from JWT.
        db: Database session.
        
    Returns:
        User model instance.
        
    Raises:
        HTTPException(401): If authentication fails.
    """
    cognito_id = user_info["sub"]
    email = user_info["email"]
    
    # Try to find existing user
    result = await db.execute(
        select(User).where(User.cognito_id == cognito_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        # Create new user on first login
        user = User(
            cognito_id=cognito_id,
            email=email,
            role="subscriber",
        )
        db.add(user)
        await db.flush()  # Generate user.id before creating subscription
        
        # Create default subscription
        # BETA: Auto-grant elite access to all new users
        # TODO: Change back to plan="free" after beta ends
        subscription = Subscription(
            user_id=user.id,
            plan="elite",
            status="active",
        )
        db.add(subscription)
        
        await db.commit()
        await db.refresh(user)
        logger.info(f"Created new user: {email}")
    
    return user


async def get_subscription(
    user: User = Depends(get_current_user_with_db),
    db: AsyncSession = Depends(get_db),
) -> Subscription:
    """
    Get the current user's subscription.
    
    Args:
        user: Current authenticated user.
        db: Database session.
        
    Returns:
        Subscription model instance.
        
    Raises:
        HTTPException(404): If subscription not found.
    """
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    subscription = result.scalar_one_or_none()
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )
    
    return subscription


def require_plan(required_plans: list[str]):
    """
    Dependency factory that requires specific subscription plans.
    
    Args:
        required_plans: List of plan names that are allowed.
        
    Returns:
        Dependency function that validates the user's plan.
    """
    async def check_plan(
        subscription: Subscription = Depends(get_subscription),
    ) -> Subscription:
        if not subscription.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Subscription is not active",
            )
        
        if subscription.plan not in required_plans:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This feature requires one of these plans: {', '.join(required_plans)}",
            )
        
        return subscription
    
    return check_plan


# Convenience dependencies for common plan requirements
require_pro_or_elite = require_plan(["pro", "elite"])
require_elite = require_plan(["elite"])
require_any_active = require_plan(["free", "pro", "elite"])
