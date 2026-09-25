# Runbook: turn the assistant off

For when the assistant itself is the incident. It stops answers in
seconds, on every server, without a deploy (ADR-0024). Alerts and
runbooks keep working.

## When

- It gives advice that would do harm: a destructive command, another
  team's data, instructions it was never given (a poisoned runbook or
  alert, a model update at the provider, a prompt change).
- The model provider has a breach, or its terms no longer allow your
  data (legal or security says stop).
- The bill runs away, or the provider fails every answer and the error
  is confusing users.

When in doubt, turn it off first and investigate after: nothing else in
the service depends on it.

## Turn it off

**As an org admin, in the app:** the "The assistant" panel, a reason,
"Switch off". Everyone who asks reads the reason: say what happened and
where to go instead ("Bad advice under investigation; use the runbooks
directly. #incident-123").

**Without the app** (the identity provider is down, nobody who is an org
admin is awake), on the production host:

```
make assistant off="Bad advice under investigation; use the runbooks directly" ENV=prod
make assistant ENV=prod           # the state, and since when
```

No single quotes in the reason. With an API client and an org admin's
session: `PUT /api/assistant {"enabled": false, "reason": "..."}`.

## Check

- The chat panel says it is switched off, with the reason.
- `curl -s -X POST https://<domain>/api/chat/stream ...` answers 503
  `assistant_disabled` (a signed-in client).
- `chat_refusals_total{reason="assistant_disabled"}` grows with each
  refused question. `HighErrorRate` leaves them out: nobody is paged.

## While it is off

- Find what it was given: `make audit a="--action chat.asked --hours 24"
  ENV=prod` lists every question's prompt version, model, alerts and
  runbook sections, by hash; a section's hash leads to the runbook save
  that wrote it (docs/handbook/ai-security.md).
- If no text may reach the provider at all, embeddings go too: unset
  `EMBEDDING_MODEL` and `make deploy` the running tag. Runbook saves and
  searches then answer 503 `runbooks_off`.

## Turn it back on

After the cause is fixed and the evals pass on the fix
(docs/handbook/ai-engineering.md): "Switch on" in the app, or `make
assistant on=1 ENV=prod`. Both flips are in the audit trail: `make audit
a="--action assistant.disabled"`.
