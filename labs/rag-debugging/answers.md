# RAG debugging lab: what was observed

Measured on 2026-09-24:
- gpt-oss:20b, reasoning effort low, prompt v6;
- nomic-embed-text with its prefixes;
- one RTX 6000 Ada, through Ollama.

Your numbers will differ run to run: the model samples. The diagnoses
should not.

## 1. A paraphrase

| Mode | Top 3 |
|---|---|
| keyword | Checkout error rate high > Recent changes; Roll back a deploy; Roll back a deploy > Roll back |
| semantic | **Memory pressure and OOM kills > Find the leak** (distance 0.360); Stop the bleeding; the runbook's intro |
| hybrid | Roll back a deploy (0.0315); **Find the leak** (0.0313); Roll back a deploy > Roll back |

- **Keyword search:** after stemming and stop words, "comes back down"
  leaves `back`, which matches "Roll back". Keyword search matched a word,
  not the meaning.
- **Semantic search** finds the leak first.
- **Hybrid** puts it second. Reciprocal rank fusion gives both lists the
  same weight:
  - the rollback runbook (keyword 2nd, semantic 5th) scores
    1/62 + 1/65 = 0.0315;
  - the right section (keyword 7th, semantic 1st) scores
    1/67 + 1/61 = 0.0313.

**Diagnosis:** fusion and ranking. Each retriever did its job; the merge
let a confident wrong keyword match win.

**What would fix it** 📘:
- give the semantic list more weight;
- or, better, rerank the fused candidates with a cross-encoder, which
  reads the question and each section together.

This pipeline has no reranker yet. The benchmark shows the cost:
recall@1 is 0.79 hybrid against 0.84 semantic, with nomic-embed-text.

## 2. An exact identifier

Both retrievers rank "Checkout error rate high > Check the payment
provider" first. Keyword search finds it as its only hit; semantic search
finds it at distance 0.194.

Here both work. Keyword search is the one to trust for rare exact tokens
(error codes, flag names, hostnames): an embedding blurs a string it has
never seen into its neighbours.

## 3. Headings inside a code block

| Splitter | Sections |
|---|---|
| naive | 3: "Restart safely" (7 words), "stop consuming new messages" (2 words: `make refunds-pause`), "restart the worker" (13 words) |
| the service's | 1: "Restart safely" (31 words), the whole procedure |

The naive third section holds the restart command and "Resume
consumption only after the worker reports healthy". It has lost "drain the
queue first". Retrieved alone, it gives a procedure without its first
step.

**Diagnosis:** parsing. The service skips headings inside fenced code
blocks (`split_sections`, tested).

## 4. Sections too small

At 12 words, "Free space" becomes four fragments. The safety warning is
cut in two:
- one fragment ends with "... `logrotate -f /etc/logrotate.conf`. Never";
- the next reads "the data directory by hand."

Neither says "never delete files in the data directory by hand".

**Diagnosis:** chunking. Too small, and a step loses its condition; too
large, and one section dilutes the match. The service splits at headings
first, and at paragraphs only past 300 words.

## 5. Too few sections in the prompt

Search ranks, for "db-1's disk is filling up. What do I do?":

| # | Section | Keyword rank | Semantic rank |
|---|---|---|---|
| 1 | Expand the volume | 2 | 1 |
| 2 | (the runbook's intro) | 1 | 3 |
| 3 | Check what is using space | 4 | 2 |
| 4 | **Free space** | 5 | 5 |

- **With 1 section** (Expand the volume only), the answer's step 1 was
  "First free up space on the host (e.g., delete old logs, unused files)".
  The model **invented** it from the phrase "after freeing space", without
  the runbook's conditions: back up before deleting WAL archives, never
  delete in the data directory. It cited [R1], with full authority.
- **With 4**, the answer gave every step in order, the conditions
  included, each cited.

**Diagnosis:** context construction, made worse by ranking. The most
important section ranked last for this wording. A runbook's sections
depend on each other.

**What would fix it** 📘: retrieve by section, but give the model the
neighbouring sections or the whole runbook ("small-to-big" retrieval);
and rerank.

## 6. A question no runbook covers

The top four sections for the Kafka question:
- distances 0.472–0.544;
- one keyword match, on "queue" (Refunds stuck in pending).

The answer: "The current alerts do not provide any information relevant
to rebalancing a Kafka consumer group." It cited nothing.

The model handled the irrelevant context well here: this is what
`rag-no-runbook-applies` measures, and it passed all its runs.

A distance cutoff would not help with nomic-embed-text. On the benchmark:
- relevant sections reached distance 0.43;
- negative questions' best matches started at 0.41.

The two overlap: a cutoff that drops the negatives drops real answers too.
It separates cleanly with embeddinggemma and its prompts, so cutoffs are
per model, and measured again whenever the model changes.

## 7. The embedding settings change

- **After the document prefix changed:**
  - `--mode semantic` returned no rows. Every stored vector carries the
    old embedding key, so none is comparable with the question's.
  - Hybrid said `mode keyword_only (embedding: no_current_vectors)`.
- **After `make reembed`** ("embedded again 8 of 8 runbook(s)"), semantic
  search found "Find the leak" again, at 0.370.

**Diagnosis:** ingestion. The vectors were made one way and queried
another.

The lab found this one. Before the fix, hybrid mode said "hybrid" while
only keyword search contributed: a silent degradation. The search now
counts the semantic candidates, reports `keyword_only`, and
`RetrievalDegraded` alerts when most searches fall back.

## 8. A team filter behind a vector index

50,000 sections in 100 teams, as a viewer of one team (1% of the rows):

| Configuration | Found (of 20) | Rows discarded | Time |
|---|---|---|---|
| 1. HNSW, iterative scans off | **0** | 40 | 5.2 ms |
| 2. HNSW, iterative scans on | 20 | 3,032 | 39.6 ms |
| 3. no index, every row compared | 20 | 49,531 | 6.5 ms |
| 4. the service's query: `team_id = ANY(...)` | 20 | 0 (team index, then sort) | **0.8 ms** |

1. The approximate index returns its nearest 40 candidates (`ef_search`),
   and the team filter runs after the scan. With 1% of the rows the
   caller's, the 40 were all other teams'. The search found nothing, and
   nothing failed.
2. Iterative scans (pgvector 0.8+) keep going until 20 rows pass the
   filter: correct, but slow.
3. The exact scan is correct and faster: 500 rows are few to compare.
4. With the team condition as a plain `team_id = ANY(...)`, the planner
   reads the team's 500 rows by their index and sorts them exactly.
   Behind an OR - row-level security's `org_admin OR team_id = ANY(...)`,
   or a query's `:team_ids IS NULL OR ...` - it cannot, and walks the
   vector index.

**Diagnosis:** multi-tenant retrieval: the permission filter and the
approximate index work against each other.

**What the service does:** it sends the explicit filter, which is
correct and fast, and keeps iterative scans on for an org admin's
unfiltered searches.

## 9. One failure is not a cause

With both suspects back (the `(none)` marker, and "If the list says
(none)"), 0 of 30 runs said "There are no alerts.". With the marker alone,
also 0 in 30. The original failure was 1 in 10. Against 0 in 60, that is
not a significant difference: if both prompts failed alike, the one failure
would land in the first 10 of 70 runs with probability 10/70 = 0.14
(one-sided Fisher test).

To call the marker the cause, you would need it to reproduce at a rate you
can tell apart from the fixed prompt's: several failures in 30 to 100
runs, compared with Fisher's test. It did not.

**What stays:** the distinct marker. It is harmless, and removes an
ambiguity a model could trip on. The code comment says the cause was not
proven. The first version of that comment claimed it was measured: one
failure is an anecdote, and a note should say which it is.

## 10. A check that is wrong

With the pattern `\[R(\d+)\]`, 5 of 20 correct answers failed the
citation check:

| Case | Passed | How the failing answers cited |
|---|---|---|
| `rag-disk-steps` | 8 of 10 | `(R1)`; `【R1】` |
| `rag-rollback` | 7 of 10 | `【R1】`; a bare "R1"; `(R1)` |

Every one of them cited the right section, in a format the pattern did not
expect. The model was right, and the check was wrong in 25% of runs.

**Diagnosis:** evaluation. The service now reads any standalone `R<number>`
as a citation, and the unit tests list every style seen.

**The general lesson:** when a pass rate drops, read the failing answers
before changing the prompt. Here that happened three times:
- a citation pattern;
- a "cites nothing" check that failed an answer naming sections to reject
  them;
- judge criteria (the AI engineering chapter).
