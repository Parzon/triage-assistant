-- 100 teams x 500 runbook sections with random 768-dimension vectors: enough
-- rows that the planner uses the HNSW index, while each team owns 1% of them.
-- Run as the schema owner (row-level security does not apply to it):
--   make overfiltering-lab   (see README.md)
INSERT INTO teams (slug, name)
SELECT 'lab-t' || g, 'Lab team ' || g FROM generate_series(1, 100) g
ON CONFLICT (slug) DO NOTHING;

INSERT INTO runbooks (team_id, title, body, body_sha256, embedding_model)
SELECT id, 'lab', 'lab', 'lab', 'lab-random' FROM teams WHERE slug LIKE 'lab-t%'
ON CONFLICT (team_id, title) DO NOTHING;

-- "+ 0 * n" makes the subquery depend on the row: evaluated once per row, so
-- every section gets its own vector (an uncorrelated one runs once, and all
-- 50,000 would share it). "+ 0 * d" keeps the aggregate in the subquery: an
-- aggregate whose arguments name only outer columns belongs to the outer query.
INSERT INTO runbook_chunks (runbook_id, team_id, ordinal, heading, content, embedding, embedding_model)
SELECT r.id, r.team_id, n, 'lab ' || n, 'lab content',
       (SELECT array_agg(random() - 0.5 + 0 * n + 0 * d)::vector FROM generate_series(1, 768) d),
       'lab-random'
FROM runbooks r CROSS JOIN generate_series(1, 500) n
WHERE r.title = 'lab' AND r.embedding_model = 'lab-random';

ANALYZE runbook_chunks;
