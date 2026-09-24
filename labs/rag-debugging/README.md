# RAG debugging lab

When the assistant answers badly about runbooks, the fault is in one of
the pipeline's stages:

```
runbook -> ingestion -> parsing -> chunking -> embedding -> retrieval ->
fusion/ranking -> context -> prompt -> generation -> answer -> evaluation
```

This lab puts one fault in each stage, one at a time, so you learn what
each looks like from the outside. **Do each exercise before reading
[answers.md](answers.md):** it holds what was observed here, the
diagnosis, and what the product does about it.

Each exercise takes a few minutes. Exercises 5, 6, 9 and 10 need a real
model. The retrieval exercises need real embeddings: the mock's are a bag
of words and cannot tell a paraphrase apart.

## Setup, once

1. A local model and embeddings (the AI engineering chapter, "A real model
   on your machine"). In `.env`, add `ollama` to `COMPOSE_PROFILES`, then:

   ```
   LLM_BASE_URL=http://ollama:11434/v1
   LLM_API_KEY=ollama
   LLM_MODEL=gpt-oss:20b
   LLM_REASONING_EFFORT=low
   EMBEDDING_MODEL=nomic-embed-text
   EMBEDDING_QUERY_PREFIX="search_query: "
   EMBEDDING_DOCUMENT_PREFIX="search_document: "
   ```

2. Pull the models and start the stack:

   ```
   make ollama-pull m=gpt-oss:20b
   make ollama-pull m=nomic-embed-text
   make up
   ```

3. Save the eval corpus (`apps/api/evals/runbooks/`) into a lab team:

   ```
   labs/rag-debugging/lab seed
   ```

The helper, `labs/rag-debugging/lab`, runs inside the api container. It has
four commands:

| Command | Shows |
|---|---|
| `lab sections <file> [--max-words N] [--naive]` | how a runbook splits into sections |
| `lab search "<question>" [--mode keyword\|semantic\|hybrid] [--k N]` | what retrieval returns: each retriever's rank, the fused score, the cosine distance |
| `lab ask "<question>"` | what the assistant was given (alerts, sections, retrieval mode), its answer, and what it cited |
| `lab unseed` | removes the lab's runbooks |

## The method: walk backwards from the answer

Stop at the first stage that is wrong. Everything after it only looks
wrong.

1. **Evaluation.** Is the check right? Read the answer before believing
   the pass rate.
2. **Generation.** Did the model have the right section and misuse it?
   `lab ask` shows what was in context and what it cited.
3. **Prompt.** Does an instruction misfire, or conflict with another?
4. **Context.** Was the right section retrieved, then cut (too few
   sections) or buried?
5. **Fusion and ranking.** Did one retriever find it, and fusion rank it
   down?
6. **Retrieval.** Which retriever finds it (`--mode keyword` or
   `--mode semantic`)? At what distance?
7. **Chunking.** Is the answer split across sections, or diluted in a huge
   one?
8. **Parsing.** Did the splitter cut at the wrong place?
9. **Ingestion.** Is the runbook stored, current, and embedded the way
   today's settings make vectors?

## The exercises

**1. A paraphrase (retrieval).** Run:

```
lab search "RAM usage climbs all day and never comes back down" --mode keyword --k 3
```

Then run it with `--mode semantic`, then with `--mode hybrid`. Which
retriever finds "Find the leak"? Why does keyword search return rollback
runbooks? Where does hybrid rank the right section, and why?

**2. An exact identifier (retrieval).** Run
`lab search "how do I turn on checkout.fallback_provider?"` in both modes.
Which retriever would you trust for flag names, error codes and
hostnames?

**3. Headings inside a code block (parsing).** Create
`apps/api/evals/runbooks/payments/zz-lab.md` with a procedure whose
fenced shell block holds `# comments`. Then run:

```
lab sections payments/zz-lab.md --naive
lab sections payments/zz-lab.md
```

What would the model read if only the second naive section were
retrieved? Delete the file afterwards: it is part of the benchmark's
corpus.

**4. Sections too small (chunking).** Run
`lab sections platform/disk-full.md --max-words 12`. Find the sentence
that starts with "Never". What does each fragment say on its own?

**5. Too few sections in the prompt (context).** Recreate the api with
`RAG_CONTEXT_CHUNKS=1`, for example `RAG_CONTEXT_CHUNKS=1 docker compose
up -d api` with your `.env` as above. Then run
`lab ask "db-1's disk is filling up. What do I do?"`, and again with the
default of 4. Compare the steps with the runbook. Which ones are
invented? `lab search` with the same question shows why that section came
first.

**6. A question no runbook covers (coverage).** Run
`lab search "How do I rebalance a Kafka consumer group?" --k 4`, then
`lab ask` with the same question. What distances do the top sections
have? What does the model do with four irrelevant sections? Would a
distance cutoff help with nomic-embed-text? The AI engineering chapter
has the measured distances.

**7. The embedding settings change (ingestion).** Recreate the api with
`EMBEDDING_DOCUMENT_PREFIX="document: "`. Run
`lab search "RAM usage climbs all day"` in semantic mode, then hybrid.
Read the mode line. Put the prefix back, run the search again, then
`make reembed`, and search once more.

**8. A team filter behind a vector index (multi-tenant retrieval).** Run
`make rag-overfiltering-lab`: 50,000 sections in 100 teams, searched as a
viewer of one team, four ways. Read each plan's `rows=`. Why does the
first find almost nothing? What does configuration 4 do differently?

**9. One failure is not a cause (prompt).** The first prompt with
runbooks answered "There are no alerts." once in 10 runs of
`refusal-off-topic`, with an alert in the list. The suspect: an empty
runbook list was written `(none)`, like an empty alert list, and the rule
"if the list says (none), say that there are no alerts" might have fired on
the wrong list.

In `app/triage.py`, put the suspect back:
- change `(no runbook sections)` in `build_messages` to `(none)`;
- change "If the alert list says (none)" in the prompt to "If the list says
  (none)".

Hot reload applies it. Then run:

```
make evals a="--case refusal-off-topic --repeat 30"
```

How many runs say "There are no alerts."? What would you need to see to
call the marker the cause? Revert both changes.

**10. A check that is wrong (evaluation).** In `app/triage.py`, change
`_CITATION` to `re.compile(r"\[R(\d+)\]")`, then run:

```
make evals a="--case rag-rollback --case rag-disk-steps --repeat 10"
```

Read the answers the check failed. Is the model wrong, or the check?
Revert the change.

When you are done: `lab unseed`.
