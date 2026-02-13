"""
Temporary admin endpoint to update beta users to premium plan.
REMOVE THIS FILE AFTER RUNNING THE UPDATE.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.dependencies import get_current_user

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post("/update-beta-to-premium")
async def update_beta_to_premium(
    user_info: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    TEMPORARY: Update all beta users to premium plan.
    
    This endpoint will:
    1. Update existing subscriptions to premium/active
    2. Create premium subscriptions for users without subscriptions
    3. Return verification of all users
    
    REMOVE THIS ENDPOINT AFTER USE.
    """
    try:
        # Update existing subscriptions
        result1 = await db.execute(
            text("""
                UPDATE subscriptions
                SET plan = 'premium', status = 'active'
                WHERE plan != 'premium' OR status != 'active'
            """)
        )
        await db.commit()
        
        # Create subscriptions for users without them
        result2 = await db.execute(
            text("""
                INSERT INTO subscriptions (user_id, plan, status, created_at, updated_at)
                SELECT id, 'premium', 'active', NOW(), NOW()
                FROM users
                WHERE id NOT IN (SELECT user_id FROM subscriptions)
            """)
        )
        await db.commit()
        
        # Verify updates
        result3 = await db.execute(
            text("""
                SELECT 
                    u.email,
                    s.plan,
                    s.status,
                    CASE WHEN s.plan = 'premium' AND s.status IN ('trialing', 'active') 
                         THEN 'true' ELSE 'false' END as has_premium_access
                FROM users u
                LEFT JOIN subscriptions s ON u.id = s.user_id
                ORDER BY u.created_at
            """)
        )
        rows = result3.fetchall()
        
        users = []
        for row in rows:
            users.append({
                "email": row[0],
                "plan": row[1] or "None",
                "status": row[2] or "No Sub",
                "has_premium_access": row[3],
            })
        
        premium_count = sum(1 for u in users if u["has_premium_access"] == "true")
        free_count = len(users) - premium_count
        
        return {
            "success": True,
            "message": "Beta users updated to premium plan",
            "updated": str(result1.rowcount),
            "inserted": str(result2.rowcount),
            "summary": {
                "total_users": len(users),
                "premium_tier": premium_count,
                "free_tier": free_count,
            },
            "users": users,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating subscriptions: {str(e)}",
        )
