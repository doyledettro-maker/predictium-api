import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect(
        host='predictium-db.cdwgcgwm2ugb.us-east-2.rds.amazonaws.com',
        port=5432,
        database='predictium',
        user='postgres',
        password=':jpN:mz#ir48nl[Lewo|_4$hi9C_'
    )
    print("Connected!")
    result1 = await conn.execute("UPDATE subscriptions SET plan = 'elite', status = 'active' WHERE plan != 'elite' OR status != 'active'")
    print(f"Updated: {result1}")
    result2 = await conn.execute("INSERT INTO subscriptions (user_id, plan, status, created_at, updated_at) SELECT id, 'elite', 'active', NOW(), NOW() FROM users WHERE id NOT IN (SELECT user_id FROM subscriptions)")
    print(f"Inserted: {result2}")
    rows = await conn.fetch("SELECT u.email, s.plan, s.status FROM users u LEFT JOIN subscriptions s ON u.id = s.user_id ORDER BY u.created_at")
    print("\nUsers:")
    for r in rows:
        print(f"  {r['email']} - {r['plan']} - {r['status']}")
    await conn.close()

asyncio.run(main())
