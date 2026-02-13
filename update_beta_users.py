#!/usr/bin/env python3
"""Update beta users to premium plan."""

import asyncio
import os
from sqlalchemy import select
from sqlalchemy.orm import selectinload

# Set environment variable for database URL
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres::jpN:mz#ir48nl[Lewo|_4$hi9C_@predictium-db.cdwgcgwm2ugb.us-east-2.rds.amazonaws.com:5432/predictium"

from app.db.database import async_session_maker
from app.models.user import User
from app.models.subscription import Subscription

async def update_beta_users():
    """Update all beta users to premium plan."""
    async with async_session_maker() as session:
        # Get all users with their subscriptions
        result = await session.execute(
            select(User)
            .options(selectinload(User.subscription))
        )
        users = result.scalars().all()
        
        print("=" * 80)
        print("Updating Beta Users to Premium Plan")
        print("=" * 80)
        
        updated_count = 0
        created_count = 0
        
        for user in users:
            if user.subscription:
                if user.subscription.plan != "premium":
                    print(f"Updating {user.email}: {user.subscription.plan} -> premium")
                    user.subscription.plan = "premium"
                    user.subscription.status = "active"
                    updated_count += 1
                else:
                    print(f"Skipping {user.email}: already premium")
            else:
                print(f"Creating premium subscription for {user.email}")
                subscription = Subscription(
                    user_id=user.id,
                    plan="premium",
                    status="active",
                )
                session.add(subscription)
                created_count += 1
        
        await session.commit()
        
        print("=" * 80)
        print(f"Updated {updated_count} subscriptions to premium")
        print(f"Created {created_count} new premium subscriptions")
        print("=" * 80)
        
        # Verify
        result = await session.execute(
            select(User)
            .options(selectinload(User.subscription))
        )
        users = result.scalars().all()
        
        print("\nVerification:")
        print(f"{'Email':<40} {'Plan':<10} {'Status':<12} {'Premium':<8}")
        print("-" * 80)
        for user in users:
            if user.subscription:
                sub = user.subscription
                print(f"{user.email:<40} {sub.plan:<10} {sub.status:<12} {str(sub.has_premium_access):<8}")

if __name__ == "__main__":
    asyncio.run(update_beta_users())
