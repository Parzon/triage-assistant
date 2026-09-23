"""Is the judge right? Answers labelled by a person, graded by the judge.

A judge is a model: before its verdicts gate anything, check that it
agrees with a person on answers whose verdict is known - good ones, bad
ones, and the ones a judge got wrong before. `python -m evals
--calibrate-judge` fails on any disagreement, and on any answer the judge
would not grade.

A judge that says YES to everything passes every good answer, so each
criterion needs at least one answer labelled NO: load_labelled refuses a
set without one.
"""

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.cases import Case
from evals.run import Judge


@dataclass(frozen=True)
class Labelled:
    case: Case
    text: str
    verdict: bool
    why: str


def load_labelled(path: Path, cases: Sequence[Case]) -> list[Labelled]:
    judged = {c.id: c for c in cases if c.expect.judge}
    with path.open("rb") as f:
        raw = tomllib.load(f).get("answer", [])
    labelled = []
    for n, item in enumerate(raw, 1):
        case = judged.get(str(item.get("case")))
        if case is None:
            raise ValueError(f"{path.name}: answer {n}: no case {item.get('case')!r} with a judge")
        why = str(item.get("why", ""))
        labelled.append(Labelled(case, str(item["text"]), bool(item["verdict"]), why))
    for case_id in judged:
        if {x.verdict for x in labelled if x.case.id == case_id} != {True, False}:
            raise ValueError(f"{path.name}: {case_id} needs an answer labelled YES and one NO")
    return labelled


async def calibrate(
    judge: Judge, labelled: Sequence[Labelled], *, repeat: int = 1
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in labelled:
        verdicts = [await judge.verdict(item.case, item.text) for _ in range(repeat)]
        rows.append(
            {
                "case": item.case.id,
                "label": item.verdict,
                "verdicts": [v.passed for v in verdicts],
                "agrees": all(v.passed is item.verdict for v in verdicts),
                "text": item.text,
                "why": item.why,
                "judge_reasons": [v.reason for v in verdicts],
            }
        )
    pairs = [(row["label"], v) for row in rows for v in row["verdicts"]]
    return {
        "answers": rows,
        "verdicts": len(pairs),
        # Passed a bad answer: the dangerous direction, a failure goes unseen.
        "false_yes": sum(1 for label, v in pairs if v is True and not label),
        # Failed a good answer: a false alarm that erodes trust in the evals.
        "false_no": sum(1 for label, v in pairs if v is False and label),
        "no_verdict": judge.missing,
    }


def calibration_markdown(summary: dict[str, Any]) -> str:
    def word(v: bool | None) -> str:
        return "?" if v is None else "YES" if v else "NO"

    lines = ["| case | label | judge | answer |", "|---|---|---|---|"]
    for row in summary["answers"]:
        mark = "" if row["agrees"] else " **differs**"
        text = row["text"].replace("\n", " ").replace("|", "\\|")[:70]
        verdicts = " ".join(word(v) for v in row["verdicts"])
        lines.append(f"| {row['case']} | {word(row['label'])} | {verdicts}{mark} | {text} |")
    agreed = sum(1 for row in summary["answers"] if row["agrees"])
    total = len(summary["answers"])
    lines.append("")
    lines.append(
        f"Agreed on {agreed} of {total} answers ({summary['verdicts']} verdicts): "
        f"{summary['false_yes']} false YES, {summary['false_no']} false NO, "
        f"{len(summary['no_verdict'])} without a verdict."
    )
    lines.append("")
    lines.append("**PASS**" if agreed == total else "**FAIL**")
    return "\n".join(lines)
