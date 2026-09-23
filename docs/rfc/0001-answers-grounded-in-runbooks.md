# RFC-0001: Answers grounded in the team's runbooks

**Status:** draft
**Author:** the service's engineers
**Reviewers:** on-call team leads, security, platform
**Date:** 2026-09-24
**Related PRD:** [PRD-0001](../prd/0001-triage-assistant.md), requirement F7

## Summary

Today the assistant knows only the alerts. It can say *what* is wrong
and *what changed*, but never *what to do*, because that lives in each
team's runbooks. This RFC proposes three steps:
- store runbooks per team, with the same access rules as alerts;
- retrieve the few sections relevant to a question (hybrid keyword and
  vector search in Postgres);
- let the assistant answer from them, **citing each section it used**.

The eval gate grows new kinds of cases (retrieval, citation, isolation
of runbooks) before any of it reaches users.

## Motivation

- The most common follow-up to "what should I look at first?" is "how
  do I fix it?". The honest answer today is "the alerts do not say".
  Correct, and not useful.
- Runbooks exist, but engineers find them by searching a wiki under
  pressure. The right section is often three clicks away, or out of
  date.
- The pilot will ask for it. Answer feedback (F6) will show how many
  "not helpful" answers are really "you didn't tell me what to do".

If we do nothing, engineers copy runbook text and alert text into
unapproved tools to get this answer. That is the thing the product
exists to prevent.

## Proposed approach

**1. Runbooks as team data.** Add a `runbooks` table (team, title,
source URL, Markdown text, updated_at) and a `runbook_chunks` table
(one row per section, split at headings: text, heading path,
embedding).

Both get a `team_id`, with row-level security policies and
database-level tests in the same migration (ADR-0014). A runbook is
visible exactly when the team's alerts are. Team admins add them
(`require_role(ADMIN)`), through the API, or by a sync job from the
wiki.

**2. Hybrid retrieval, in Postgres.**
- **Keyword:** Postgres full-text search (`tsvector` on the chunk,
  `websearch_to_tsquery` on the question). It catches exact service
  names, error codes and hostnames.
- **Meaning:** a `pgvector` column, with an HNSW index and cosine
  distance. It catches paraphrases ("disk full" for "no space left on
  device").
- **Merged** by reciprocal rank fusion. The top 3–5 chunks go into the
  prompt, below the alerts, each tagged `[runbook: <title> § <heading>]`.

Everything runs through the caller's visibility: the same session
settings that feed row-level security for alerts. One datastore, one
access rule.

**3. Embeddings through the existing seam.** OpenAI-compatible
providers serve `/v1/embeddings` (OpenAI; Ollama with
`nomic-embed-text` locally). `app/llm.py` gains an `embed()` method.
Chunks are embedded when written, and questions when asked.

**4. The prompt:**
- use runbook sections only for *what to do*;
- cite each one used as `[runbook: … § …]`;
- never present a runbook step as something the assistant did.

The assistant still has no tools. **A person runs every command.**

**5. Evals first.** Before any user sees it, the harness gains:
- **retrieval cases:** questions labelled with the sections that answer
  them, scored by recall@k. That measures retrieval apart from the
  model;
- **citation checks:** every cited section exists in what was
  retrieved, and none is invented;
- **isolation cases:** a runbook of team B is never retrieved or cited
  for an asker in team A (api target);
- **injection cases:** runbook text is team-editable, so an instruction
  planted in a runbook is treated like one in an alert.

## Alternatives considered

- **Put every runbook in the prompt.** It is simple, but the prompt grows
  with the team's documentation: cost and latency rise with every page,
  and small models lose facts in long contexts. Rejected beyond a
  handful of runbooks.
- **A separate vector database** (Qdrant, Weaviate, OpenSearch). It adds
  a second datastore, a second access-control system that must mirror
  team membership, and a second backup. At this size (thousands of
  chunks) `pgvector` is enough, and keeps row-level security as the
  single rule.
- **Keyword search only.** It needs no embeddings, but misses
  paraphrases, and on-call questions are rarely worded like the
  runbook. Kept as half of the hybrid.
- **Vector search only.** It misses exact identifiers (`checkout-api`,
  `ORA-00060`), which is what the keyword half is for.
- **Fine-tuning a model on the runbooks.** Runbooks change weekly,
  fine-tuning gives no citations, and it cannot enforce team
  boundaries. Rejected.
- **The wiki's own search, called as a tool.** It gives tools to the
  model, and depends on the wiki's permissions matching teams.
  Rejected for now: no tools until each action has a confirmation
  step.

## Tradeoffs

**Easier:**
- actionable answers, with a source the engineer can check;
- stale runbooks become visible, because a cited section shows its
  `updated_at`.

**Harder:**
- a new data type to keep in sync;
- a new injection surface (runbook text);
- an embedding call per question;
- a bigger prompt.

**Costs to measure** before acceptance, on the pilot's real runbooks:
- retrieval latency (p95), which must keep the time to first word
  under the 3 s SLO;
- recall@5 on at least 30 labelled questions (target ≥ 0.8);
- the prompt tokens added per answer (and so the cost per answer);
- the eval suite with the new kinds, 10 runs per case, against the
  production model.

**Operations:**
- the database image changes to one with `pgvector`
  (`pgvector/pgvector:pg17`). That is a data migration step, rehearsed
  with `make fresh-host-test`;
- embeddings must be recomputed when the embedding model changes (a
  background job; the model name is stored with each vector).

## Open questions

- **The source of truth:** the wiki, synced one way, or runbooks edited
  in this service?
- **Embeddings at the provider:** do they need the same zero-retention
  terms as chat? They do if the runbooks are confidential. That is a
  question for RFQ-0001.
- **Chunk size and overlap:** measure at 200–400 words, split at
  headings, before choosing.
- **Stale sections:** show every runbook's age, or flag only sections
  older than N months?

## Rollout plan

1. The eval cases and the retrieval benchmark, on 3 pilot runbooks,
   with no user-visible change.
2. The migration, the retrieval and the prompt change behind a per-team
   setting. The pilot team only.
3. Shadow mode for two weeks: answers are generated with runbooks, but
   shown without them. Compare offline.
4. On for the pilot team. Watch answer feedback, time to first word,
   and cost per answer.
5. Other teams, once they have imported runbooks.

**Rollback:** turn the setting off. The tables stay; answers revert to
alerts only.
