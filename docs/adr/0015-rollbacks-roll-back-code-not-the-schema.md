# ADR-0015: Rollbacks roll back the code, never the schema

**Status:** accepted
**Date:** 2026-09-23

## Context

ADR-0011 made rollback "deploy the previous tag". That had only ever been
tried between releases with the same migrations. Rolling v0.3.0 back to
v0.2.0 on the demo host failed at the first step: `make deploy` ran
v0.2.0's migrations, and v0.2.0's Alembic had never heard of the
revision v0.3.0 applied ("Can't locate revision identified by
'ce83ff21ab01'"). The deploy stopped before touching anything, so it
failed safely, but the procedure could not work across any migration.
That is exactly when a rollback is most likely to be needed.

## Decision

1. **A deploy never reverses a migration.** When the database's revision
   is one the image does not know, the database is ahead: this is a
   rollback. `make deploy` skips the migrations, says so in its output,
   and rolls out the older code on the newer schema.
2. **That is safe only down to a release that runs on the current
   schema.** Expand/contract (daily-work chapter) guarantees it back to
   the release just before a contract migration, never past it:
   - v0.3.0 → v0.2.0 is fine (v0.2.0 names the caller, as the
     row-level security policies need);
   - v0.3.0 → v0.1.0 is not.
3. **Reversing a migration is a separate, deliberate step:**
   `alembic downgrade <revision>`, run with the *newer* image, which
   knows the migration. It is for a migration that is itself the fault,
   and it is decided by a person, never by a deploy script.

## Alternatives considered

- **Run `alembic downgrade` automatically on rollback.** Downgrades are
  the least-tested code in any project. They can lose data (a dropped
  column cannot come back), and a rollback happens under pressure.
- **Keep failing, and document a manual procedure.** Every rollback would
  start with a surprise.
- **Ship every image with every future migration.** That is impossible:
  an old image cannot know migrations written after it.

## Consequences

Measured on the demo host, under steady signed-in load (8 readers and a
chat stream, through the TLS edge):
- **Rolling v0.3.0 back to v0.2.0:** "the database (ce83ff21ab01) is
  ahead of 0.2.0: rolling back the code only". v0.2.0 ran on the
  row-level-security schema, and no request failed during the api and
  nginx swaps.
- **Rolling forward again:** v0.3.0's contract migration ran while v0.2.0
  served, and nothing failed. The only failures in either deploy were
  the TLS edge's replacement (~2-3 s), which the same change fixes for
  future releases (below).

The rule every migration author follows gets one more reason: a release
must be able to run on the next release's schema, because it will, the
day it is rolled back to.

Also in this change: CI stamps the edge image with a hash of its build
inputs. A rebuilt edge never has the same layers (COPY records file
times, and every checkout gets new ones), so every release replaced it:
~2 s of refused connections per deploy. Now `make deploy` replaces it
only when `tools/edge` changed.
