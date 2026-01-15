#!/bin/bash
# Update Beta Users to Elite Plan
# Run this script in AWS CloudShell

set -e

echo "=========================================="
echo "Updating Beta Users to Elite Plan"
echo "=========================================="
echo ""

# Database connection details
export PGHOST="predictium-db.cdwgcgwm2ugb.us-east-2.rds.amazonaws.com"
export PGUSER="postgres"
export PGDATABASE="predictium"
export PGPORT="5432"
export PGPASSWORD=":jpN:mz#ir48nl[Lewo|_4\$hi9C_"

# Install PostgreSQL client if not already installed
if ! command -v psql &> /dev/null; then
    echo "Installing PostgreSQL client..."
    sudo yum install postgresql15 -y
    echo "✓ PostgreSQL client installed"
    echo ""
fi

# Test connection
echo "Testing database connection..."
if psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c "SELECT version();" > /dev/null 2>&1; then
    echo "✓ Database connection successful"
    echo ""
else
    echo "✗ Database connection failed"
    exit 1
fi

# Update existing subscriptions to elite
echo "Updating existing subscriptions to elite..."
psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" <<EOF
UPDATE subscriptions
SET plan = 'elite',
    status = 'active'
WHERE plan != 'elite' OR status != 'active';
EOF

echo "✓ Subscriptions updated"
echo ""

# Create subscriptions for users who don't have one
echo "Creating elite subscriptions for users without subscriptions..."
psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" <<EOF
INSERT INTO subscriptions (user_id, plan, status, created_at, updated_at)
SELECT id, 'elite', 'active', NOW(), NOW()
FROM users
WHERE id NOT IN (SELECT user_id FROM subscriptions);
EOF

echo "✓ New subscriptions created"
echo ""

# Verify the updates
echo "=========================================="
echo "Verification - Current Subscription Status"
echo "=========================================="
psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" <<EOF
SELECT 
    u.email,
    s.plan,
    s.status,
    CASE WHEN s.plan IN ('pro', 'elite') AND s.status IN ('trialing', 'active') THEN 'true' ELSE 'false' END as has_pro_access,
    CASE WHEN s.plan = 'elite' AND s.status IN ('trialing', 'active') THEN 'true' ELSE 'false' END as has_elite_access
FROM users u
LEFT JOIN subscriptions s ON u.id = s.user_id
ORDER BY u.created_at;
EOF

echo ""
echo "=========================================="
echo "✓ Update completed successfully!"
echo "=========================================="

# Clear password from environment
unset PGPASSWORD
