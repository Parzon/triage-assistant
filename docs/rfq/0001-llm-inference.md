# RFQ-0001: Hosted LLM inference for an internal incident triage assistant

**Reference:** RFQ-0001
**Issued by:** <Company> Engineering, on-call tooling
**Contact:** <name, role, email>, the only channel for questions
**Issued:** <date> · **Questions by:** issue + 7 days · **Responses by:** issue + 21 days · **Trial and evaluation:** issue + 22–35 days · **Decision by:** issue + 42 days

## 1. Purpose

<Company> requests quotations for **hosted large language model
inference**: chat completions, streamed, through an OpenAI-compatible
API, for an internal assistant that helps on-call engineers triage
alerts. Optionally, it also requests **text embeddings**, for a planned
feature that grounds answers in runbooks.

## 2. Background

The assistant is an internal web service.
- Engineers ask questions in plain language about their team's current
  alerts.
- The service sends the model:
  - fixed instructions;
  - up to 20 of the asker's alerts (300 characters each at most);
  - the question.
- It streams the answer back.

The model has no tools and takes no actions. Every change to the prompt
or the model is gated by an evaluation suite. That suite is also the
acceptance test in section 6.

The service calls models through any OpenAI-compatible endpoint. It
needs no vendor-specific SDK. Today it runs against an open-weights
model on internal hardware.

## 3. Scope of supply

**Included:**
- API access to one or more chat models suitable for this task;
- enterprise data-processing terms;
- support.

**Optional** (price separately):
- an embeddings model;
- private network connectivity;
- dedicated or provisioned capacity.

**Not included:**
- fine-tuning;
- consulting;
- hosting of the service itself.

## 4. Requirements

**Mandatory** (a response failing any one is not scored):

| # | Requirement | Show it by |
|---|---|---|
| M1 | An OpenAI-compatible Chat Completions API: `POST /v1/chat/completions` with `stream: true` (server-sent events) and token usage in the stream (`stream_options.include_usage`) | the API reference; the trial (section 6) |
| M2 | `max_tokens` honoured. `finish_reason` reported (`stop`, `length`) | the same |
| M3 | **Zero data retention** for prompts and completions (not stored beyond the request, not used for training or human review), in the contract | the contract clause; the DPA |
| M4 | Processing and storage in <region: EU / US / ...> | the data residency terms |
| M5 | A data processing agreement (GDPR Art. 28 or equivalent), with sub-processors listed | a draft DPA |
| M6 | Security attestations: SOC 2 Type II or ISO/IEC 27001, current | the report or certificate |
| M7 | API keys scoped per project, revocable, with a spending limit per project or key | console documentation |
| M8 | Encryption in transit (TLS 1.2 or later) | documentation |
| M9 | **Pinned model versions**: dated snapshots, with at least 90 days' notice before one is retired or changed | the model lifecycle policy |
| M10 | Throughput for the peak scenario (section 5), without rate-limit errors | quota terms; the trial |
| M11 | An availability SLA of at least 99.9% a month, with service credits | the SLA document |
| M12 | Incident notification: a public status page, and notice of incidents affecting the customer | the status page URL; the terms |

**Desirable** (scored, total 100):

| # | Requirement | Weight |
|---|---|---|
| D1 | Time to first token p95 under 1.5 s from <region>, for the prompt sizes in section 5 | 15 |
| D2 | Private connectivity (for example PrivateLink or VPC peering) | 10 |
| D3 | An embeddings endpoint (`/v1/embeddings`) under the same terms | 10 |
| D4 | Discounts for cached prompt prefixes: the instructions repeat on every request | 10 |
| D5 | Regional failover, or a second region under the same terms | 10 |
| D6 | Reasoning models with a controllable effort (`reasoning_effort`), priced transparently for reasoning tokens | 10 |
| D7 | Single sign-on for the vendor console, with audit logs | 10 |
| D8 | A named technical contact, and a support response within 4 hours for severity 1 | 15 |
| D9 | A model roadmap, and deprecation practices shown by past behaviour | 10 |

## 5. Volumes and assumptions

**Measured today**, with an open-weights model and the current prompt:
- **Prompt:** about 350 tokens per answer with a few alerts in the
  context. With the full 20 alerts, an estimated 850.
- **Output:** 85 tokens per answer on average, and up to 300 for a model
  that reasons before answering (reasoning tokens included).

**Assumptions:**
- 20 questions per engineer per on-call day;
- 10 engineers per team;
- 22 working days a month.

| Scenario | Answers | Input tokens | Output tokens |
|---|---|---|---|
| **Pilot** (1 team, 10 engineers) | 200 a day; ~4,400 a month | ≤ 0.2 M a day; ≤ 3.8 M a month | ≤ 0.06 M a day; ≤ 1.3 M a month |
| **General availability** (30 teams, 300 engineers) | 6,000 a day; ~132,000 a month | ≤ 5.1 M a day; ≤ 112 M a month | ≤ 1.8 M a day; ≤ 40 M a month |
| **Peak** (a major incident: 50 engineers, one question every 30 s each) | 100 a minute | ≤ 85,000 a minute | ≤ 30,000 a minute |

The totals are upper bounds: they assume every prompt carries the full
20 alerts, and every answer the maximum reasoning. Price the upper
bounds. Optional embeddings (D3): about 5,000 runbook sections of ~300
tokens, re-embedded when they change, plus one query embedding per
answer.

## 6. Acceptance test

Shortlisted vendors provide trial API keys for two weeks. <Company>
runs its evaluation suite against each candidate model, through the
vendor's endpoint, with the vendor's recommended settings:

| Test | Pass |
|---|---|
| **Safety cases:** prompt injection, isolation between teams; 10 runs per case | 100% of runs |
| **Quality cases:** grounding, refusals; 10 runs per case | ≥ 80% of cases pass every run |
| **Instruction leak:** an alert asking for the instructions, 200 runs | 0 leaks |
| **Latency**, from <region> | time to first token p95 ≤ 3 s (M), ≤ 1.5 s (D1) |
| **Throughput** | the peak scenario for 10 minutes, with no rate-limit errors |

Results are shared with the vendor on request. The suite's method is
public (<link to the evals chapter>). Its cases are not sent in
advance.

## 7. Pricing format

Fill one row per model offered, and one table per region, if prices
differ:

| Model (dated version) | Input, per 1 M tokens | Cached input, per 1 M | Output, per 1 M (reasoning included?) | Minimum commitment | Rate limits included (tokens and requests a minute) |
|---|---|---|---|---|---|

Then:
- **the total monthly cost** for the pilot and the general-availability
  scenarios (section 5), all fees included;
- **the cost of the peak scenario's capacity**, if it needs a higher
  tier or provisioned throughput;
- **optional items:** embeddings (per 1 M tokens), private
  connectivity, support tiers;
- **volume or commitment discounts**, and the terms that trigger them;
- **price changes:** how much notice, and whether prices are fixed for
  the contract's term.

## 8. Evaluation

1. **The mandatory gate:** M1–M12, pass or fail.
2. **Scoring**, for the responses that pass:

| Criterion | Weight |
|---|---|
| Acceptance test results (section 6) | 30% |
| Data protection and compliance beyond the mandatory terms | 20% |
| Total cost at general availability, including the peak capacity | 20% |
| Desirable requirements D1–D9 | 20% |
| References from comparable customers | 10% |

## 9. Commercial terms

- A quotation must remain valid for 90 days from the response deadline.
- This request is not a commitment to buy. No obligation exists until a
  contract is signed.
- The contents of this request, and of the trial, are confidential.
- The contract will include the DPA (M5), the SLA (M11), and the data
  retention clause (M3).

## 10. How to respond

Send a single PDF to <contact> by the response deadline, with:
1. the requirement tables of section 4, with an answer and evidence for
   each row;
2. the pricing tables of section 7;
3. the trial arrangements for section 6;
4. two references;
5. the draft DPA and SLA.

Questions go to <contact> by the questions deadline. Questions and
answers are shared with all vendors, anonymised.
