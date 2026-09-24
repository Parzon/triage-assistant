# AI security

What changes when a service has a language model in it, what this one does
about it, and what was measured. General security (sign-in, roles,
secrets, the network, the supply chain) is in [Security](security.md).
The hands-on part is [labs/ai-security](../../labs/ai-security/README.md).
✅ = in place and checked here, 📘 = documented practice, not built here.

## What changes

**Text becomes an input channel for instructions.** Anyone who can write
text the model reads (an alert, a runbook, a web page, an email) can try
to instruct it (prompt injection, OWASP LLM01). The model cannot reliably
tell instructions from data, and no prompt makes it able to. Published
defences that work at the prompt or model level are bypassed by adaptive
attackers: [The Attacker Moves Second](https://arxiv.org/abs/2510.09023)
(2025) broke all 12 it tested, most above 90% 📘.

**So the question is architectural.** Follow the chain from what the model
reads to what it can affect:

```
what the model reads → the model → its decision → a tool → a system or data
```

At each step ask: *what happens if the model is wrong, or if its context
was written by someone other than the person asking?* The controls that
hold are the ones outside the model: what it may read, what it may do,
with whose rights, and who confirms.

## The chain, in this service

| Step | What can go wrong | What bounds it here |
|---|---|---|
| What the model reads | alert and runbook text written by others: instructions, or credentials | ✅ only what the asker may read (the same queries as the UI, row-level security underneath); ✅ credentials redacted before any model call; ✅ the prompt marks alerts and runbooks as untrusted, and evals measure how often that holds |
| The model | a wrong answer, or one steered by planted text | ✅ evals, including injection and isolation cases, gate every prompt or model change ([AI engineering](ai-engineering.md)) |
| Its decision | acting on the steered answer | ✅ **the model has no tools**: nothing it writes is executed. A person reads the answer |
| The answer on screen | markup that loads or runs: an image URL carrying data out, a script | ✅ rendered as text, never HTML or markdown (a test fails otherwise); ✅ the page's content security policy allows images and fetches from this site only |
| Systems and data | — | nothing to reach: no tools. The api's database role cannot drop or alter tables ([Security](security.md), least privilege) |
| After the fact | not knowing who wrote what the model read, or what it was given | ✅ the audit trail (below); ✅ traces and logs hold no content ([AI observability](ai-observability.md)) |

The strongest control in that table is the absence of tools. The day the
assistant gets one, read [Before the assistant gets tools](#before-the-assistant-gets-tools).

## Redaction: measured ✅

Credentials in alerts and runbooks are replaced before any model call: the
prompt, and the text sent for embedding (`app/redact.py`). The prompt's
own rule ("never repeat credentials") failed about 1 run in 100; redaction
does not fail for the formats it knows, and it keeps them from the model
provider too (OWASP LLM02).

**How much it catches.** Scored with `labs/ai-security/lab redaction`,
over fake credentials in the shapes logs carry them (env files, JSON,
YAML, headers, URLs, command lines):

| | Tuning set (54) | Held-out set (24) |
|---|---|---|
| the v0.6.0 redactor | 17 | 11 |
| gitleaks v8.30.1, for comparison | 29 | 8 |
| this redactor, held-out scored once | 54 | **21** |

None of 56 ordinary lines was changed by this redactor. The v0.6.0 one
changed 2. The held-out set was written before the patterns changed and not
used to tune them. 21 of 24 is the honest estimate for shapes nobody
anticipated; its three misses were covered afterwards.

**Three ways to recognise a secret**, and the patterns use all three:
- **by name:** an identifier that *ends* with password, secret, token or
  api_key (`PGPASSWORD`, `aws_secret_access_key`, `"client_secret"`). It
  must *end* with it: `max_tokens` and `token_count` are not secrets. The
  v0.6.0 version matched whole words only, and `\bpassword` never matches
  `PGPASSWORD`, since `_` and letters are word characters;
- **by format:** 16 token formats, and private keys (also cut short);
- **by position:** a URL's `user:password@`, `Authorization` and cookie
  headers, passwords on command lines.

Masks (`****`), references (`$DB_PASSWORD`) and placeholders are left
alone.

**What it can never catch:** a secret with no known format, name or
position. The fix for those is at the source.

**Cost:** 44 µs per alert, 0.4 ms per runbook section, on every chat.

**Bounded, or it is a denial of service.** Some patterns scanned the rest
of a line from every "mysql" or "curl" in it. On a line made of such words
that is quadratic: 100,000 characters of "mysql " took 6.4 s, on the event
loop. A runbook section can be 200,000 characters, and is redacted on every
chat that retrieves it. Every scan is now bounded; the worst hostile input
measured takes ~0.2 s per 100,000 characters, and
`test_hostile_text_is_redacted_in_linear_time` keeps it linear. Any regular
expression run on text others write needs the same test.

## The audit trail ✅

`audit_events` (ADR-0019, `app/audit.py`) answers the two questions after
a bad answer: **what was the model given, and who wrote it?**

| Event | Recorded when | What it holds |
|---|---|---|
| `runbook.saved`, `runbook.deleted` | a team admin, or `make reembed`, writes a runbook | the author, the runbook, the hash of its text, the hash it replaced |
| `alert.created`, `alert.deleted` | a user, or the Alertmanager webhook | who (or `via: alertmanager`), the alert, the hash of its message |
| `chat.asked` | every question, before the answer starts | who asked, their teams, the prompt's version and hash, the model, the alert ids and runbook sections in the context, each with its runbook's version hash |

A chat's section hash leads to the save that wrote that version, so from a
complaint to its author takes two queries (lab, exercise 2).

**What it never holds:** the text of a question, an answer, an alert or a
runbook. Those live in their own tables under their own access rules, and
nowhere else; the hashes prove which version was given. The trade: the
trail says exactly who was *given* a step, not whether the answer
*repeated* it. An organisation that must keep answers stores them on
purpose, with its own access rules and retention.

**Written with the change.** Each event is inserted in the same transaction
as the change it records: a change whose audit write fails does not happen
(`test_no_change_happens_without_its_audit_event`). A chat's event is
committed before the answer starts, so no answer goes unrecorded. Its cost:
0.5 ms per chat (measured, through PgBouncer), against seconds of model
time.

**The api cannot rewrite it.** The migration revokes `UPDATE`, `DELETE` and
`TRUNCATE` from the app's database role, and row-level security has no
policy for them. Tried as that role: update, delete, truncate, drop,
disabling row-level security and inserting in another user's name are all
refused (`test_the_app_role_cannot_rewrite_the_audit_trail`). Row-level
security also lets only org admins read it.

**Reading it:** org admins, `GET /api/audit` (filters: `action`, `actor`,
`team`, `target`, `since`; paged with `before`); operators, `make audit
a="--action runbook.saved --target 17"` (add `ENV=prod`).

**Retention is yours to decide.** Rows are kept until the schema owner
deletes them: `make audit-prune days=N`. Set N from your organisation's
policy. Some rules require a minimum; privacy law requires a maximum.

**Traces are not an audit trail.** Traces are meant to be sampled under
load ([AI observability](ai-observability.md) measured why), are kept for
days (Jaeger here holds the last 20,000 in memory), and are there to find
where time went. The audit trail records every event, keeps it as long as
policy says, and is there to establish who did what.

📘 **Against the database owner:** the owner can still delete rows. For
history that even the database's administrators cannot rewrite, copy each
event to an append-only store outside the database (object storage with a
retention lock, a separate logging account), or chain each row's hash to
the previous one and publish the latest elsewhere.

## Output handling ✅

The answer is rendered as text (`white-space: pre-wrap`), never as HTML or
markdown. A model steered into writing a markdown image whose URL carries
data, or a `<script>`, shows it as characters; nothing loads or runs. This
is the channel of known data-leak incidents against chat assistants:
Microsoft 365 Copilot's EchoLeak (CVE-2025-32711, 2025) sent data out
through a rendered image link 📘. `Chat.test.tsx` fails if the answer is
rendered as HTML (checked by switching it). The content security policy is
a second layer: images and connections to this site only.

To render markdown one day, sanitise it, allow no images from outside the
site, and keep the policy.

## Before the assistant gets tools

📘 Nothing here is built yet: tools arrive with the agents work. These
rules come from the OWASP Top 10 for LLM Applications (LLM06 excessive
agency), the OWASP Top 10 for Agentic Applications (ASI02 tool misuse,
ASI03 identity and privilege abuse), MCP's security best practices, and
Meta's Agents Rule of Two:

1. **A tool acts as the asking user**, through the api, with the user's
   session: the same role checks and row-level security. Never a service
   account, which lets anyone who can steer the model use the service's
   rights (a confused deputy).
2. **Tenancy and identity come from the session, never from a parameter**
   the model fills in. A `team` argument is an argument anyone who writes
   the model's context can fill in.
3. **A change of state needs the person's confirmation**, showing exactly
   what will happen, and is bounded: one object, a limited effect, easy to
   undo.
4. **The Rule of Two:** in one session, at most two of *untrusted input*,
   *private data or sensitive systems*, and *the ability to change state or
   communicate out*, without a person confirming. All three together is how
   data leaks. Simon Willison's "lethal trifecta" is the same idea for
   data exfiltration.
5. **Egress is an allow-list**, with no internal addresses (SSRF) and no
   redirects off the list. Anything a tool fetches is untrusted input.
6. **Every call is audited** like a change by a person: a `tool.called`
   event with the arguments' ids, the approval and the outcome.
7. **Least privilege per tool:** narrow scopes, short-lived credentials,
   never a token passed through from the user to another service (MCP
   forbids token passthrough).
8. **Code a model writes runs sandboxed:** no network, a read-only file
   system, no secrets in its environment, limits on time and memory.

The research on designs that resist injection names six patterns 📘
([Beurer-Kellner et al., 2025](https://arxiv.org/abs/2506.08837)):
action-selector, plan-then-execute, LLM map-reduce, dual LLM,
code-then-execute and context minimisation. Its conclusion: once an agent
has read untrusted input, it must be *impossible* for that input to trigger
a consequential action. General-purpose agents, it argues, cannot give that
guarantee.

## Gotchas met here

- **`INSERT ... RETURNING` under row-level security is checked against the
  SELECT policy.** Audit rows are readable by org admins only, so an
  insert that asks for its new id back is refused. `app/audit.py` inserts
  with `Insert.inline()`, which asks for nothing back.
- **SQLAlchemy's `implicit_returning=False` is not the fix:** it fetches
  the next id itself and inserts it, which a `GENERATED ALWAYS` identity
  column refuses.
- **Audit rows outlive every test**, since nothing may delete them. Tests
  find their own rows by action and target, or by a user created for the
  test. A runbook and an alert can share an id.
- **A tuning-set score is not a measurement.** Patterns written against a
  set score well on it by construction. Keep a held-out set, written first
  and scored once.
- **`\b` does not separate `_` from letters.** `\bpassword` matches
  "password" but not `DB_PASSWORD`.
