# AI security lab: what was measured

Measured on 2026-09-24, on the dev stack with the mock model, at the commit
that added this lab.

## 1. What does redaction catch?

`lab redaction` scores two sets of fake credentials, each in a shape alerts
and logs carry them (an env file, JSON, YAML, a header, a URL, a command
line), and ordinary ops lines that must come through unchanged:

| | Tuning set: 54 secrets, 36 ordinary lines | Held-out set: 24 secrets, 20 ordinary lines |
|---|---|---|
| the first redactor (v0.6.0) | 17 caught; 2 lines changed | 11 caught; 0 changed |
| [gitleaks](https://github.com/gitleaks/gitleaks) v8.30.1, for comparison | 29 caught; 0 flagged | 8 caught; 0 flagged |
| this redactor, when the held-out set was first scored | 54 caught; 0 changed | **21 caught**; 0 changed |
| this redactor, now | 54; 0 | 24; 0 |

**Why the held-out score is the one to report.** The patterns were written
against the tuning set, so 54 of 54 shows they fit it, and little more. The
held-out set was written before the patterns changed, and not looked at
while changing them: 21 of 24 is the estimate for shapes nobody tuned the
patterns to. Its three misses (`redis-cli -a <password>`, a `Set-Cookie`
header, a Kubernetes env entry with the name and value on separate lines)
were then added. So 24 of 24 is no longer an unbiased number.

**What the first version missed, and why.** Its secret names were whole
words, so `\bpassword` never matched `PGPASSWORD`, `DB_PASSWORD` or
`aws_secret_access_key`: `_` counts as part of a word. And a quoted name
(`"password": "..."`) never matched at all. Those are the commonest shapes
in logs. Now a name matches when an identifier *ends* with the word.

**Why `max_tokens: 800` must survive.** That's why names must *end* with
the word: `max_tokens`, `token_count` and `PASSWORD_MIN_LENGTH` name no
secret, and redacting them would strip the facts someone debugging needs.
Likewise masks (`****`), references (`$DB_PASSWORD`, `${...}`) and
placeholders (`<your password>`) are not secrets. The shell's own
`PWD=/home/...` is a directory.

**What redaction can never catch.** A secret with no recognisable format,
no telling name and no telling position: a random string alone on a line,
or a password said in prose ("it's the name of the dog"). Redaction is a
list of patterns, a floor under the prompt's own rule, never a guarantee.
Keeping secrets out of alerts at the source is the fix.

**Also found while measuring.** Some patterns scanned the rest of a line
from every "mysql" or "curl" in it, and a scheme pattern ran to the end of
any run of letters and dots. On text made of many such words the time grew
with the square of the length: 100,000 characters of "mysql " took 6.4 s,
on the api's event loop. A runbook section can be 200,000 characters, and
it is redacted on every chat that retrieves it. Every scan is bounded now:
the worst case measured is ~0.2 s per 100,000 characters, and a unit test
holds it linear. On ordinary text, redaction costs 44 µs per alert and
0.4 ms per runbook (the first version: 10 µs and 80 µs).

## 2. An investigation

The runbook's versions, oldest last:

```
make audit a="--action runbook.saved --hours 1"
```
```
created_at 14:17:37.67  actor ana@lab.example.com  body_sha256 0009b0aeb24e…  previous ddcbdd690585…
created_at 14:17:34.73  actor ben@lab.example.com  body_sha256 ddcbdd690585…  previous 4e0d418a7e78…
created_at 14:17:33.26  actor ana@lab.example.com  body_sha256 4e0d418a7e78…  previous -
```

`GET /api/runbooks/<id>` shows the text of the current version only. To
confirm which save introduced the step, compare the hashes: the step is in
ben's version, and gone from ana's fix three seconds later.

Which questions were given ben's version? Each `chat.asked` event lists its
sections with their runbook's version:

```
BAD=<ben's body_sha256>
make -s audit a="--action chat.asked --hours 1" | jq -c --arg bad "$BAD" \
  '{created_at, actor_email, request_id,
    given_it: ([.detail.sections[].runbook_sha256] | index($bad) != null)}'
```
```
14:17:37.68  cara  given_it: false   (after the fix)
14:17:36.21  eli   given_it: true
14:17:34.75  dev   given_it: true
14:17:33.28  cara  given_it: false   (before the edit)
```

**The answer:**
- ben wrote the step at 14:17:34.7;
- dev and eli were given it;
- ana fixed it at 14:17:37.7.

In production the gaps would be hours, not seconds. The exercise is the same.

**What the audit trail cannot tell you:** whether the answers *repeated*
the step. It records what the model was given, never what it said. The
question and the answer are not stored anywhere: they are as sensitive as
anything the service holds, and a store of every answer is one more copy
to protect, to retain and to delete. So "who was given it" is exact, and
"who was told it" needs the people involved. An organisation that must keep
answers (some regulated ones must) stores them on purpose, in a store with
its own access rules and retention. That is not a side effect of logging.

The `request_id` of each event leads to its log lines (`make trace
id=<request id>`), and its `trace_id` to its trace, when one was kept.

## 3. Who can rewrite the history?

Tried as the app's database role, claiming to be an org admin:

| Statement | Result |
|---|---|
| `UPDATE audit_events ...` | permission denied |
| `DELETE FROM audit_events ...` | permission denied |
| `TRUNCATE audit_events` | permission denied |
| `DROP TABLE audit_events` | must be owner |
| `ALTER TABLE audit_events DISABLE ROW LEVEL SECURITY` | must be owner |
| an `INSERT` naming another user as the actor | refused by row-level security |
| an `INSERT` naming itself | allowed |
| `SELECT`, not as an org admin | 0 rows |

Two layers: the migration revokes `UPDATE`, `DELETE` and `TRUNCATE` from
the app's role, and row-level security has no policy that would let it
update or delete anyway.

`make audit-prune days=0` worked (`DELETE 5` here). It runs `psql` in the
database container as `POSTGRES_USER`, the schema **owner**. The owner has
every privilege on its tables, and row-level security does not apply to it.

**Trustworthy even against the owner** (📘 not built here): copy each event
somewhere the database's administrators cannot change. Two options:
- **an append-only store** outside the database, such as object storage
  with a retention lock, or a separate logging account;
- **a hash chain:** each row carries the hash of the previous one, and the
  latest hash is published elsewhere. That makes a rewrite *detectable*,
  not impossible.

## 4. A design review

Not built or tested here. This is a reference answer, drawn from the OWASP
Top 10 for LLM Applications (LLM06, excessive agency), the OWASP Top 10 for
Agentic Applications (ASI02 tool misuse, ASI03 identity and privilege
abuse), MCP's security best practices, and Meta's "Agents Rule of Two".

**What both tools need:**
- **They act as the asker, never as a service account.** A tool call goes
  through the api with the user's own session, so the api's checks and
  row-level security apply unchanged. A service account would let anyone
  who can influence the model act with the service's rights: a confused
  deputy.
- **The team comes from the session, not from the model.** A tool
  parameter the model fills in (`team`, `user`) is a parameter anyone who
  writes the model's context can fill in.
- **Every call is audited**, like a change by a person: a `tool.called`
  event with the arguments' ids, the user, the approval and the outcome.

**Silence an alert** changes state:
- **The person confirms it.** The approval shows exactly what will happen
  ("silence *db-1 disk 95%* in team payments for 60 minutes"), and it is
  refused unless the user's own role allows it (responder or above).
- **Bounded**: one alert, a maximum duration, easy to undo.
- **The model proposes, the person decides:** text in an alert or a runbook
  must never trigger it on its own.

**Fetch a web page** brings in content no one in the team wrote, and can
reach the network:
- **An allow-list** of domains, with no internal addresses (SSRF), no
  redirects off the list, and a size and time limit.
- **Its result is untrusted data**, like an alert.
- **The Rule of Two.** An agent that reads the team's private data *and*
  untrusted pages *and* can reach the outside can be steered into leaking
  the first through the third. Meta's rule: at most two of the three
  without a person in the loop. So a turn that fetched a page gets no
  state-changing tool without confirmation, and no way to send data
  anywhere.

**The review's verdict:** silence ships with confirmation, the user's
permissions and an audit event. Fetch ships only with an allow-list and
with its content kept away from tools that change state. Or it waits, if
nobody needs it enough.
