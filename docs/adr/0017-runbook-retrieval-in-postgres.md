# ADR-0017: Runbook retrieval in Postgres, hybrid, under row-level security

**Status:** accepted
**Date:** 2026-09-24

## Context

[RFC-0001](../rfc/0001-answers-grounded-in-runbooks.md) asked for answers
that give the steps from the team's own runbooks and cite them. The
retrieval behind it had to:
- keep team isolation exactly as for alerts, since a runbook can name
  secrets and internal systems;
- find paraphrases as well as exact identifiers;
- work with any OpenAI-compatible embedding model;
- degrade rather than fail when the embedding model is slow or down.

## Decision

1. **pgvector inside the existing Postgres.** The image is
   `pgvector/pgvector:0.8.6-pg17-trixie`: the same Debian 13, glibc 2.41
   and PostgreSQL 17.11 as `postgres:17`, so collations and indexes are
   unchanged. There is no second datastore, and no second
   access-control system to keep in step with team membership.
2. **Runbooks are team data.** `runbooks` and `runbook_chunks` have
   row-level security like `alerts`: members read, team admins write. The
   search also sends an explicit `team_id = ANY(...)`, not a filter hidden
   behind an OR.
   - Measured on 50,000 sections in 100 teams, over three runs: behind
     the OR, a one-team search walked the HNSW index and found 0 or 1 of
     20. With iterative scans on, it found 20, in 32 to 71 ms.
   - The explicit filter found 20 in 0.8 to 1.6 ms.
3. **Hybrid retrieval with reciprocal rank fusion (k = 60).**
   - Postgres full-text search over the question's own lexemes, ORed,
     finds exact identifiers.
   - Cosine distance over an HNSW index finds meaning.
   - Each contributes its 20 best candidates.
4. **No distance cutoff.** Measured: with nomic-embed-text, relevant
   sections reached distance 0.43 while unanswerable questions' best
   matches began at 0.41, so no cutoff separates them. The prompt and the
   evals handle irrelevant sections instead.
5. **An embedding key on every vector:** the model, the dimensions and a
   hash of the document prefix. Retrieval uses only today's key.
   `make reembed` embeds the others again, and until then they are found
   by keyword only, reported as `keyword_only (no_current_vectors)`.
6. **Degrade, never fail.** When the question cannot be embedded within
   `EMBEDDING_TIMEOUT_S` (5 s), keyword search answers alone. The
   `RetrievalDegraded` alert fires when most searches do.
7. **Credentials are redacted before the prompt,** from alerts and
   runbooks alike. The prompt's rule against repeating them failed 2 runs
   in 110, and 2 in 200 worded more strongly. With redaction: 0 in 200.
   The model cannot repeat what it never saw.
8. **The judge sees the runbook sections, and new criteria are
   calibrated first** (ADR-0016): 13 labelled answers, 39 of 39 verdicts
   agreeing.

## Alternatives considered

- **A dedicated vector database** (Qdrant, Weaviate, OpenSearch): a second
  datastore, a second backup, a second copy of team membership to keep in
  step. At thousands of sections, pgvector is enough.
- **Vector search only:** it misses exact identifiers, and gives up the
  fallback when the embedding model fails.
- **Keyword search only:** measured, it missed a paraphrased question in
  the first 5 results, and ranked the right section first for only 63% of
  questions.
- **A distance cutoff:** see decision 4. It is model-specific, and has to
  be measured again for each model.
- **A reranker (cross-encoder)** over the fused candidates. Not yet: it
  would fix the one weakness measured in fusion (a confident wrong keyword
  match outranking the semantic winner), at the cost of another model on
  the critical path. It is the first improvement to measure.

## Consequences

- The database image changes: a release that brings it recreates the
  database container once. The data directory is the same, and so is the
  PostgreSQL build.
- Changing any embedding setting other than the query prefix needs `make
  reembed`. The key makes forgetting visible, not silent.
- An answer's quality now depends on retrieval, and it is measured
  separately: `python -m evals --target retrieval` (recall@k, MRR), apart
  from the answer evals.
- Every prompt carries up to `RAG_CONTEXT_CHUNKS` (4) sections, about 400
  words each at most. Measured through the service: first token p50 0.28 s
  with runbooks, against 0.21 s without.
