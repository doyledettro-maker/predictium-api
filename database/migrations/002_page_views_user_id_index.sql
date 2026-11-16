-- DELETE /me removes the caller's analytics rows (page_views.user_id is the
-- Cognito sub). Without this index that delete scans the whole table.
-- CONCURRENTLY cannot run inside a transaction: run this file on its own,
-- for example `psql -f 002_page_views_user_id_index.sql`, not wrapped in BEGIN.
-- Idempotent. Safe to run while the API is serving.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_page_views_user_id
    ON page_views (user_id)
    WHERE user_id IS NOT NULL;
