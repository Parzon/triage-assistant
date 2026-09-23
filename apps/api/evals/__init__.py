"""Evaluations for the assistant: does it answer well, and safely?

Unit tests prove the code does what it says; they cannot say whether a
model's answers are grounded in the alerts, resist instructions smuggled
into alert text, or never mention another team's incident. Evals do, by
running versioned cases (`evals/cases/*.toml`) through the real prompt and
a real model, and checking every answer.

    python -m evals --target model --mode quality     # a real model, the production prompt
    python -m evals --target api --mode plumbing      # the whole service, the mock model

Two targets:
- model: the production prompt (app.triage.build_messages) around each
  case's alerts, sent straight to LLM_BASE_URL. Fast; measures the model
  and the prompt.
- api: the running service, signed in as a user of the case's groups.
  Measures the whole system, and is where data isolation is proven: the
  case's alerts are created as an org admin, and the question is asked by
  someone who may see only some of them.

Two modes:
- plumbing (CI, the mock model): the mock's replies are canned, so only
  the checks that hold for any model are gated - isolation and injection.
  Proves the harness, the service and the gate work, for free.
- quality (a real model): every check counts. Safety kinds (isolation,
  injection) must all pass; quality kinds (grounding, refusal) a
  threshold, because model output varies (--repeat measures how much).

What no substring can decide goes to a judge (--judge): a second model,
from another family, asked a yes/no question about the answer. It is
trusted only after it agrees with a person on labelled answers
(judge_calibration.toml, --calibrate-judge), and never as the only check
of a safety case.
"""
