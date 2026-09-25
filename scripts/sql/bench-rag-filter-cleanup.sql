-- Removes the benchmark's teams; their runbooks and sections go with them (cascade).
DELETE FROM teams WHERE slug LIKE 'bench-t%';
