# triage-assistant: overview

One page for anyone deciding about this project, technical or not.

## What it is

An assistant for on-call engineers.
- Alerts from monitoring systems land in one place, owned by teams.
- An engineer asks in plain language what is happening, and an AI
  model answers, streaming, from **only the alerts that engineer is
  allowed to see**.
- People sign in with the company's identity provider, and their roles
  come from it.

It is also a **reference template**. It is the way this team builds any
AI service: another team copies it, replaces the domain (alerts), and
keeps everything else:
- sign-in and permissions;
- deployment;
- monitoring;
- testing;
- the measurement of AI quality.

## Who it is for

| Who | What they get |
|---|---|
| On-call engineers | the right alert first, the likely cause (a recent deploy to the same service), and a plain "the alerts do not say" instead of a guess |
| Team leads | per-team ownership: responders add alerts, admins manage them, and nobody sees another team's incidents |
| Security and compliance | company sign-in; access enforced twice, in the service and in the database; the model never given data the asker could not read; no secrets in code |
| Platform and infrastructure | three container images, one database, measured capacity, a documented handoff ([infrastructure Q&A](handbook/infrastructure-qa.md)) |
| Other engineering teams | a working starting point, and a documented way to adopt it ([using this template](handbook/using-this-template.md)) |

## Where it stands (v0.4.0, September 2026)

✅ **Built and measured**, on one production-shaped host:

| | Measured |
|---|---|
| Tests | 134 unit, 95 integration (real database, identity provider, pooler), 49 UI, 12 browser end-to-end; 94% line and branch coverage |
| Capacity | ~1,000 signed-in reads per second, or 500 simultaneous streamed answers, on 2 CPUs |
| Deploys | 155,659 requests during a deploy, 0 failed; rollbacks work across database changes |
| Failure drills | 22 injected faults (database frozen, identity provider down, model provider erroring...), each with what users saw and how it recovered |
| AI quality | 14 test cases against a real model: grounding, refusals, prompt injection, isolation between teams. The 11 answer cases pass 10 runs in 10; all 14 pass through the whole service. The model printed its instructions 0 times in 200 attempts, down from 5 in 200 before the last prompt fix |
| Releases | 4 releases, for Intel and ARM servers, each smoke-tested after publishing |

📘 **Not yet:**
- a cloud server with a real domain;
- the company's identity provider;
- a paid model provider;
- real users.

[Going to production](handbook/production.md) lays these out as five
stages, each with a measurable exit.

## How it works

```
browser ─HTTPS─► edge (TLS) ─► web server ─► api ─► database (alerts, teams, sessions)
                                              ├───► identity provider (sign-in)
                                              └───► AI model (any OpenAI-compatible provider)
```

The model sees the question and the asker's recent alerts, and nothing
else. It has no tools: it cannot run commands or change data. The worst
a malicious alert can do is distort one answer, to someone who could
read that alert anyway.

## Risks, and what limits them

| Risk | Limited by |
|---|---|
| The AI answers wrongly or is manipulated by alert text | measured with test cases before each change, calibrated automatic grading, safety cases that must pass 100%; no tools; answers shown as text only |
| One team sees another's data | access checks in the service, plus database-level row security, both tested |
| Alert text sent to an external AI provider | provider terms with zero data retention ([RFQ-0001](rfq/0001-llm-inference.md)), or a model in the company's own cloud |
| Cost runaway | per-user rate limits, output limits, a cost panel; a provider budget alarm is stage 3 |
| One server is a single point of failure | acceptable for a pilot; a second host or a managed platform is stage 5 |

## The documents

| Question | Document |
|---|---|
| What are we building, and why? | [PRD-0001](prd/0001-triage-assistant.md) |
| Is the architecture sound? | [ARD-0001](ard/0001-triage-assistant.md), the architecture review |
| What should come next? | [RFC-0001](rfc/0001-answers-grounded-in-runbooks.md): answers that cite the team's runbooks |
| What do we ask model vendors for? | [RFQ-0001](rfq/0001-llm-inference.md) |
| Why was each technical decision made? | [the ADRs](adr/) (16) |
| How is it built, run and repaired? | [the guide](../gold_standard_development_guide.md) and the [handbook](handbook/) |
