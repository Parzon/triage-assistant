# triage-assistant: overview

One page for anyone deciding about this project, technical or not.

## What it is

An assistant for on-call engineers.
- Alerts from monitoring systems land in one place, owned by teams.
- An engineer asks in plain language what is happening, and an AI
  model answers, streaming, from **only the alerts that engineer is
  allowed to see**.
- For what to do, it gives the steps from the team's own runbooks, and
  names each section it used, so the engineer can check the source.
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
| On-call engineers | the right alert first, the likely cause (a recent deploy to the same service), the runbook's steps with their source, and a plain "the alerts do not say" instead of a guess |
| Team leads | per-team ownership: responders add alerts, admins manage them, and nobody sees another team's incidents |
| Security and compliance | company sign-in; access enforced twice, in the service and in the database; the model never given data the asker could not read; credentials removed before any model sees them; an audit trail of who wrote what the assistant reads, and what every answer was given, that the service itself cannot rewrite; no secrets in code |
| Platform and infrastructure | three container images, one database, measured capacity, a documented handoff ([infrastructure Q&A](handbook/infrastructure-qa.md)) |
| Other engineering teams | a working starting point, and a documented way to adopt it ([using this template](handbook/using-this-template.md)) |

## Where it stands (v0.6.0, September 2026)

✅ **Built and measured**, on one production-shaped host:

| | Measured |
|---|---|
| Tests | 181 unit, 115 integration (real database, identity provider, pooler), 52 UI, 12 browser end-to-end; 93% line and branch coverage |
| Capacity | ~1,000 signed-in reads per second, or 500 simultaneous streamed answers, on 2 CPUs |
| Deploys | 155,659 requests during a deploy, 0 failed; rollbacks work across database changes |
| Failure drills | 22 injected faults (database frozen, identity provider down, model provider erroring...), each with what users saw and how it recovered |
| AI quality | 19 test cases against a real model: grounding, refusals, prompt injection, isolation between teams, answers from runbooks. The 15 answer cases pass 10 runs in 10, but one at 9 in 10, within chance. Through the whole service, 18 pass 3 runs in 3; one runbook answer left out a step once. The model printed its instructions 2 times in 600 attempts, under the 1.5% limit set for it |
| Runbook search | the section that answers is in the top 5 for all 19 test questions, and first for 15; about 10 ms a search |
| Explaining an answer | every answer can be traced: what it was given (which runbook sections, which prompt version, which model), and where its time went, with no question or answer text kept. Four silent misconfigurations were each found from their traces alone |
| Releases | 6 releases, for Intel and ARM servers, each smoke-tested after publishing, and deployed on a production-shaped host with no failed request |

📘 **Not yet:**
- a cloud server with a real domain;
- the company's identity provider;
- a paid model provider;
- real users.

[Going to production](handbook/production.md) lays these out as five
stages, each with a measurable exit.

## How it works

```
browser ─HTTPS─► edge (TLS) ─► web server ─► api ─► database (alerts, runbooks, teams, sessions)
                                              ├───► identity provider (sign-in)
                                              └───► AI model (any OpenAI-compatible provider)
```

The model sees the question, the asker's recent alerts, and the
sections of the asker's runbooks that match the question, with known
credential formats removed. Nothing else. It has no tools: it cannot run commands or change data. The worst
a malicious alert can do is distort one answer, to someone who could
read that alert anyway.

## Risks, and what limits them

| Risk | Limited by |
|---|---|
| The AI answers wrongly or is manipulated by alert text | measured with test cases before each change, calibrated automatic grading, safety cases that must pass 100%; no tools; answers shown as text only |
| One team sees another's data | access checks in the service, plus database-level row security, both tested |
| Alert and runbook text sent to an external AI provider | provider terms with zero data retention ([RFQ-0001](rfq/0001-llm-inference.md)), or a model in the company's own cloud; known credential formats removed before anything is sent |
| A runbook's wrong or dangerous step repeated by the assistant | every step names its section and the section's date; runbooks stay the teams' own; every version's author, and every answer's sources, are on record |
| Cost runaway | per-user rate limits, output limits, a cost panel; a provider budget alarm is stage 3 |
| One server is a single point of failure | acceptable for a pilot; a second host or a managed platform is stage 5 |

## The documents

| Question | Document |
|---|---|
| What are we building, and why? | [PRD-0001](prd/0001-triage-assistant.md) |
| Is the architecture sound? | [ARD-0001](ard/0001-triage-assistant.md), the architecture review |
| How was a feature proposed, and what did it cost? | [RFC-0001](rfc/0001-answers-grounded-in-runbooks.md): answers that cite the team's runbooks (accepted and built, with the costs measured) |
| What should come next? | "Not done yet" in [the guide](../gold_standard_development_guide.md#not-done-yet) |
| What do we ask model vendors for? | [RFQ-0001](rfq/0001-llm-inference.md) |
| Why was each technical decision made? | [the ADRs](adr/) (18) |
| How is it built, run and repaired? | [the guide](../gold_standard_development_guide.md) and the [handbook](handbook/) |
