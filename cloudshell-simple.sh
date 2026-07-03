# Credentials must come from the environment — never commit them.
# Set PGPASSWORD before running, e.g.:
#   export PGPASSWORD="$(aws secretsmanager get-secret-value --secret-id predictium-db-password --query SecretString --output text)"
export PGHOST="${PGHOST:-predictium-db.cdwgcgwm2ugb.us-east-2.rds.amazonaws.com}"
export PGUSER="${PGUSER:-postgres}"
export PGDATABASE="${PGDATABASE:-predictium}"
if [ -z "$PGPASSWORD" ]; then echo "ERROR: PGPASSWORD is not set. Refusing to run." >&2; exit 1; fi
if ! command -v psql &> /dev/null; then sudo yum install postgresql15 -y; fi
echo "Updating subscriptions to elite..."
psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c "UPDATE subscriptions SET plan = 'elite', status = 'active' WHERE plan != 'elite' OR status != 'active';"
psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c "INSERT INTO subscriptions (user_id, plan, status, created_at, updated_at) SELECT id, 'elite', 'active', NOW(), NOW() FROM users WHERE id NOT IN (SELECT user_id FROM subscriptions);"
echo "Current subscription status:"
psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c "SELECT u.email, s.plan, s.status FROM users u LEFT JOIN subscriptions s ON u.id = s.user_id ORDER BY u.created_at;"
unset PGPASSWORD
echo "Done!"
