"""
Temporary admin endpoint to update beta users to elite plan.
REMOVE THIS FILE AFTER RUNNING THE UPDATE.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.dependencies import get_current_user

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post("/update-beta-to-elite")
async def update_beta_to_elite(
    user_info: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    TEMPORARY: Update all beta users to elite plan.
    
    This endpoint will:
    1. Update existing subscriptions to elite/active
    2. Create elite subscriptions for users without subscriptions
    3. Return verification of all users
    
    REMOVE THIS ENDPOINT AFTER USE.
    """
    try:
        # Update existing subscriptions
        result1 = await db.execute(
            text("""
                UPDATE subscriptions
                SET plan = 'elite', status = 'active'
                WHERE plan != 'elite' OR status != 'active'
            """)
        )
        await db.commit()
        
        # Create subscriptions for users without them
        result2 = await db.execute(
            text("""
                INSERT INTO subscriptions (user_id, plan, status, created_at, updated_at)
                SELECT id, 'elite', 'active', NOW(), NOW()
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
                    CASE WHEN s.plan IN ('pro', 'elite') AND s.status IN ('trialing', 'active') 
                         THEN 'true' ELSE 'false' END as has_pro_access,
                    CASE WHEN s.plan = 'elite' AND s.status IN ('trialing', 'active') 
                         THEN 'true' ELSE 'false' END as has_elite_access
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
                "has_pro_access": row[3],
                "has_elite_access": row[4],
            })
        
        elite_count = sum(1 for u in users if u["has_elite_access"] == "true")
        pro_count = sum(1 for u in users if u["has_pro_access"] == "true" and u["has_elite_access"] != "true")
        free_count = len(users) - pro_count - elite_count
        
        return {
            "success": True,
            "message": "Beta users updated to elite plan",
            "updated": str(result1.rowcount),
            "inserted": str(result2.rowcount),
            "summary": {
                "total_users": len(users),
                "elite_tier": elite_count,
                "pro_tier": pro_count,
                "free_tier": free_count,
            },
            "users": users,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating subscriptions: {str(e)}",
        )
