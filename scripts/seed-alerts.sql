-- Bulk-inserts synthetic alerts for load tests and query-plan work.
--   make seed n=1000000            (dev stack)
--   make seed n=1000000 ENV=prod   (production-shaped stack)
-- Severity is skewed like real monitoring (mostly info, few critical) and
-- timestamps spread over 30 days, so query plans look like production's.
-- Alerts are spread over three teams - the load tests' user sees two of them,
-- which exercises the multi-team read (app/queries.py).
-- Runs as the schema owner, directly against Postgres.
\set ON_ERROR_STOP on
\timing on

INSERT INTO teams (slug, name) VALUES ('payments', 'payments'), ('platform', 'platform')
ON CONFLICT (slug) DO NOTHING;

WITH t AS (SELECT array_agg(id ORDER BY id) AS ids FROM teams WHERE slug IN ('default', 'payments', 'platform'))
INSERT INTO alerts (team_id, source, severity, message, created_at)
SELECT
  t.ids[1 + floor(random() * 3)::int],
  (ARRAY['prometheus', 'grafana', 'cloudwatch', 'sentry', 'pagerduty'])[1 + floor(random() * 5)::int],
  CASE
    WHEN r < 0.70 THEN 'info'
    WHEN r < 0.90 THEN 'warning'
    WHEN r < 0.98 THEN 'high'
    ELSE 'critical'
  END,
  'synthetic alert ' || g || ': ' || md5(g::text),
  now() - random() * interval '30 days'
FROM t, (SELECT g, random() AS r FROM generate_series(1, :n) AS g) AS s;

-- Fresh statistics: the planner chooses plans from them, and autovacuum
-- would only get to it later.
ANALYZE alerts;

SELECT severity, count(*) FROM alerts GROUP BY severity ORDER BY 2 DESC;
SELECT teams.slug AS team, count(*) FROM alerts JOIN teams ON teams.id = alerts.team_id GROUP BY 1 ORDER BY 2 DESC;
