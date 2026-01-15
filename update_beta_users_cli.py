#!/usr/bin/env python3
"""
Update all beta users to elite plan via direct database connection.
Can be run locally or in CloudShell.
"""

import asyncio
import asyncpg
import os

# Database connection details
DB_HOST = "predictium-db.cdwgcgwm2ugb.us-east-2.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "predictium"
DB_USER = "postgres"
DB_PASSWORD = ":jpN:mz#ir48nl[Lewo|_4$hi9C_"

async def update_beta_users():
    """Update all beta users to elite plan."""
    print("=" * 80)
    print("Updating Beta Users to Elite Plan")
    print("=" * 80)
    print(f"Connecting to: {DB_HOST}:{DB_PORT}/{DB_NAME}")
    print()
    
    try:
        # Connect to database
        conn = await asyncpg.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )
        
        print("✓ Connected to database")
        print()
        
        # Update existing subscriptions to elite
        print("Updating existing subscriptions to elite...")
        result = await conn.execute("""
            UPDATE subscriptions
            SET plan = 'elite',
                status = 'active'
            WHERE plan != 'elite' OR status != 'active'
        """)
        print(f"✓ Updated subscriptions: {result}")
        print()
        
        # Create subscriptions for users who don't have one
        print("Creating elite subscriptions for users without subscriptions...")
        result = await conn.execute("""
            INSERT INTO subscriptions (user_id, plan, status, created_at, updated_at)
            SELECT id, 'elite', 'active', NOW(), NOW()
            FROM users
            WHERE id NOT IN (SELECT user_id FROM subscriptions)
        """)
        print(f"✓ Created subscriptions: {result}")
        print()
        
        # Verify the updates
        print("=" * 80)
        print("Verification - Current Subscription Status")
        print("=" * 80)
        print(f"{'Email':<40} {'Plan':<10} {'Status':<12} {'Has Pro':<8} {'Has Elite':<8}")
        print("-" * 80)
        
        rows = await conn.fetch("""
            SELECT 
                u.email,
                s.plan,
                s.status,
                CASE WHEN s.plan IN ('pro', 'elite') AND s.status IN ('trialing', 'active') THEN 'true' ELSE 'false' END as has_pro_access,
                CASE WHEN s.plan = 'elite' AND s.status IN ('trialing', 'active') THEN 'true' ELSE 'false' END as has_elite_access
            FROM users u
            LEFT JOIN subscriptions s ON u.id = s.user_id
            ORDER BY u.created_at
        """)
        
        for row in rows:
            print(f"{row['email']:<40} {row['plan'] or 'None':<10} {row['status'] or 'No Sub':<12} {row['has_pro_access']:<8} {row['has_elite_access']:<8}")
        
        print("=" * 80)
        
        # Count summary
        elite_count = sum(1 for row in rows if row['has_elite_access'] == 'true')
        pro_count = sum(1 for row in rows if row['has_pro_access'] == 'true' and row['has_elite_access'] != 'true')
        free_count = len(rows) - pro_count - elite_count
        
        print(f"\nSummary:")
        print(f"  Total users: {len(rows)}")
        print(f"  Elite tier: {elite_count}")
        print(f"  Pro tier: {pro_count}")
        print(f"  Free tier: {free_count}")
        print("=" * 80)
        print("✓ Update completed successfully!")
        
        await conn.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(update_beta_users())
