#!/usr/bin/env python3
"""Update beta users to elite plan."""

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
    """Update all beta users to elite plan."""
    async with async_session_maker() as session:
        # Get all users with their subscriptions
        result = await session.execute(
            select(User)
            .options(selectinload(User.subscription))
        )
        users = result.scalars().all()
        
        print("=" * 80)
        print("Updating Beta Users to Elite Plan")
        print("=" * 80)
        
        updated_count = 0
        created_count = 0
        
        for user in users:
            if user.subscription:
                # Update existing subscription to elite
                if user.subscription.plan != "elite":
                    print(f"Updating {user.email}: {user.subscription.plan} -> elite")
                    user.subscription.plan = "elite"
                    user.subscription.status = "active"  # Ensure it's active
                    updated_count += 1
                else:
                    print(f"Skipping {user.email}: already elite")
            else:
                # Create elite subscription
                print(f"Creating elite subscription for {user.email}")
                subscription = Subscription(
                    user_id=user.id,
                    plan="elite",
                    status="active",
                )
                session.add(subscription)
                created_count += 1
        
        await session.commit()
        
        print("=" * 80)
        print(f"Updated {updated_count} subscriptions to elite")
        print(f"Created {created_count} new elite subscriptions")
        print("=" * 80)
        
        # Verify
        result = await session.execute(
            select(User)
            .options(selectinload(User.subscription))
        )
        users = result.scalars().all()
        
        print("\nVerification:")
        print(f"{'Email':<40} {'Plan':<10} {'Status':<12} {'Has Pro':<8} {'Has Elite':<8}")
        print("-" * 80)
        for user in users:
            if user.subscription:
                sub = user.subscription
                has_pro = sub.has_pro_access
                has_elite = sub.has_elite_access
                print(f"{user.email:<40} {sub.plan:<10} {sub.status:<12} {str(has_pro):<8} {str(has_elite):<8}")

if __name__ == "__main__":
    asyncio.run(update_beta_users())
