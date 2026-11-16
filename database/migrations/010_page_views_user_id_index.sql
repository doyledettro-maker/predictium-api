-- DELETE /me removes the caller's analytics rows (page_views.user_id is the
-- Cognito sub). Without this index that delete scans the whole table.
-- CONCURRENTLY cannot run inside a transaction: run this file on its own,
-- for example `psql -f 010_page_views_user_id_index.sql`, not wrapped in BEGIN.
-- Run it after 009. Then check the index is valid (a failed CONCURRENTLY
-- build leaves an invalid one):
--   SELECT indisvalid FROM pg_index WHERE indexrelid = 'idx_page_views_user_id'::regclass;
-- If false: DROP INDEX CONCURRENTLY idx_page_views_user_id; then run this again.
-- Idempotent. Safe to run while the API is serving.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_page_views_user_id
    ON page_views (user_id)
    WHERE user_id IS NOT NULL;
