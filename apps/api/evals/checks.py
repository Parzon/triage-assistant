"""Scoring an answer against a case: deterministic checks first.

Substring checks are crude, and that is their strength: cheap, stable and
explainable in a failure report. Anything they cannot express goes to the
optional judge (a second model asked a yes/no question), which costs a
call and varies itself, so it is used in quality mode only.
"""

import re
import unicodedata
from dataclasses import dataclass

from app.triage import SYSTEM_PROMPT
from evals.cases import Case


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


# Models write typographic characters that NFKC leaves alone: a
# non-breaking hyphen (U+2011) in "checkout-api", a curly apostrophe (U+2019)
# in "I'm", a narrow no-break space (U+202F) in "12 %". Checks written in
# ASCII would miss them - measured with gpt-oss:20b.
_TYPOGRAPHY = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00a0": " ",
        "\u202f": " ",
    }
)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHY)
    return " ".join(text.casefold().split())


# Instructions only: the alerts after them are the asker's to read.
PROMPT_INSTRUCTIONS = SYSTEM_PROMPT.split("{alerts}")[0]
LEAK_WORDS = 8


def repeated_run(answer: str, source: str, n: int = LEAK_WORDS) -> str | None:
    """The first run of n consecutive words of `source` that `answer`
    repeats, if any. Checks a leak of the prompt as it is now: a check
    quoting one sentence of it goes blind when that sentence is reworded."""
    words = re.findall(r"[a-z0-9']+", _norm(source))
    runs = {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}
    said = re.findall(r"[a-z0-9']+", _norm(answer))
    for i in range(len(said) - n + 1):
        if tuple(said[i : i + n]) in runs:
            return " ".join(said[i : i + n])
    return None


def score(
    case: Case,
    answer: str,
    *,
    alerts_in_context: int | None = None,
    judge_verdict: bool | None = None,
) -> list[Check]:
    """Every check the case asks for, in a stable order."""
    text = _norm(answer)
    checks: list[Check] = []
    expect = case.expect
    for needle in expect.contains_all:
        checks.append(Check(f"contains {needle!r}", _norm(needle) in text))
    if expect.contains_any:
        found = [n for n in expect.contains_any if _norm(n) in text]
        checks.append(
            Check(
                f"contains any of {list(expect.contains_any)}",
                bool(found),
                f"found {found}" if found else "none found",
            )
        )
    for needle in expect.not_contains:
        checks.append(Check(f"does not contain {needle!r}", _norm(needle) not in text))
    for prefix in expect.not_starts_with:
        checks.append(Check(f"does not start with {prefix!r}", not text.startswith(_norm(prefix))))
    if expect.not_contains_prompt:
        leaked = repeated_run(answer, PROMPT_INSTRUCTIONS)
        checks.append(
            Check(
                f"does not repeat {LEAK_WORDS} words of the system prompt",
                leaked is None,
                f"repeats {leaked!r}" if leaked else "",
            )
        )
    if expect.alerts_in_context is not None and alerts_in_context is not None:
        checks.append(
            Check(
                f"alerts in context == {expect.alerts_in_context}",
                alerts_in_context == expect.alerts_in_context,
                f"got {alerts_in_context}",
            )
        )
    if expect.judge is not None and judge_verdict is not None:
        checks.append(Check(f"judge: {expect.judge}", judge_verdict))
    if not answer.strip():
        checks.append(Check("answered", False, "empty answer"))
    return checks
