export PGHOST="predictium-db.cdwgcgwm2ugb.us-east-2.rds.amazonaws.com"
export PGUSER="postgres"
export PGDATABASE="predictium"
export PGPASSWORD=":jpN:mz#ir48nl[Lewo|_4\$hi9C_"
if ! command -v psql &> /dev/null; then sudo yum install postgresql15 -y; fi
echo "Updating subscriptions to elite..."
psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c "UPDATE subscriptions SET plan = 'elite', status = 'active' WHERE plan != 'elite' OR status != 'active';"
psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c "INSERT INTO subscriptions (user_id, plan, status, created_at, updated_at) SELECT id, 'elite', 'active', NOW(), NOW() FROM users WHERE id NOT IN (SELECT user_id FROM subscriptions);"
echo "Current subscription status:"
psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c "SELECT u.email, s.plan, s.status FROM users u LEFT JOIN subscriptions s ON u.id = s.user_id ORDER BY u.created_at;"
unset PGPASSWORD
echo "Done!"
