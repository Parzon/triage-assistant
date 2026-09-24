# PgBouncer pool saturated

Clients queue for a server connection when every connection in PgBouncer's
pool is busy. Symptoms: rising request latency, then 503 errors from the api,
while Postgres itself looks idle.

## Confirm the saturation

Connect to the admin console and run `SHOW POOLS;`. A growing `cl_waiting`
column with `sv_active` at the pool size confirms it. `SHOW CLIENTS;` shows who
is waiting.

## Find the slow queries

Long transactions hold server connections. Look at `pg_stat_activity` for
sessions in state `idle in transaction` or queries running for minutes, and
check the slow-query dashboard.

## Relieve the pressure

Kill the offending backend with `SELECT pg_terminate_backend(pid)`. Raise
`default_pool_size` only when Postgres has spare connections: the pool exists to
protect Postgres from too many of them.
