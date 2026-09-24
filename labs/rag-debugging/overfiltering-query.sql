-- As the app's role (row-level security applies), a viewer of one lab team
-- (1% of the rows): the 20 nearest sections, four ways. `make
-- rag-overfiltering-lab` runs it; read the "rows=" of each Limit line.
--
-- The vector is a bound parameter, as in the service: a prepared statement
-- with a generic plan (and the plans show $1, not 768 numbers).
\set team_id `echo "$TEAM_ID"`
SELECT (SELECT array_agg(round((random() - 0.5)::numeric, 4)) FROM generate_series(1, 768))::vector::text AS v \gset
SET plan_cache_mode = force_generic_plan;
BEGIN;
SELECT set_config('app.read_team_ids', '{' || :team_id || '}', true) \g /dev/null
PREPARE nearest(vector) AS
  SELECT c.id FROM runbook_chunks c
  WHERE c.embedding_key = 'lab-random'
  ORDER BY c.embedding <=> $1 LIMIT 20;
PREPARE nearest_in_team(vector, bigint[]) AS
  SELECT c.id FROM runbook_chunks c
  WHERE c.embedding_key = 'lab-random' AND c.team_id = ANY($2)
  ORDER BY c.embedding <=> $1 LIMIT 20;

\echo '=== 1. HNSW index, team filtered after the scan, iterative scans off'
SET LOCAL hnsw.iterative_scan = off;
EXPLAIN (ANALYZE, COSTS OFF) EXECUTE nearest(:'v');

\echo '=== 2. HNSW index, iterative scans on (relaxed_order)'
SET LOCAL hnsw.iterative_scan = relaxed_order;
EXPLAIN (ANALYZE, COSTS OFF) EXECUTE nearest(:'v');

\echo '=== 3. no index at all: every row compared (exact)'
-- Its own statement, planned after the setting: a cached plan is never
-- re-planned because a planner setting changed.
SET LOCAL enable_indexscan = off;
PREPARE nearest_exact(vector) AS
  SELECT c.id FROM runbook_chunks c
  WHERE c.embedding_key = 'lab-random'
  ORDER BY c.embedding <=> $1 LIMIT 20;
EXPLAIN (ANALYZE, COSTS OFF) EXECUTE nearest_exact(:'v');
SET LOCAL enable_indexscan = on;

\echo '=== 4. what the service sends: an explicit team filter, no OR'
EXPLAIN (ANALYZE, COSTS OFF) EXECUTE nearest_in_team(:'v', ARRAY[:team_id]::bigint[]);
ROLLBACK;
