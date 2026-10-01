# One-off, idempotent: the least-privileged role the api connects as. RDS runs no init
# scripts, so this does what infra/postgres/initdb does for the compose database.
# libpq reads PGHOST, PGUSER, PGPASSWORD, PGDATABASE and PGSSLMODE from the environment.
psql -v ON_ERROR_STOP=1 -v app_password="$APP_DB_PASSWORD" <<'SQL'
SELECT 'CREATE ROLE triage_app LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'triage_app') \gexec
ALTER ROLE triage_app PASSWORD :'app_password';
ALTER ROLE triage_app SET statement_timeout = '10s';
ALTER ROLE triage_app SET idle_in_transaction_session_timeout = '30s';
GRANT CONNECT ON DATABASE triage TO triage_app;
GRANT USAGE ON SCHEMA public TO triage_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO triage_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO triage_app;
SQL
