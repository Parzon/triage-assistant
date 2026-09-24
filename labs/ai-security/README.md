# AI security lab

Two controls keep what the model sees in check, and this lab measures them:
- **redaction**: known credential formats are removed from everything sent
  to a model (`app/redact.py`);
- **the audit trail**: who wrote the text the assistant reads, and what it
  was given for each question (`app/audit.py`, ADR-0019).

The last exercise is a design review, on paper, for the day the assistant
gets tools.

**Do each exercise before reading [answers.md](answers.md):** it holds what
was measured here, and the reasoning.

## Setup

The dev stack: `make up`. The mock model is enough. Nothing here depends
on what the model answers, only on what it is given.

The helper, `labs/ai-security/lab`:

| Command | Does |
|---|---|
| `lab redaction [tuning\|heldout\|both]` | runs the redactor over two sets of fake credentials, and ordinary lines that must come through unchanged; prints what it missed and what it changed |
| `lab setup` | team `audit-lab`: a runbook edited three times by two admins, and four questions asked along the way (exercise 2) |

## The exercises

**1. What does redaction catch?**

```
lab redaction
```

Then open `labs/ai-security/redaction.py` and add a line to `positives()`
in a shape you have seen a secret take in your own logs, with a made-up
value. Predict whether it is caught, and run it again.

- Why does the answers file report the **held-out** score, not the tuning
  one?
- What can redaction never catch, however many patterns it has?
- Why must it leave `max_tokens: 800` and `PWD=/home/deploy` alone?

**2. An investigation.**

```
lab setup
```

An engineer says the assistant told them to delete files in the database's
data directory. Using only the audit trail (`make audit a="..."`; `make
audit a="--help"` lists the filters), find:
- who wrote that step, and when;
- which questions were given that version, and who asked them;
- when it was fixed.

What can the audit trail *not* tell you about those answers? Why was it
built that way?

**3. Who can rewrite the history?** Read the migration
(`apps/api/migrations/versions/20260924_e46b40a7952a_audit_events.py`) and
`test_the_app_role_cannot_rewrite_the_audit_trail` in
`apps/api/tests/integration/test_audit.py`.
- Which database roles can change or delete an audit event?
- Run `make audit-prune days=0` on the dev stack. Which role did that run
  as, and why could it?
- What would make the history trustworthy even against that role?

**4. A design review, on paper.** The team proposes two tools for the
assistant:
- **silence an alert** for an hour;
- **fetch a web page** that a runbook links to.

Use the chain *model → decision → tool → system or data*. At each step,
ask what happens if the model is wrong, or if its context was written by
someone other than the asker. Write the review: what must be true before
each tool ships? Then compare with the answers file.
