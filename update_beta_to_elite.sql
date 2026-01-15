-- Update all beta users to elite plan
-- This ensures all initial beta users have elite access

-- Update existing subscriptions to elite
UPDATE subscriptions
SET plan = 'elite',
    status = 'active'
WHERE plan != 'elite' OR status != 'active';

-- Create subscriptions for users who don't have one
INSERT INTO subscriptions (user_id, plan, status, created_at, updated_at)
SELECT id, 'elite', 'active', NOW(), NOW()
FROM users
WHERE id NOT IN (SELECT user_id FROM subscriptions);

-- Verify the updates
SELECT 
    u.email,
    s.plan,
    s.status,
    CASE WHEN s.plan IN ('pro', 'elite') AND s.status IN ('trialing', 'active') THEN 'true' ELSE 'false' END as has_pro_access,
    CASE WHEN s.plan = 'elite' AND s.status IN ('trialing', 'active') THEN 'true' ELSE 'false' END as has_elite_access
FROM users u
LEFT JOIN subscriptions s ON u.id = s.user_id
ORDER BY u.created_at;
