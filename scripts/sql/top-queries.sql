-- The queries that cost the database the most time since the last reset.
--   make db-top-queries [ENV=prod]      reset: SELECT pg_stat_statements_reset();
-- total_ms is what matters for capacity (calls x mean); a fast query called
-- a million times can outrank a slow one called once.
SELECT calls,
       round(total_exec_time)                                   AS total_ms,
       round(mean_exec_time::numeric, 2)                        AS mean_ms,
       round((100 * total_exec_time / sum(total_exec_time) OVER ())::numeric, 1) AS pct,
       left(regexp_replace(query, '\s+', ' ', 'g'), 90)         AS query
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY total_exec_time DESC
LIMIT 10;
