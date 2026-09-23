-- Blocked queries and what blocks them. Empty = nothing waits on a lock.
--   make db-locks [ENV=prod]
-- The classic incident: a migration's ALTER TABLE waits behind a long
-- query, and every new query on the table then queues behind the ALTER.
-- (Migrations here set lock_timeout = 5s so the ALTER gives up instead.)
SELECT blocked.pid                                   AS blocked_pid,
       left(blocked.query, 60)                       AS blocked_query,
       date_trunc('second', now() - blocked.query_start) AS waiting_for,
       blocking.pid                                  AS blocking_pid,
       blocking.state                                AS blocking_state,
       left(blocking.query, 60)                      AS blocking_query
FROM pg_stat_activity AS blocked
CROSS JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS b(pid)
JOIN pg_stat_activity AS blocking ON blocking.pid = b.pid
ORDER BY waiting_for DESC;
