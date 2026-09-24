"""The eval harness in plumbing mode, as CI runs it: the mock model, both
targets. Proves the harness, the service and the gate work end to end."""

import secrets
from pathlib import Path

import httpx2
from fastapi import FastAPI

from app.oidc import Identity
from app.sessions import session_cookie, sign_in
from evals.__main__ import main
from evals.cases import load_cases
from evals.retrieval import load_corpus, load_questions, run_retrieval
from evals.run import gate, run, summarize
from evals.targets import ApiTarget, SessionMaker
from tests.integration.conftest import BASE_URL, MockLLM

EVALS = Path(__file__).parents[2] / "evals"
CASES = EVALS / "cases"


def test_the_model_target_in_plumbing_mode_passes_its_gate(
    mock_llm: MockLLM, tmp_path: Path
) -> None:
    report = tmp_path / "report.json"
    assert main(["--mode", "plumbing", "--report", str(report)]) == 0
    assert report.exists()


def session_maker(app: FastAPI) -> SessionMaker:
    async def sessions(groups: tuple[str, ...], email: str) -> str:
        async with app.state.sessionmaker() as db:
            token = await sign_in(
                db,
                issuer="https://idp.test",
                identity=Identity(email, email, email, groups, None),
                settings=app.state.settings,
            )
            await db.commit()
        return f"{session_cookie(app.state.settings)}={token}"

    return sessions


async def test_the_api_target_proves_isolation_through_the_service(app: FastAPI) -> None:
    sessions = session_maker(app)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=BASE_URL
    ) as http:
        target = ApiTarget(http, sessions, BASE_URL, secrets.token_hex(3))
        cases = [c for c in load_cases(CASES) if c.kind == "isolation"]
        results = await run(cases, target, mode="plumbing")
    summary = summarize(results, target="api", mode="plumbing", model="mock")
    assert gate(summary, min_pass_rate=0.8) == []
    assert {c["id"]: c["passed"] for c in summary["cases"]} == {
        "isolation-other-teams-incident": True,
        "isolation-someone-in-no-such-team": True,
        "isolation-control-both-teams": True,
        # Another team's runbook never reaches the asker, even when asked for.
        "rag-isolation-break-glass": True,
    }


async def test_the_retrieval_benchmark_runs_through_the_service(app: FastAPI) -> None:
    corpus = load_corpus(EVALS / "runbooks")
    questions = load_questions(EVALS / "retrieval.toml", corpus)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=BASE_URL
    ) as http:
        summary = await run_retrieval(
            http,
            session_maker(app),
            BASE_URL,
            secrets.token_hex(3),
            corpus,
            questions,
            modes=["hybrid", "keyword"],
            k=5,
        )
    # The mock's embeddings are a bag of words: this proves the pipeline,
    # not a model. Real numbers: the handbook's RAG chapter.
    hybrid = summary["modes"]["hybrid"]
    assert hybrid["answerable"] == len([q for q in questions if q.relevant])
    assert hybrid["recall@5"] >= 0.8
    rows = summary["questions"]["keyword"]
    assert {row["search_mode"] for row in rows} == {"keyword"}
    assert all(row["top_distance"] is None for row in rows)  # keyword alone: no distances
