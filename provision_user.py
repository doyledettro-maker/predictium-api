#!/usr/bin/env python3
"""
Provision a new beta user end-to-end:
  1. Create Cognito account with temporary password
  2. Create user record in DB
  3. Create premium subscription

Usage: python provision_user.py user@example.com [password]
  If no password given, generates a random one.
  User will be forced to change password on first login.
"""

import asyncio
import json
import secrets
import string
import sys
import subprocess

import asyncpg

# ── Config ──────────────────────────────────────────────────────────────────
COGNITO_USER_POOL_ID = "us-east-2_wgbzYKLtp"
COGNITO_REGION = "us-east-2"

DB_HOST = "predictium-db.cdwgcgwm2ugb.us-east-2.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "predictium"
DB_USER = "postgres"
DB_PASSWORD = ":jpN:mz#ir48nl[Lewo|_4$hi9C_"


def generate_password(length=12):
    """Generate a readable temporary password meeting Cognito requirements."""
    # Ensure at least one of each required type
    upper = secrets.choice(string.ascii_uppercase)
    lower = secrets.choice(string.ascii_lowercase)
    digit = secrets.choice(string.digits)
    symbol = secrets.choice("!@#$%^&*")
    rest = ''.join(secrets.choice(string.ascii_letters + string.digits + "!@#$%^&*") for _ in range(length - 4))
    password = list(upper + lower + digit + symbol + rest)
    secrets.SystemRandom().shuffle(password)
    return ''.join(password)


def create_cognito_user(email: str, password: str) -> str:
    """Create user in Cognito via AWS CLI. Returns the Cognito sub (user ID)."""
    # Create user with temporary password
    result = subprocess.run(
        [
            "aws", "cognito-idp", "admin-create-user",
            "--user-pool-id", COGNITO_USER_POOL_ID,
            "--username", email,
            "--user-attributes",
            f"Name=email,Value={email}",
            "Name=email_verified,Value=true",
            "--temporary-password", password,
            "--message-action", "SUPPRESS",  # Don't send welcome email
            "--region", COGNITO_REGION,
        ],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        error = result.stderr.strip()
        if "UsernameExistsException" in error:
            print(f"ℹ️  Cognito account already exists for {email}, skipping creation")
            # Get existing sub
            return get_cognito_sub(email)
        raise RuntimeError(f"Cognito create failed: {error}")

    data = json.loads(result.stdout)
    sub = None
    for attr in data["User"]["Attributes"]:
        if attr["Name"] == "sub":
            sub = attr["Value"]
            break

    if not sub:
        raise RuntimeError("No sub returned from Cognito")

    print(f"✅ Cognito account created (sub: {sub})")

    # Set permanent password so they can log in immediately
    subprocess.run(
        [
            "aws", "cognito-idp", "admin-set-user-password",
            "--user-pool-id", COGNITO_USER_POOL_ID,
            "--username", email,
            "--password", password,
            "--permanent",
            "--region", COGNITO_REGION,
        ],
        capture_output=True, text=True, check=True,
    )
    print(f"✅ Password set (permanent)")

    return sub


def get_cognito_sub(email: str) -> str:
    """Look up existing Cognito user's sub."""
    result = subprocess.run(
        [
            "aws", "cognito-idp", "admin-get-user",
            "--user-pool-id", COGNITO_USER_POOL_ID,
            "--username", email,
            "--region", COGNITO_REGION,
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    for attr in data["UserAttributes"]:
        if attr["Name"] == "sub":
            return attr["Value"]
    raise RuntimeError("Could not find sub for existing user")


async def provision_db(email: str, cognito_sub: str):
    """Create DB user + premium subscription."""
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )

    # Check if user exists
    existing = await conn.fetchrow(
        "SELECT id, email FROM users WHERE LOWER(email) = LOWER($1)", email
    )

    if existing:
        user_id = existing["id"]
        print(f"ℹ️  DB user already exists (id: {user_id})")
    else:
        # Create user record
        user_id = await conn.fetchval(
            """INSERT INTO users (cognito_sub, email, created_at, updated_at)
               VALUES ($1, $2, NOW(), NOW())
               RETURNING id""",
            cognito_sub, email,
        )
        print(f"✅ DB user created (id: {user_id})")

    # Upsert subscription
    sub = await conn.fetchrow(
        "SELECT plan, status FROM subscriptions WHERE user_id = $1", user_id
    )

    if sub and sub["plan"] == "premium" and sub["status"] == "active":
        print(f"✅ Already has premium access")
    elif sub:
        await conn.execute(
            "UPDATE subscriptions SET plan = 'premium', status = 'active', updated_at = NOW() WHERE user_id = $1",
            user_id,
        )
        print(f"✅ Upgraded to premium (was {sub['plan']}/{sub['status']})")
    else:
        await conn.execute(
            "INSERT INTO subscriptions (user_id, plan, status, created_at, updated_at) VALUES ($1, 'premium', 'active', NOW(), NOW())",
            user_id,
        )
        print(f"✅ Premium subscription created")

    await conn.close()


async def main():
    if len(sys.argv) < 2:
        print("Usage: python provision_user.py <email> [password]")
        sys.exit(1)

    email = sys.argv[1].strip().lower()
    password = sys.argv[2] if len(sys.argv) > 2 else generate_password()

    print(f"\n{'='*60}")
    print(f"Provisioning beta user: {email}")
    print(f"{'='*60}\n")

    # Step 1: Cognito
    cognito_sub = create_cognito_user(email, password)

    # Step 2: Database
    await provision_db(email, cognito_sub)

    # Summary
    print(f"\n{'='*60}")
    print(f"✅ DONE — User ready to log in")
    print(f"{'='*60}")
    print(f"  Site:     https://www.predictium.ai")
    print(f"  Email:    {email}")
    print(f"  Password: {password}")
    print(f"  Plan:     Premium (full access)")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
