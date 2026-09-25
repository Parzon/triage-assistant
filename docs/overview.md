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
| Platform and infrastructure | three container images, one database, measured capacity, a documented handoff ([production](handbook/production.md#handing-it-to-an-infrastructure-team)) |
| Other engineering teams | a working starting point, and a documented way to adopt it ([using this template](handbook/using-this-template.md)) |

## Where it stands (v0.8.0, September 2026)

✅ **Built and measured**, on one production-shaped host:

| | Measured |
|---|---|
| Tests | 259 unit, 131 integration (real database, identity provider, pooler), 54 UI, 12 browser end-to-end; 92% line and branch coverage |
| Capacity | ~1,000 signed-in reads per second, or 500 simultaneous streamed answers, on 2 CPUs |
| Deploys | 238,281 requests during the last upgrade, which added a table, 0 failed; rolling back and forward again, 0 failed of 243,594 and 244,223 |
| Failure drills | 22 injected faults (database frozen, identity provider down, model provider erroring...), each with what users saw and how it recovered |
| AI quality | 19 test cases against a real model: grounding, refusals, prompt injection, isolation between teams, answers from runbooks. The 15 answer cases pass 10 runs in 10, but one at 9 in 10, within chance. Through the whole service, 18 pass 3 runs in 3; one runbook answer left out a step once. The model printed its instructions 2 times in 600 attempts, under the 1.5% limit set for it |
| Runbook search | the section that answers is in the top 5 for all 19 test questions, and first for 15; about 10 ms a search |
| Explaining an answer | every answer can be traced: what it was given (which runbook sections, which prompt version, which model), and where its time went, with no question or answer text kept. Four silent misconfigurations were each found from their traces alone |
| Accountability | every question is on record with what it was given, and every runbook and alert with who wrote each version, in an audit trail the service cannot rewrite (checked by trying, as its own database account). A bad runbook step leads to its author, and to everyone who was given it, in two queries |
| Agent or pipeline | an agent that chooses what to read passed 14 of the 19 cases, against 18 for the fixed pipeline; told to read what the pipeline reads, 17, at 3.8× the tokens. The pipeline stays the default; the agent's read-only tools are also offered to other AI tools (MCP) |
| Keeping secrets from the model | credentials are removed before anything reaches a model: 21 of 24 credential shapes it was not written against, up from 11, and no ordinary text changed |
| Releases | 8 releases, for Intel and ARM servers, each smoke-tested after publishing, and deployed on a production-shaped host with no failed request |

Since v0.8.0, not yet released: the assistant can be switched off in
seconds, without a deploy, with the reason shown to everyone; production
refuses to start with unsafe settings; every image and commit is scanned
for known vulnerabilities and leaked secrets; personal data has an
inventory, a retention job, and a person's export and erasure
([privacy](privacy.md)).

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
credential formats removed. Nothing else. It cannot run commands or change data: by default it has no tools,
and in agent mode (off by default) two that only read, with the asker's rights. The worst
a malicious alert can do is distort one answer, to someone who could
read that alert anyway.

## Risks, and what limits them

| Risk | Limited by |
|---|---|
| The AI answers wrongly or is manipulated by alert text | measured with test cases before each change, calibrated automatic grading, safety cases that must pass 100%; no tool that changes anything; answers shown as text only |
| One team sees another's data | access checks in the service, plus database-level row security, both tested |
| Alert and runbook text sent to an external AI provider | provider terms with zero data retention, or a model in the company's own cloud; known credential formats removed before anything is sent |
| A runbook's wrong or dangerous step repeated by the assistant | every step names its section and the section's date; runbooks stay the teams' own; every version's author, and every answer's sources, are on record |
| Cost runaway | per-user rate limits, output limits, a cost panel; a provider budget alarm is stage 3 |
| The assistant itself becomes the incident | an off switch for org admins: every question refused before any model call, alerts and runbooks still working, the switch audited |
| A known vulnerability or a leaked secret ships | images and commits scanned in every build and release; accepted risks carry a reason and an expiry |
| Personal data kept longer than it may be | an inventory of what is held; a retention job; a person's export and erasure; the legal decisions listed for the privacy officer |
| One server is a single point of failure | acceptable for a pilot; a second host or a managed platform is stage 5 |

## The documents

| Question | Document |
|---|---|
| What are we building, and why? | [PRD-0001](prd/0001-triage-assistant.md) |
| How was a feature proposed, and what did it cost? | [RFC-0001](rfc/0001-answers-grounded-in-runbooks.md): answers that cite the team's runbooks (accepted and built, with the costs measured) |
| What should come next? | "Not done yet" in [the guide](../gold_standard_development_guide.md#not-done-yet) |
| Why was each technical decision made? | [the ADRs](adr/) (25) |
| How is it built, run and repaired? | [the guide](../gold_standard_development_guide.md) and the [handbook](handbook/) |
