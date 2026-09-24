"""The retrieval benchmark: does search find the section that answers a
question? Measured apart from the model, through the running service.

    python -m evals --target retrieval               # hybrid, keyword and semantic, k = 5

Saves every runbook in evals/runbooks/<team>/*.md through POST /runbooks, as
an admin of teams unique to the run, then asks each question of
retrieval.toml through POST /runbooks/search, in each mode, as a viewer of
all of those teams. Per mode:
- recall@k: the share of answerable questions with a relevant section among
  the first k results (k = 1, 3 and the run's k);
- MRR: the mean of 1/rank of the first relevant section (0 when it is not in
  the first k);
- distances (modes with a semantic side): the relevant sections' cosine
  distance to their question, and each negative question's best match. A
  cutoff that drops irrelevant sections needs the two apart.

A wrong answer from the assistant starts here: if retrieval never shows the
model the right section, no prompt can fix it.
"""

import statistics
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2

from app.runbooks import split_sections
from evals.targets import SessionMaker

HERE = Path(__file__).parent


@dataclass(frozen=True)
class CorpusRunbook:
    team: str
    title: str
    body: str


def load_corpus(directory: Path) -> list[CorpusRunbook]:
    """evals/runbooks/<team>/<name>.md; the first line is "# <title>"."""
    runbooks = []
    for path in sorted(directory.glob("*/*.md")):
        text = path.read_text()
        first = text.partition("\n")[0]
        if not first.startswith("# "):
            raise ValueError(f"{path.name}: the first line must be '# <title>'")
        runbooks.append(CorpusRunbook(team=path.parent.name, title=first[2:].strip(), body=text))
    return runbooks


@dataclass(frozen=True)
class Question:
    id: str
    query: str
    # Heading paths of the sections that answer it; empty: none does.
    relevant: tuple[str, ...]
    notes: str = ""


def load_questions(path: Path, corpus: Sequence[CorpusRunbook]) -> list[Question]:
    """A label naming a section that does not exist would silently count as
    a retrieval miss: every label is checked against the corpus's sections,
    split as the service splits them."""
    headings = {s.heading for r in corpus for s in split_sections(r.title, r.body)}
    with path.open("rb") as f:
        raw = tomllib.load(f).get("question", [])
    questions = []
    for item in raw:
        question = Question(
            id=str(item["id"]),
            query=str(item["query"]),
            relevant=tuple(str(h) for h in item.get("relevant", [])),
            notes=str(item.get("notes", "")),
        )
        if unknown := [h for h in question.relevant if h not in headings]:
            raise ValueError(f"{path.name}: {question.id}: no such section {unknown}")
        questions.append(question)
    ids = [q.id for q in questions]
    if duplicates := {i for i in ids if ids.count(i) > 1}:
        raise ValueError(f"duplicate question ids: {sorted(duplicates)}")
    return questions


def first_relevant(headings: Sequence[str], relevant: Sequence[str]) -> int | None:
    """1-based rank of the first relevant heading, or None."""
    return next((n for n, h in enumerate(headings, 1) if h in relevant), None)


def summarize_mode(rows: Sequence[dict[str, Any]], k: int) -> dict[str, Any]:
    answerable = [r for r in rows if r["relevant"]]
    negatives = [r for r in rows if not r["relevant"]]

    def recall(at: int) -> float | None:
        if not answerable:
            return None
        return sum(1 for r in answerable if r["rank"] is not None and r["rank"] <= at) / len(
            answerable
        )

    ranks = [r["rank"] for r in answerable]
    relevant_distances = [
        r["relevant_distance"] for r in answerable if r["relevant_distance"] is not None
    ]
    negative_distances = [r["top_distance"] for r in negatives if r["top_distance"] is not None]
    latencies = [r["latency_s"] for r in rows]
    return {
        "questions": len(rows),
        "answerable": len(answerable),
        **{f"recall@{at}": recall(at) for at in sorted({1, 3, k})},
        "mrr": (
            round(sum(1 / rank for rank in ranks if rank) / len(answerable), 3)
            if answerable
            else None
        ),
        "missed": [r["id"] for r in answerable if r["rank"] is None],
        "relevant_distance": _spread(relevant_distances),
        "negative_top_distance": _spread(negative_distances),
        "latency_p50_s": round(statistics.median(latencies), 4) if latencies else None,
    }


def _spread(values: Sequence[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "min": round(min(values), 3),
        "median": round(statistics.median(values), 3),
        "max": round(max(values), 3),
    }


async def run_retrieval(
    http: httpx2.AsyncClient,
    sessions: SessionMaker,
    origin: str,
    run_id: str,
    corpus: Sequence[CorpusRunbook],
    questions: Sequence[Question],
    *,
    modes: Sequence[str],
    k: int,
) -> dict[str, Any]:
    teams = sorted({r.team for r in corpus})
    slug = {team: f"ev{run_id}-{team}"[:63] for team in teams}
    admin = await sessions(
        tuple(f"team:{slug[t]}:admin" for t in teams), f"evals-rag-admin-{run_id}@example.com"
    )
    for runbook in corpus:
        saved = await http.post(
            "/runbooks",
            json={"team": slug[runbook.team], "title": runbook.title, "body": runbook.body},
            headers={"Cookie": admin, "Origin": origin},
        )
        saved.raise_for_status()

    by_mode: dict[str, Any] = {}
    rows_by_mode: dict[str, list[dict[str, Any]]] = {}
    for mode in modes:
        # An asker per mode: rate limits count per user.
        asker = await sessions(
            tuple(f"team:{slug[t]}:viewer" for t in teams),
            f"evals-rag-{mode}-{run_id}@example.com",
        )
        rows = []
        for question in questions:
            response = await http.post(
                "/runbooks/search",
                json={"query": question.query, "k": k, "mode": mode},
                headers={"Cookie": asker, "Origin": origin},
            )
            response.raise_for_status()
            data = response.json()
            hits = data["hits"]
            headings = [h["heading"] for h in hits]
            rank = first_relevant(headings, question.relevant)
            rows.append(
                {
                    "id": question.id,
                    "relevant": list(question.relevant),
                    "notes": question.notes,
                    "rank": rank,
                    "top": headings,
                    "top_distance": hits[0]["distance"] if hits else None,
                    "relevant_distance": hits[rank - 1]["distance"] if rank else None,
                    "latency_s": response.elapsed.total_seconds(),
                    "search_mode": data["mode"],
                    "embedding_error": data["embedding_error"],
                }
            )
        rows_by_mode[mode] = rows
        by_mode[mode] = summarize_mode(rows, k)
    return {"k": k, "modes": by_mode, "questions": rows_by_mode}


def retrieval_markdown(summary: dict[str, Any]) -> str:
    k = summary["k"]
    ats = sorted({1, 3, k})
    lines = [
        "| mode | " + " | ".join(f"recall@{at}" for at in ats) + " | MRR | p50 latency |",
        "|---|" + "---|" * (len(ats) + 2),
    ]
    for mode, m in summary["modes"].items():
        recalls = " | ".join(
            "-" if m[f"recall@{at}"] is None else f"{m[f'recall@{at}']:.2f}" for at in ats
        )
        latency = "-" if m["latency_p50_s"] is None else f"{m['latency_p50_s'] * 1000:.0f} ms"
        lines.append(f"| {mode} | {recalls} | {m['mrr']} | {latency} |")
    lines.append("")
    for mode, m in summary["modes"].items():
        if m["missed"]:
            lines.append(f"Not in the first {k} ({mode}): {', '.join(m['missed'])}")
        if m["relevant_distance"] or m["negative_top_distance"]:
            lines.append(
                f"Distances ({mode}): relevant sections {m['relevant_distance']}; "
                f"negatives' best match {m['negative_top_distance']}"
            )
    return "\n".join(lines)
