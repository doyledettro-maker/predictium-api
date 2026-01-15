#!/usr/bin/env python3
"""Check subscription statuses for all users."""

import asyncio
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.database import async_session_maker
from app.models.user import User
from app.models.subscription import Subscription

async def check_subscriptions():
    """Check subscription statuses."""
    async with async_session_maker() as session:
        # Get all users with their subscriptions
        result = await session.execute(
            select(User)
            .options(selectinload(User.subscription))
        )
        users = result.scalars().all()
        
        print("=" * 80)
        print("User Subscription Status")
        print("=" * 80)
        print(f"{'Email':<40} {'Plan':<10} {'Status':<12} {'Has Pro':<8} {'Has Elite':<8}")
        print("-" * 80)
        
        for user in users:
            if user.subscription:
                sub = user.subscription
                has_pro = sub.has_pro_access
                has_elite = sub.has_elite_access
                print(f"{user.email:<40} {sub.plan:<10} {sub.status:<12} {str(has_pro):<8} {str(has_elite):<8}")
            else:
                print(f"{user.email:<40} {'None':<10} {'No Sub':<12} {'False':<8} {'False':<8}")
        
        print("=" * 80)
        print(f"\nTotal users: {len(users)}")
        
        # Count by plan
        pro_count = sum(1 for u in users if u.subscription and u.subscription.has_pro_access)
        elite_count = sum(1 for u in users if u.subscription and u.subscription.has_elite_access)
        free_count = len(users) - pro_count - elite_count
        
        print(f"Free tier: {free_count}")
        print(f"Pro tier: {pro_count}")
        print(f"Elite tier: {elite_count}")

if __name__ == "__main__":
    asyncio.run(check_subscriptions())
