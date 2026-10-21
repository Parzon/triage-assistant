---
name: prove-it
description: Back every claim that work is done, fixed or passing with a command run now and its output. Use before saying a task is complete, a bug is fixed or the checks pass, and before committing, opening a PR or reporting back.
---

# Prove it

Say it's done only with the evidence next to the claim.

## Before any claim

1. Which command proves it?
2. Run it now, in full. A run from before your last change doesn't count.
3. Read the output: the exit code, the count of failures, the warnings.
4. Then state what it shows, with the command and the lines that matter.
   If it shows less than you hoped, say that.

| Claim | Proof | Not proof |
|---|---|---|
| The tests pass | the test command, now: 0 failures | an earlier run; "should pass" |
| Lint and types are clean | each command, now: 0 errors | one of them; the editor's colours |
| It builds | the build, exit code 0 | lint passing |
| The bug is fixed | the command that showed the bug now passes | the code changed |
| The test catches the bug | it fails with the fix reverted and passes with it | it passed once |
| A subagent or a tool did it | the diff shows the change, and the checks pass | its report says so |
| The task is done | each thing the issue asked for, checked one by one | the tests pass |

## When you can't run it

Say so plainly: which check you didn't run, why (no Docker here, no
access), and the command the reader should run. CI is a backstop, not a
replacement for the check you can run now.

## Words that need a command next to them

"Should work", "probably", "looks right", "done", "fixed", "all green".
