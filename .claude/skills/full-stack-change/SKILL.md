---
name: full-stack-change
description: Checklists for the changes that are hard to undo once shipped, an API endpoint, a database schema change or migration, and a user interface. Use when adding or changing an endpoint, a table, a column, a migration, a page or a component.
---

# Full-stack change

Use the section for each part the change touches. Where `AGENTS.md` is more
specific (this repository's error format, roles, migration tool), it wins.

## An endpoint

- **Contract first.** Write down the request, the response and the errors
  before the handler. Callers come to depend on everything they can
  observe, documented or not, so add rather than change: a new optional
  field, not a renamed one; a new endpoint, not a new meaning for an old
  one.
- **One error shape** across the API: a machine-readable code and a message
  for people, and the same status code for the same failure everywhere.
- **Access, every time.** Check who is calling, and that this caller may
  act on this record, not only that they are signed in. A record the caller
  may not see is a 404, so the answer doesn't confirm it exists; 403 is for
  a record they can see but may not change. Never trust an owner, team or
  id from the request body without that check.
- **Validate at the edge:** bodies, query strings, headers, uploads, and
  other services' responses, which are untrusted input too. Inside, trust
  the types.
- **Limits:** a maximum page size and request size, a timeout on every
  outbound call, a rate limit on anything expensive.
- **Retries:** a write a client may retry (a payment, a creation) takes an
  idempotency key or is safe to repeat.
- **Never in a response or a log:** secrets, tokens, password hashes, stack
  traces. The client gets a plain error; the details go to the server log
  with the request id.
- **Tests:** success; invalid input; not signed in; another tenant's
  record; a role too low; the dependency down.

## A schema change

- **Never change a column in place.** During a deploy the old and the new
  code run side by side, so every step must work with both:
  1. Expand: add the new column or table, nullable or with a default.
  2. Write to both the old and the new.
  3. Backfill the existing rows in batches, away from peak load.
  4. Read from the new one.
  5. Contract: stop writing the old one, and drop it in a later, separate
     release.
- **Locks:** build indexes without blocking writes (Postgres: `CREATE INDEX
  CONCURRENTLY`), set a lock timeout, and never update a whole large table
  in one transaction.
- **The way back:** write the down migration and run it once, or say in the
  PR why there is none.
- **Generated migrations:** read every line. Generators miss renames (a drop
  plus an add loses the data) and some constraint changes.
- **Access rules come with the table:** a new table holding a team's or a
  customer's data gets the same protection as the others (row-level
  security, where the repository uses it), in the same migration.

## A user interface

- **Accessible:** every control works with the keyboard alone and shows its
  focus; text contrast at least 4.5:1; a visible label on every input (a
  placeholder is not a label); alt text on images; motion respects the
  reduced-motion setting.
- **Every state:** loading, empty, error, partly loaded, and too much (long
  names, ten thousand rows). An empty screen says what to do next. An error
  says what happened and how to fix it.
- **Words:** name things the way users do, not the way the system is built
  ("notifications", not "webhook config"). A button says what it does
  ("Save changes", not "Submit"), and the result uses the same word
  ("Published" after "Publish"). Errors don't apologise and are never vague.
- **Any screen size:** usable at phone width without sideways scrolling;
  touch targets about 44 by 44 px.
- **Follow what exists:** the project's components, spacing, colours and
  type. A new pattern needs a reason. A page meant to look designed gets a
  deliberate look, not the generated default (identical rounded cards,
  gradient washes, an all-caps label over every heading).
- **Look at it:** in a browser, at phone and desktop widths, with the
  keyboard only.
