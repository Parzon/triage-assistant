# AI cost

What an answer costs here, where its tokens go, and which settings move the
bill. Measured with gpt-oss:20b on one local GPU; prices are list prices
read on 2026-09-24. The exercises and the full numbers are in
[labs/ai-cost](../../labs/ai-cost/README.md). ✅ = measured here,
📘 = documented practice, not built here.

## The cost model

```
cost = requests × tokens per request × price per token
```

Split tokens into input and output: output is priced 5 to 8 times higher.
Hidden reasoning is output. Then add what is not tokens:
- embeddings, each question and every saved runbook section;
- the vector store and its storage;
- the observability stack;
- for a self-hosted model, the GPU hours whether it is busy or not;
- the people who run it.

## Measured ✅

| | |
|---|---|
| prompt per question | 1,318 tokens at p50: instructions 401, 20 alerts 685, 4 runbook sections 304 |
| answer per question (effort low) | 172 tokens at p50, 319 at p95; hidden reasoning included |
| per 1,000 questions, list prices | GPT-5 mini $0.67; Claude Haiku 4.5 $2.16; Claude Sonnet 5 $4.33 |
| 300 users × 5 questions a working day | ~$22 a month on GPT-5 mini, ~$71 on Haiku |
| reasoning effort high instead of low | 4.1× the answer tokens billed, and 13 of 22 questions **unanswered**: the whole output limit spent thinking |
| self-hosted, gpt-oss:20b on an L40S-class GPU ($1.861/h rented) | $2.93 per million output tokens with one request at a time; $1.08–1.41 with 4–8 batched |

**The prompt is most of the bill** (on Haiku, $1.32 of $2.16 per 1,000
questions), and the alerts are half the prompt.

## The levers, for this service

| Lever | Here |
|---|---|
| **Context size** | the biggest: `CHAT_CONTEXT_ALERTS` (20), `RAG_CONTEXT_CHUNKS` (4), each alert cut to 300 characters. Fewer alerts in context saves tokens and loses what older alerts say (the deploy before the incident): measure with the evals |
| **Output and reasoning** | `LLM_MAX_OUTPUT_TOKENS` (800) caps the worst case. `LLM_REASONING_EFFORT` multiplies output: keep it low unless the evals show a gain worth 4× |
| **Volume** | `CHAT_RATE_LIMIT` per user (10 a minute). 📘 A per-user or per-team token budget would bound a runaway client in tokens, not only in requests |
| **A smaller or cheaper model** | the biggest lever of all on price, from $4.33 to $0.67 per 1,000. Only with the evals run against it before and after (ADR-0016) |
| **Provider prompt caching** | does **not** apply to this prompt. The stable part is ~400 tokens (the instructions), under the 1,024-token minimum (GPT-5.6, Claude Sonnet 5; 4,096 for Haiku 4.5), and the alerts change with every new alert. It pays for long, stable prefixes read again and again |
| **Semantic caching of answers** | wrong here. The closest pair of benchmark questions (distance 0.261) needs different answers. An answer depends on the live alerts and on who asks, so a cached one is stale, or another team's |
| **Batch APIs** | half price for work that can wait hours (Anthropic, OpenAI) 📘. Not this interactive chat; a nightly summary or re-embedding, yes |
| **Self-hosting** | cheaper per token only when the GPU stays busy. At 33,000 questions a month, one rented GPU costs ~$41 per 1,000 questions, 60× GPT-5 mini. Break-even is ~2 million a month. Batching halves its cost per token, and slows each answer |

## When the bill jumps

1. **Which factor moved?** Requests (`llm_requests_total`), tokens per
   request (`llm_tokens_total` ÷ requests, prompt and completion apart), or
   price (the model: `app_info`, the `model` label).
2. **What changed in it?**
   - requests: a client in a loop, retries;
   - prompt tokens: context settings, a prompt change;
   - completion tokens: reasoning effort, the output limit;
   - price: the model or provider, a cache that stopped hitting.
3. **Lowest risk first:** undo the change; cap the damage (output limit, rate
   limit); then optimise, with evals.

Measured: raising the reasoning effort moved only one factor, completion
tokens per request, 4×. Failures moved with it (`llm_empty_answer`). An
alert on request volume would have stayed silent.

On the dashboard, "Estimated model cost / hour" multiplies the token rate
by prices set as dashboard variables: set them to your model's.

## Not built 📘

- Prices as settings, and a cost metric that alerts can use.
- Token budgets per user or team (a runaway client is bounded in requests
  today, not in tokens).
- Alerts on each factor (volume, tokens per request), against last week.

Worth building when the bill is large enough to matter. At the measured
volumes it is not yet.
