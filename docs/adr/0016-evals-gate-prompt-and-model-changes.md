# ADR-0016: Evals gate prompt and model changes

**Status:** accepted
**Date:** 2026-09-24

## Context

The assistant's answers depend on the model and the prompt together, and
no unit test can pin them down: the same prompt gives different answers
on every run. Measured with gpt-oss:20b while building this repo:

- a prompt rule that fixed one behaviour broke another, twice (v3, v4);
- a failure that happens 1 run in 40 passed 110 calls in a row;
- a model left to its default reasoning effort returned blank answers,
  which the service reported as successes.

Changes to the prompt or the model needed a measurement before and after
that a reviewer can trust, not a few answers read by hand.

## Decision

1. **Versioned cases in git** (`apps/api/evals/cases/*.toml`), in four
   kinds: grounding, refusal, injection, isolation. Changing a case is
   reviewed like code.
2. **Two targets.** The model target is the production prompt sent
   straight to the model. The api target is the running service, signed
   in, where data isolation is proven.
3. **Deterministic checks first, an LLM judge for the rest.** A safety
   case (injection, isolation) always has a deterministic check: the
   judge reads output from a model that may have been injected.
4. **The gate.** A run fails on:
   - any safety failure in any repeat;
   - a quality pass rate under 80%;
   - a judge that gave no verdict;
   - a regression against the committed baseline.

   A case passes only if every repeat passes.
5. **The judge is calibrated before it is trusted.** It is from another
   model family than the model under test, and runs at temperature 0. It
   gives a reason, then a verdict. Labelled answers
   (`judge_calibration.toml`) include a NO for every criterion, and it
   must agree with every label.
6. **CI runs plumbing mode** against the mock (free, deterministic).
   Quality mode needs a real model and runs on demand: before merging a
   prompt or model change, with the numbers in the PR.

## Alternatives considered

- **An evals framework** (promptfoo, DeepEval, OpenAI Evals) or a hosted
  platform (LangSmith, Braintrust, Langfuse). Each would need adapters
  to use the service's own code paths: its prompt builder, and its
  signed-in API for the isolation cases. The harness here is about 1,100
  lines, with no new dependency. Revisit when the cases outgrow a
  directory of TOML files, or when non-engineers curate them.
- **Quality evals in CI.** That needs a GPU runner or a provider key,
  plus a budget, on every push. Plumbing mode in CI plus quality runs on
  demand catches the same regressions at the point they are introduced
  (a prompt or model change).
- **Judge-only checks.** Cheaper to write, but a judge can be wrong in
  both directions, and calibration here caught it being wrong four
  times. It stays a complement to deterministic checks, never the only
  check of a safety case.
- **Retrying flaky cases until they pass.** That hides exactly the rare
  failures that matter. Repeats measure the rate instead.

## Consequences

- A prompt change costs a few minutes of eval runs on a local GPU, or a
  few cents of provider tokens (about 350 tokens in and 85 out per
  answer). Rare failures (leaks) need about 200 runs of the one case.
- The baseline (`evals/baselines/<model>.json`) must be replaced in the
  same PR as a change that moves it. A new model gets its own baseline.
- Quality numbers measured on a local model do not carry over to another
  model: switching models means re-running the suite and calibrating the
  judge again.
- A failing case is a claim to investigate, not a verdict. The report
  keeps every answer and the judge's reason, because four of the first
  five "failures" here were wrong checks, not wrong answers.
