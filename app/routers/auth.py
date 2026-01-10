"""Authentication endpoints."""

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.dependencies import get_current_user_with_db
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["Auth"])


class SubscriptionInfo(BaseModel):
    """Subscription information in user response."""
    
    plan: str
    status: str
    is_active: bool
    has_pro_access: bool
    has_elite_access: bool
    trial_ends_at: Optional[datetime] = None
    current_period_end: Optional[datetime] = None


class UserResponse(BaseModel):
    """Response model for /auth/me endpoint."""
    
    id: str
    email: str
    role: str
    created_at: datetime
    subscription: Optional[SubscriptionInfo] = None


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    user: User = Depends(get_current_user_with_db),
) -> Dict[str, Any]:
    """
    Get current user information.
    
    Returns the authenticated user's profile and subscription status.
    
    Requires authentication.
    
    Returns:
        UserResponse with user profile and subscription info.
    """
    response: Dict[str, Any] = {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "created_at": user.created_at,
    }
    
    if user.subscription:
        response["subscription"] = {
            "plan": user.subscription.plan,
            "status": user.subscription.status,
            "is_active": user.subscription.is_active,
            "has_pro_access": user.subscription.has_pro_access,
            "has_elite_access": user.subscription.has_elite_access,
            "trial_ends_at": user.subscription.trial_ends_at,
            "current_period_end": user.subscription.current_period_end,
        }
    
    return response
