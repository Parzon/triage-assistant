-- As the app's role (row-level security applies), a viewer of one lab team:
-- the 20 nearest sections, three ways. Run through psql as APP_DB_USER.
-- The question's vector must be a constant (or a bound parameter, as in the
-- service): ORDER BY distance to a column of another table cannot use the
-- HNSW index, and Postgres then compares every row - exact, and no trap.
\set team_id `echo "$TEAM_ID"`
SELECT (SELECT array_agg(round((random() - 0.5)::numeric, 4)) FROM generate_series(1, 768))::vector::text AS v \gset
BEGIN;
SELECT set_config('app.read_team_ids', '{' || :team_id || '}', true) \g /dev/null

\echo '--- 1. iterative scans off (pgvector before 0.8)'
SET LOCAL hnsw.iterative_scan = off;
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON)
  SELECT c.id FROM runbook_chunks c WHERE c.embedding_model = 'lab-random'
  ORDER BY c.embedding <=> :'v'::vector LIMIT 20;

\echo '--- 2. iterative scans on (relaxed_order): what the service sets'
SET LOCAL hnsw.iterative_scan = relaxed_order;
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON)
  SELECT c.id FROM runbook_chunks c WHERE c.embedding_model = 'lab-random'
  ORDER BY c.embedding <=> :'v'::vector LIMIT 20;

\echo '--- 3. exact: no index, every row compared'
SET LOCAL enable_indexscan = off;
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON)
  SELECT c.id FROM runbook_chunks c WHERE c.embedding_model = 'lab-random'
  ORDER BY c.embedding <=> :'v'::vector LIMIT 20;
ROLLBACK;
