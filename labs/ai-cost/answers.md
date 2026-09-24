# AI cost lab: what was measured

Measured on 2026-09-24:
- gpt-oss:20b at reasoning effort low, and nomic-embed-text, through Ollama
  on one RTX 6000 Ada;
- prompt v6; team "lab" with 25 alerts (20 reach the prompt) and 4 runbook
  sections per question.

Prices are list prices read that day ([Anthropic](https://platform.claude.com/docs/en/about-claude/pricing),
[OpenAI](https://developers.openai.com/api/docs/pricing)). Token counts are
gpt-oss's own. Another model tokenizes the same text differently, and
answers at its own length, so the dollar figures are estimates.

## 1. Estimate first, then measure

`lab measure`, 22 questions, all answered:

| Tokens per question | p50 | p95 | billed, mean |
|---|---|---|---|
| prompt | 1,318 | 1,386 | 1,322 |
| answer (hidden reasoning included) | 172 | 319 | 168 |

300 people × 5 questions × 22 working days is 33,000 questions a month:

| Model (per million tokens: input / output) | Per 1,000 questions | A month |
|---|---|---|
| GPT-5 mini ($0.25 / $2.00) | $0.67 | ~$22 |
| Claude Haiku 4.5 ($1 / $5) | $2.16 | ~$71 |
| Claude Sonnet 5 ($2 / $10) | $4.33 | ~$143 |

**The prompt is the bigger half of the bill.** Output costs five times more
per token, but the prompt has eight times more tokens: on Haiku, $1.32 of
the $2.16.

**At this volume the model bill is small.** One engineer-day costs more
than a month of Haiku. The bill becomes a design question with volume,
with agents (many calls per task), or with long contexts.

## 2. Where do the tokens go?

`lab tokens "db-1's disk is at 96%, how do I get space back?"`:

| Part | Tokens |
|---|---|
| instructions + question | 401 |
| 20 alerts | 685 |
| 4 runbook sections | 304 |
| **the whole prompt** | **1,390** |
| the answer | 232 (15 reasoning chunks, 207 answer chunks) |

**The alerts are half the prompt.** `CHAT_CONTEXT_ALERTS` (20) controls
them, and `MAX_ALERT_CHARS` cuts each to 300 characters. Lowering it saves
tokens, and loses what the older alerts say: often the deploy that started
the incident. Measure it with the evals before shipping.

**Provider prompt caching would not help this prompt.** The part that stays
the same between two questions is the instructions, about 400 tokens. The
alerts change with every new alert, and the sections with every question.
The minimum cacheable prefix is 1,024 tokens on GPT-5.6 and Claude Sonnet 5,
and 4,096 on Claude Haiku 4.5. Caching pays where a long, stable prefix is
read again and again: a large system prompt, a document asked about many
times, a long conversation.

## 3. The bill tripled

`lab up LLM_REASONING_EFFORT=high`, then `lab measure`:

| | Effort low | Effort high |
|---|---|---|
| answered | 22 of 22 | **9 of 22** |
| failed | none | 13, `llm_empty_answer` |
| answer tokens billed per question | 168 | **694** (4.1×) |
| per 1,000 questions on Claude Haiku 4.5 | $2.16 | $4.79 (2.2×) |

The 13 failures spent the whole output budget (800 tokens) thinking and
wrote nothing. The provider bills them all the same. A single question
asked directly showed the same: 82 output tokens at low effort, 870 at
high.

**What would have told you:**
- tokens per answer: `rate(llm_tokens_total{kind="completion"})` over
  `rate(llm_requests_total)`, up 4×;
- `llm_requests_total{outcome="llm_empty_answer"}`, up from zero.

**What would not:** the request rate and the prompt tokens did not move. A
cost alert on request volume alone stays silent.

**The method** for any cost jump: cost = requests × tokens per request ×
price per token. Find which factor moved, then look at what changed in
it:
- **requests:** a client in a loop, retries;
- **tokens per request:** context size, reasoning effort, the output
  limit;
- **price:** the model, the provider, a cache that stopped hitting.

The lowest-risk fix is usually to undo the change (a setting). Next, cap
the damage (output limit, rate limit), then optimise with evals.

## 4. A semantic cache?

`lab cache`, over the 22 benchmark questions (231 pairs, of which 1 pair
shares its answer):

| Distance cutoff | Served from the cache | Right | Wrong |
|---|---|---|---|
| 0.25 or less | 0 | 0 | 0 |
| 0.30 | 3 | 1 | **2** |

**The closest pair is a wrong hit.** "db-1's disk is at 96%, how do I get
space back?" and "cleanup did not help, the disk is still over 90%" sit at
0.261. The second is the follow-up to the first, and needs the next step.
These questions were written to be distinct (a retrieval benchmark, not
traffic), so real traffic would show more repeats. The wrong-hit problem
would remain.

**Two more reasons it is dangerous here:**
- **the answer depends on the live alerts:** a cached answer is stale once
  a new alert arrives;
- **the answer depends on who asks:** it is built from the asker's teams'
  alerts and runbooks. Served to someone else, it leaks another team's data.
  A cache key that includes the access scope and the context's version
  leaves almost nothing to hit.

## 5. Self-hosting

`lab throughput`: gpt-oss:20b answering this lab's prompt, measured warm
for 60 seconds each. The cost uses the cloud price of the same class of GPU
(an L40S: AWS g6e.xlarge, on demand, $1.861 an hour):

| Ollama slots | At a time | Output tokens/s | Answers/s | Per million output tokens | Per 1,000 answers |
|---|---|---|---|---|---|
| 1 | 1 | 176 | 0.53 | $2.93 | $0.98 |
| 1 | 4 or 8 | 176–177 (they queue) | 0.54–0.56 | $2.92 | $0.93–0.96 |
| 4 | 4 | 365 | 1.17 | $1.41 | $0.44 |
| 4 | 8 | 372 | 1.20 | $1.39 | $0.43 |
| 8 | 8 | 401 | 2.03 | $1.29 | $0.25 |
| 8 | 16 | 478 | 1.51 | $1.08 | $0.34 |

Answers per second varies with how long the sampled answers happen to be;
output tokens per second is the steadier number.

**The extra slots bought throughput, and cost each user speed.** With four
at a time, each answer streamed at about 91 tokens/s instead of 176. Eight
slots used 38 GB of GPU memory, most of it for their key-value caches.

**When self-hosting wins:** only when the GPU is kept busy. These costs
assume it never idles. At 33,000 questions a month (exercise 1):
- one GPU on demand around the clock costs ~$1,358 a month, which is ~$41
  per 1,000 questions;
- that is 60 times GPT-5 mini's $0.67, and 19 times Haiku's $2.16;
- break-even against GPT-5 mini is about 2 million questions a month, near
  40% of what eight slots can serve.

That leaves out the people who run it, and it assumes a 20B model answers
as well as the one it replaces, which the evals must show first. Where
self-hosting wins for other reasons (data may not leave, or a sustained high
load), batching is what makes it affordable.
