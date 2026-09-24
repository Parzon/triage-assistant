# AI cost lab

What one answer costs, where its tokens go, and which levers move the bill.
Everything here runs on the local models; the prices are list prices, read
on the date in `lab.py`.

**Do each exercise before reading [answers.md](answers.md).**

## Setup

The RAG lab's setup first: the local models, and its runbooks in team "lab"
([labs/rag-debugging](../rag-debugging/README.md), "Setup, once").

```
labs/ai-cost/lab up      # the dev api on gpt-oss:20b and nomic-embed-text
labs/ai-cost/lab seed    # 25 alerts in team "lab": a realistic prompt
```

`lab down` puts the dev api back on `.env` when you are done.

| Command | Does |
|---|---|
| `lab tokens "<question>"` | one question's prompt, part by part (instructions, alerts, runbook sections), and its answer's tokens |
| `lab measure [n]` | n benchmark questions through the api: tokens per answer, tokens billed (failed answers included), cost per 1,000 questions at list prices |
| `lab up VAR=value...` | the dev api with a setting changed, e.g. `LLM_REASONING_EFFORT=high` |
| `lab cache` | would a semantic cache have served the right answer? |
| `lab throughput <n> [seconds]` | the model server alone, n answers at a time: output tokens per second, and the cost per million on a rented GPU |

## The exercises

**1. Estimate first, then measure.** A 2,000-engineer organisation; say 300
of them ask the assistant 5 questions a working day. Estimate the model's
monthly bill on Claude Haiku 4.5 and on GPT-5 mini: requests a month ×
tokens per request × price per token. Write down your tokens-per-request
guess, then run `lab measure` and redo the estimate. Which half of the
bill is bigger, the prompt or the answer? Why, when output costs five
times more per token?

**2. Where do the tokens go?** `lab tokens "db-1's disk is at 96%, how do I
get space back?"`. Which part of the prompt is the biggest? Which setting
controls it, and what would you lose by lowering it? Would provider prompt
caching help this prompt? (Look up the minimum cacheable length, and which
part of the prompt stays the same between two questions.)

**3. The bill tripled.** `lab up LLM_REASONING_EFFORT=high`, then `lab
measure`. What happened to the tokens billed per question, and to the
answers? Which metric would have told you, and which would not? Then `lab
up` to go back.

**4. A semantic cache?** `lab cache`. Which pair of questions is the
closest, and should they share an answer? Name two more reasons why a cache
of answers is dangerous for this assistant in particular.

**5. Self-hosting.** `lab throughput 1`, then `lab throughput 4`. Then give
Ollama four slots and repeat:

```
OLLAMA_NUM_PARALLEL=4 docker compose up -d --wait ollama
labs/ai-cost/lab throughput 4
docker compose up -d --wait ollama     # back to one slot
```

What did the extra slots buy, and what did they cost each user? When is
self-hosting cheaper than GPT-5 mini, and when is it far more expensive?
