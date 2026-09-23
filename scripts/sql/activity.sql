-- Who is connected and what each connection is doing right now.
--   make db-activity [ENV=prod]
-- state 'idle in transaction' for long = a transaction held open (by a
-- request that is waiting on something else) - it blocks vacuum and can
-- hold locks; idle_in_transaction_session_timeout on the app role ends it.
SELECT pid, usename, application_name, client_addr, state,
       date_trunc('second', now() - xact_start)  AS xact_age,
       date_trunc('second', now() - query_start) AS query_age,
       wait_event_type, wait_event,
       left(regexp_replace(query, '\s+', ' ', 'g'), 70) AS query
FROM pg_stat_activity
WHERE datname = current_database() AND pid <> pg_backend_pid()
ORDER BY xact_start NULLS LAST;
