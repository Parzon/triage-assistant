---
name: two-pass-review
description: Review a change in two passes reported apart, spec (does it do what was asked, and only that) and standards (does it follow AGENTS.md and read cleanly), with every finding shown by a probe or a test. Use when asked to review a branch, a pull request or a diff.
---

# Two-pass review

## Set up

- **The diff:** `git diff <base>...HEAD` (three dots: against the merge
  base) and `git log <base>..HEAD`. Check first that the base exists and the
  diff isn't empty.
- **The spec:** the issue the change closes, its RFC or ticket. No spec?
  The spec pass says so and stops.
- **The standards:** `AGENTS.md`, `CONTRIBUTING.md` and any style guide
  they name.
- **The size:** up to a few hundred lines, review it whole. Larger: one
  review per area the change's promises rest on (for example access,
  sign-in, migrations, the handling of secrets), then one pass over the
  whole diff for what crosses areas.

## Pass 1: spec

- What the spec asked for that is missing or half done.
- What the change does that nobody asked for.
- What looks done but is wrong. Try the edges: empty, huge, non-ASCII,
  concurrent, the dependency down.

Quote the spec line for each finding.

## Pass 2: standards

- Each place the change breaks a rule in `AGENTS.md` or the style guide:
  cite the rule.
- Code smells, as judgement calls (the repository's own rules win over
  them):
  - a name that doesn't say what the thing is or does;
  - the same logic twice in the change;
  - a function that mostly works on another object's data;
  - the same few values always passed around together: a type waiting to
    be made;
  - a plain string or number standing in for a domain idea;
  - the same switch on a type in several places;
  - one logical change needing edits scattered across many files;
  - options, parameters or layers that nothing uses yet;
  - a layer that only passes calls on.
- Skip what a linter or formatter already checks.

## Every finding

- Shown with a probe or a test you ran, not a reading. Probes go in a
  scratch folder; leave the repository as you found it.
- Where (file and line), what goes wrong and when, and how sure you are.
- Most severe first within each pass. Keep the two lists apart: a change
  can do the wrong thing cleanly, or the right thing badly.
- A short list of real problems beats a long one. None? Say so.

## End

One line per pass: how many findings, and the worst one.
