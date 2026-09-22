#!/usr/bin/env bash
# Runs ONCE, when the postgres container initialises an empty data volume.
# Editing this file does nothing to an existing volume: recreate it
# (`make nuke`) or apply the change with a migration/by hand.
#
# Two roles, least privilege:
#   POSTGRES_USER  owns the schema; migrations run as it (direct to Postgres).
#   APP_DB_USER    what the api uses (through PgBouncer): read/write rows,
#                  cannot create, alter or drop anything.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v db="$POSTGRES_DB" -v app_user="$APP_DB_USER" -v app_password="$APP_DB_PASSWORD" <<'SQL'
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password';

-- Server-side limits for every connection the app opens. They live on the
-- role because PgBouncer's transaction pooling discards session-level SET.
ALTER ROLE :"app_user" SET statement_timeout = '10s';
ALTER ROLE :"app_user" SET idle_in_transaction_session_timeout = '30s';

GRANT CONNECT ON DATABASE :"db" TO :"app_user";
GRANT USAGE ON SCHEMA public TO :"app_user";

-- Applies to tables/sequences the owner creates later, i.e. every migration.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";
SQL
