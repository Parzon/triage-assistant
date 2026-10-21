---
name: debug
description: Find the cause of a bug before fixing it, starting from a command that fails on the bug. Use when something is broken, a test or build fails, behaviour is unexpected or something got slow, before proposing a fix.
---

# Debug

Get a command that fails on this bug before you form a theory. With one,
finding the cause is a matter of time. Without one, reading code is
guessing.

## 1. A command that fails on this bug

Build the first of these that reaches the bug:

1. A failing test, at whatever level reaches it: unit, integration, end to end.
2. A request (curl, a short HTTP script) against the running app.
3. The script or CLI with a saved input, its output compared with a known-good one.
4. A browser script (Playwright) asserting on the page, the console or the network.
5. A real input (a request, a payload, an event) saved to a file and replayed through the code path.
6. The same input through the old and the new version, or two configurations: diff the outputs.
7. Between a good and a bad commit: `git bisect run` with the command.

It is ready when you have run it and it:
- fails on the symptom that was reported, not on "some error";
- gives the same verdict every run. A flaky bug: raise how often it
  fails first (loop it, add load, narrow the timing) until it fails often
  enough to work with;
- takes seconds, not minutes;
- runs without a person.

Can't build one? Stop and say so: what you tried, and what you need (access
to where it happens, a log, a captured request). Don't move on to theories.

Redact secrets in everything you show: tokens, cookies, keys, connection
strings. Keep them in environment variables, not in the commands you print.

## 2. Find where the bad value starts

- Read the whole error and stack trace: the file, the line, the value.
- Check what changed: the diff, recent commits, dependency and
  configuration changes.
- Trace it back. Where did the wrong value come from, and who passed it?
  Keep going until you reach the place it is first wrong. That is the
  cause; where it crashed is only the symptom.
- Several components (browser, api, database; CI, build, deploy)? Log what
  enters and leaves each boundary once, then look at where it goes wrong.
- Find similar code that works, and list every difference.

## 3. Fix it once, at the source

- Fix where the value first goes wrong, so every caller gets the fix.
- The command from step 1 now passes. Keep it as a test that fails on the
  old code.
- Three fixes in a row didn't work? Stop: your picture of the problem is
  wrong. Report what you know instead of trying a fourth.

## Report

The command, its output before and after the fix, the cause in one
sentence, and what you didn't check.
