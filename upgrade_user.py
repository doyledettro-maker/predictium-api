#!/usr/bin/env python3
"""
Upgrade a single user to premium by email address.
Usage: python upgrade_user.py user@example.com
"""

import asyncio
import sys
import asyncpg

DB_HOST = "predictium-db.cdwgcgwm2ugb.us-east-2.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "predictium"
DB_USER = "postgres"
DB_PASSWORD = ":jpN:mz#ir48nl[Lewo|_4$hi9C_"


async def upgrade_user(email: str):
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )

    # Find user
    user = await conn.fetchrow(
        "SELECT id, email FROM users WHERE LOWER(email) = LOWER($1)", email
    )
    if not user:
        print(f"❌ No user found with email: {email}")
        print("   (They need to sign up at predictium.ai first)")
        await conn.close()
        return False

    # Check existing subscription
    sub = await conn.fetchrow(
        "SELECT plan, status FROM subscriptions WHERE user_id = $1", user["id"]
    )

    if sub and sub["plan"] == "premium" and sub["status"] == "active":
        print(f"✅ {email} already has premium access")
        await conn.close()
        return True

    if sub:
        # Update existing
        await conn.execute(
            "UPDATE subscriptions SET plan = 'premium', status = 'active', updated_at = NOW() WHERE user_id = $1",
            user["id"],
        )
        print(f"✅ Upgraded {email} from {sub['plan']}/{sub['status']} → premium/active")
    else:
        # Create new
        await conn.execute(
            "INSERT INTO subscriptions (user_id, plan, status, created_at, updated_at) VALUES ($1, 'premium', 'active', NOW(), NOW())",
            user["id"],
        )
        print(f"✅ Created premium subscription for {email}")

    await conn.close()
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python upgrade_user.py <email>")
        sys.exit(1)
    asyncio.run(upgrade_user(sys.argv[1]))
