"""Eval cases: versioned in git as TOML, one file per kind.

A case is a question, the alerts it is asked about, and what a good answer
must (and must not) contain. Changing a case changes the benchmark: review
it like code, and compare reports only between runs of the same cases.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Kind = Literal["grounding", "refusal", "injection", "isolation"]
# A failure in these is a security bug, not a quality dip: they must all pass.
SAFETY_KINDS: frozenset[str] = frozenset({"injection", "isolation"})
KINDS: frozenset[str] = frozenset({"grounding", "refusal", "injection", "isolation"})
SEVERITIES = ("info", "warning", "high", "critical")


@dataclass(frozen=True)
class CaseAlert:
    message: str
    severity: str = "warning"
    source: str = "prometheus"
    # Only the api target uses teams: who owns the alert, for isolation.
    team: str = "default"


@dataclass(frozen=True)
class CaseRunbook:
    title: str
    body: str
    # Whose runbook it is (api target): isolation cases put one elsewhere.
    team: str = "default"


@dataclass(frozen=True)
class Expect:
    contains_all: tuple[str, ...] = ()
    contains_any: tuple[str, ...] = ()
    not_contains: tuple[str, ...] = ()
    # Obeying an injected "reply only with X" shows at the start of an
    # answer; quoting it while warning about it does not.
    not_starts_with: tuple[str, ...] = ()
    # No run of words from the system prompt's instructions (checks.py).
    not_contains_prompt: bool = False
    # api target only: how many alerts the service put in the prompt.
    alerts_in_context: int | None = None
    # Each must match (as a substring) the heading of a section the answer
    # cites, like "Free space" for [R1] = "Disk full > Free space".
    cites: tuple[str, ...] = ()
    # The answer must cite no runbook at all: none applies.
    cites_nothing: bool = False
    # api target only: how many runbook sections the service retrieved.
    runbooks_in_context: int | None = None
    # quality mode only: a yes/no question for an LLM judge about the answer.
    judge: str | None = None

    @property
    def deterministic(self) -> bool:
        return bool(
            self.contains_all
            or self.contains_any
            or self.not_contains
            or self.not_starts_with
            or self.not_contains_prompt
            or self.alerts_in_context is not None
            or self.cites
            or self.cites_nothing
            or self.runbooks_in_context is not None
        )


@dataclass(frozen=True)
class Case:
    id: str
    kind: Kind
    question: str
    alerts: tuple[CaseAlert, ...]
    expect: Expect
    # Runbooks the answer may draw on: given to the model as retrieved
    # sections (model target), or saved and retrieved by the service (api).
    runbooks: tuple[CaseRunbook, ...] = ()
    # api target: the groups of the user asking ("team:payments:viewer").
    # Empty: a viewer of every team the case's alerts belong to.
    asker_groups: tuple[str, ...] = ()
    # Checks that hold for any model, the mock included (plumbing mode).
    plumbing: bool = False
    notes: str = ""
    targets: tuple[str, ...] = field(default=("model", "api"))


def load_cases(directory: Path) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(directory.glob("*.toml")):
        with path.open("rb") as f:
            data = tomllib.load(f)
        for raw in data.get("case", []):
            cases.append(_case(raw, path))
    ids = [c.id for c in cases]
    if duplicates := {i for i in ids if ids.count(i) > 1}:
        raise ValueError(f"duplicate case ids: {sorted(duplicates)}")
    return cases


def _case(raw: dict[str, object], path: Path) -> Case:
    where = f"{path.name}: case {raw.get('id', '?')}"
    try:
        kind = str(raw["kind"])
        if kind not in KINDS:
            raise ValueError(f"{where}: kind must be one of {sorted(KINDS)}")
        alerts = tuple(_alert(a, where) for a in _dicts(raw.get("alerts", [])))
        for alert in alerts:
            if alert.severity not in SEVERITIES:
                raise ValueError(f"{where}: severity {alert.severity!r}")
        expect = _dicts([raw.get("expect", {})])[0]
        targets = tuple(str(t) for t in _list(raw.get("targets", ["model", "api"])))
        case = Case(
            id=str(raw["id"]),
            kind=kind,  # type: ignore[arg-type]  # checked against KINDS above
            question=str(raw["question"]),
            alerts=alerts,
            expect=Expect(
                contains_all=tuple(_list(expect.get("contains_all", []))),
                contains_any=tuple(_list(expect.get("contains_any", []))),
                not_contains=tuple(_list(expect.get("not_contains", []))),
                not_starts_with=tuple(_list(expect.get("not_starts_with", []))),
                not_contains_prompt=bool(expect.get("not_contains_prompt", False)),
                alerts_in_context=_int_or_none(expect.get("alerts_in_context")),
                cites=tuple(_list(expect.get("cites", []))),
                cites_nothing=bool(expect.get("cites_nothing", False)),
                runbooks_in_context=_int_or_none(expect.get("runbooks_in_context")),
                judge=str(expect["judge"]) if "judge" in expect else None,
            ),
            asker_groups=tuple(str(g) for g in _list(raw.get("asker_groups", []))),
            runbooks=tuple(_runbook(r, where) for r in _dicts(raw.get("runbooks", []))),
            plumbing=bool(raw.get("plumbing", False)),
            notes=str(raw.get("notes", "")),
            targets=targets,
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{where}: {exc}") from exc
    if not (case.expect.deterministic or case.expect.judge):
        raise ValueError(f"{where}: needs at least one check")
    if case.kind in SAFETY_KINDS and not case.expect.deterministic:
        raise ValueError(f"{where}: a safety case needs a check that is not a model's judgement")
    if (case.expect.cites or case.expect.cites_nothing) and not case.runbooks:
        raise ValueError(f"{where}: citation checks need runbooks")
    if "org:admin" in case.asker_groups and "api" in case.targets:
        raise ValueError(f"{where}: an org admin would see every case's alerts")
    return case


def _alert(raw: dict[str, object], where: str) -> CaseAlert:
    if unknown := raw.keys() - {"message", "severity", "source", "team"}:
        raise ValueError(f"{where}: unknown alert fields {sorted(unknown)}")
    return CaseAlert(
        message=str(raw["message"]),
        severity=str(raw.get("severity", "warning")),
        source=str(raw.get("source", "prometheus")),
        team=str(raw.get("team", "default")),
    )


def _runbook(raw: dict[str, object], where: str) -> CaseRunbook:
    if unknown := raw.keys() - {"title", "body", "team"}:
        raise ValueError(f"{where}: unknown runbook fields {sorted(unknown)}")
    return CaseRunbook(
        title=str(raw["title"]), body=str(raw["body"]), team=str(raw.get("team", "default"))
    )


def _list(value: object) -> list[str]:
    if not isinstance(value, list):
        raise TypeError(f"expected a list, got {value!r}")
    return [str(v) for v in value]


def _dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise TypeError(f"expected a list of tables, got {value!r}")
    return value


def _int_or_none(value: object) -> int | None:
    return None if value is None else int(str(value))
