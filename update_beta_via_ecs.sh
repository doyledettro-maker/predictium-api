#!/bin/bash
# Run this via ECS Exec to update beta users to elite
# Usage: aws ecs execute-command --cluster predictium-cluster --task <TASK_ARN> --container predictium-api --interactive --command "/bin/bash" --region us-east-2

export PGHOST="predictium-db.cdwgcgwm2ugb.us-east-2.rds.amazonaws.com"
export PGUSER="postgres"
export PGDATABASE="predictium"
export PGPASSWORD=":jpN:mz#ir48nl[Lewo|_4\$hi9C_"

psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" <<EOF
UPDATE subscriptions
SET plan = 'elite', status = 'active'
WHERE plan != 'elite' OR status != 'active';

INSERT INTO subscriptions (user_id, plan, status, created_at, updated_at)
SELECT id, 'elite', 'active', NOW(), NOW()
FROM users
WHERE id NOT IN (SELECT user_id FROM subscriptions);

SELECT u.email, s.plan, s.status,
    CASE WHEN s.plan IN ('pro', 'elite') AND s.status IN ('trialing', 'active') THEN 'true' ELSE 'false' END as has_pro,
    CASE WHEN s.plan = 'elite' AND s.status IN ('trialing', 'active') THEN 'true' ELSE 'false' END as has_elite
FROM users u
LEFT JOIN subscriptions s ON u.id = s.user_id
ORDER BY u.created_at;
EOF

unset PGPASSWORD
