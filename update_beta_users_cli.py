#!/usr/bin/env python3
"""
Update all beta users to premium plan via direct database connection.
Can be run locally or in CloudShell.
"""

import asyncio
import asyncpg

# Database connection details
DB_HOST = "predictium-db.cdwgcgwm2ugb.us-east-2.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "predictium"
DB_USER = "postgres"
DB_PASSWORD = ":jpN:mz#ir48nl[Lewo|_4$hi9C_"

async def update_beta_users():
    """Update all beta users to premium plan."""
    print("=" * 80)
    print("Updating Beta Users to Premium Plan")
    print("=" * 80)
    print(f"Connecting to: {DB_HOST}:{DB_PORT}/{DB_NAME}")
    print()
    
    try:
        conn = await asyncpg.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )
        
        print("✓ Connected to database")
        print()
        
        print("Updating existing subscriptions to premium...")
        result = await conn.execute("""
            UPDATE subscriptions
            SET plan = 'premium',
                status = 'active'
            WHERE plan != 'premium' OR status != 'active'
        """)
        print(f"✓ Updated subscriptions: {result}")
        print()
        
        print("Creating premium subscriptions for users without subscriptions...")
        result = await conn.execute("""
            INSERT INTO subscriptions (user_id, plan, status, created_at, updated_at)
            SELECT id, 'premium', 'active', NOW(), NOW()
            FROM users
            WHERE id NOT IN (SELECT user_id FROM subscriptions)
        """)
        print(f"✓ Created subscriptions: {result}")
        print()
        
        print("=" * 80)
        print("Verification - Current Subscription Status")
        print("=" * 80)
        print(f"{'Email':<40} {'Plan':<10} {'Status':<12} {'Premium':<8}")
        print("-" * 80)
        
        rows = await conn.fetch("""
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
        
        for row in rows:
            print(f"{row['email']:<40} {row['plan'] or 'None':<10} {row['status'] or 'No Sub':<12} {row['has_premium_access']:<8}")
        
        print("=" * 80)
        
        premium_count = sum(1 for row in rows if row['has_premium_access'] == 'true')
        free_count = len(rows) - premium_count
        
        print(f"\nSummary:")
        print(f"  Total users: {len(rows)}")
        print(f"  Premium tier: {premium_count}")
        print(f"  Free tier: {free_count}")
        print("=" * 80)
        print("✓ Update completed successfully!")
        
        await conn.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(update_beta_users())
