"""python -m evals: run eval cases, print a report, exit 1 if the gate fails.

    python -m evals                                   # model target, quality mode, LLM_* settings
    python -m evals --mode plumbing                   # what CI runs, with the mock
    python -m evals --target api                      # the running service, signed in
    python -m evals --llm-base-url http://ollama:11434/v1 --llm-model gpt-oss:20b --repeat 3
    python -m evals --baseline evals/baselines/gpt-oss-20b.json   # fail on regressions
    python -m evals --calibrate-judge --judge-model gemma3:27b --repeat 3   # trust the judge?

Run inside an api container (`make evals`): it uses the service's own
settings, prompt and model client, and - for the api target - mints
sessions the way `make session` does.
"""

import argparse
import asyncio
import json
import os
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx2
from pydantic import SecretStr

from app.cli import mint_session
from app.config import Settings, get_settings
from app.llm import OpenAICompatibleClient
from app.tracing import configure_tracing, shutdown_tracing
from evals.calibration import calibrate, calibration_markdown, load_labelled
from evals.cases import load_cases
from evals.retrieval import load_corpus, load_questions, retrieval_markdown, run_retrieval
from evals.run import Judge, gate, markdown, regressions, run, summarize
from evals.targets import ApiTarget, ModelTarget, Target

HERE = Path(__file__).parent


def parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m evals", description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--target",
        choices=["model", "api", "retrieval"],
        default="model",
        help="retrieval: the runbook search benchmark (evals/retrieval.toml), through the api",
    )
    p.add_argument("--mode", choices=["plumbing", "quality"], default="quality")
    p.add_argument("--cases", type=Path, default=HERE / "cases")
    p.add_argument("--kind", action="append", default=[], help="only these kinds (repeatable)")
    p.add_argument("--case", action="append", default=[], help="only these case ids (repeatable)")
    p.add_argument("--repeat", type=int, default=1, help="runs per case; a case passes if all do")
    p.add_argument("--judge", action="store_true", help="ask a model to grade `judge` criteria")
    p.add_argument("--judge-model", help="default: the model under test")
    p.add_argument(
        "--judge-base-url",
        help="default: the tested model's (the key: JUDGE_API_KEY, else the tested model's)",
    )
    p.add_argument(
        "--judge-temperature",
        type=float,
        default=0.0,
        help="default 0; OpenAI's reasoning models accept only 1",
    )
    p.add_argument("--judge-reasoning-effort", choices=["low", "medium", "high"])
    p.add_argument(
        "--calibrate-judge",
        action="store_true",
        help="grade the labelled answers in --calibration only: does the judge agree?",
    )
    p.add_argument("--calibration", type=Path, default=HERE / "judge_calibration.toml")
    p.add_argument("--corpus", type=Path, default=HERE / "runbooks", help="retrieval: runbooks")
    p.add_argument("--questions", type=Path, default=HERE / "retrieval.toml")
    p.add_argument("--k", type=int, default=5, help="retrieval: results per question")
    p.add_argument(
        "--search-mode",
        action="append",
        choices=["hybrid", "keyword", "semantic"],
        help="retrieval: modes to measure (repeatable; default all three)",
    )
    p.add_argument("--min-recall", type=float, help="retrieval: fail when hybrid recall@k is lower")
    p.add_argument("--llm-base-url", help="default: LLM_BASE_URL")
    p.add_argument("--llm-model", help="default: LLM_MODEL")
    p.add_argument("--llm-api-key", help="default: LLM_API_KEY (prefer the environment)")
    p.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high"],
        help="default: LLM_REASONING_EFFORT",
    )
    p.add_argument("--api-url", default="http://localhost:8010", help="api target: the api itself")
    p.add_argument("--origin", help="api target: default PUBLIC_URL")
    p.add_argument("--min-pass-rate", type=float, default=0.8)
    p.add_argument("--report", type=Path, help="default: evals/reports/<time>-<target>-<mode>.json")
    p.add_argument("--baseline", type=Path, help="a previous report: its passing cases must pass")
    return p.parse_args(argv)


def model_settings(args: argparse.Namespace, base: Settings, model: str | None = None) -> Settings:
    update: dict[str, object] = {}
    if args.llm_base_url:
        update["llm_base_url"] = args.llm_base_url
    if model or args.llm_model:
        update["llm_model"] = model or args.llm_model
    if args.llm_api_key:
        update["llm_api_key"] = SecretStr(args.llm_api_key)
    if args.reasoning_effort:
        update["llm_reasoning_effort"] = args.reasoning_effort
    return base.model_copy(update=update)


def judge_client(args: argparse.Namespace, base: Settings) -> OpenAICompatibleClient:
    update: dict[str, object] = {
        # Never the tested model's reasoning effort: a judge that is not a
        # reasoning model rejects the parameter (HTTP 400, measured).
        "llm_reasoning_effort": args.judge_reasoning_effort,
    }
    if args.judge_base_url:
        update["llm_base_url"] = args.judge_base_url
    if key := os.environ.get("JUDGE_API_KEY"):
        update["llm_api_key"] = SecretStr(key)
    settings = model_settings(args, base, args.judge_model).model_copy(update=update)
    return OpenAICompatibleClient(settings, temperature=args.judge_temperature)


def selected(args: argparse.Namespace, kind: str, case_id: str) -> bool:
    return (not args.kind or kind in args.kind) and (not args.case or case_id in args.case)


async def calibrate_main(args: argparse.Namespace, base: Settings) -> int:
    labelled = [
        x
        for x in load_labelled(args.calibration, load_cases(args.cases))
        if selected(args, x.case.kind, x.case.id)
    ]
    judge_llm = judge_client(args, base)
    judge = Judge(judge_llm)
    try:
        summary = await calibrate(judge, labelled, repeat=args.repeat)
    finally:
        await judge_llm.aclose()
    summary = {
        "judge_model": judge_llm.model,
        "temperature": args.judge_temperature,
        "repeat": args.repeat,
        **summary,
    }
    report = args.report or HERE / "reports" / (
        f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-calibration.json"
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(calibration_markdown(summary))
    print(f"\nreport: {report}")
    return 0 if all(row["agrees"] for row in summary["answers"]) else 1


async def retrieval_main(args: argparse.Namespace, base: Settings) -> int:
    corpus = load_corpus(args.corpus)
    questions = load_questions(args.questions, corpus)
    modes = args.search_mode or ["hybrid", "keyword", "semantic"]

    async def sessions(groups: tuple[str, ...], email: str) -> str:
        return await mint_session(email, list(groups), hours=1.0)

    async with httpx2.AsyncClient(base_url=args.api_url, timeout=30) as http:
        summary = await run_retrieval(
            http,
            sessions,
            args.origin or base.public_url,
            secrets.token_hex(3),
            corpus,
            questions,
            modes=modes,
            k=args.k,
        )
    summary = {"embedding_model": base.embedding_model, **summary}
    reasons = []
    hybrid = summary["modes"].get("hybrid")
    if (
        args.min_recall is not None
        and hybrid
        and (hybrid[f"recall@{args.k}"] or 0) < args.min_recall
    ):
        reasons.append(f"hybrid recall@{args.k} {hybrid[f'recall@{args.k}']} < {args.min_recall}")
    report = args.report or HERE / "reports" / (
        f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-retrieval.json"
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({**summary, "gate": reasons}, indent=2, ensure_ascii=False))
    print(f"embedding model: {base.embedding_model}\n")
    print(retrieval_markdown(summary))
    print("\n" + ("**FAIL:** " + "; ".join(reasons) if reasons else "**PASS**"))
    print(f"\nreport: {report}")
    return 1 if reasons else 0


async def main_async(args: argparse.Namespace) -> int:
    base = get_settings()
    # With OTEL_EXPORTER_OTLP_ENDPOINT set (make obs-up), each case's run is
    # a trace: its checks as events, and, through the api, the service's
    # own spans under it.
    if configure_tracing(base.model_copy(update={"otel_service_name": "triage-assistant-evals"})):
        try:
            return await _main(args, base)
        finally:
            shutdown_tracing()  # exports what is still buffered
    return await _main(args, base)


async def _main(args: argparse.Namespace, base: Settings) -> int:
    if args.calibrate_judge:
        return await calibrate_main(args, base)
    if args.target == "retrieval":
        return await retrieval_main(args, base)
    cases = [c for c in load_cases(args.cases) if selected(args, c.kind, c.id)]
    settings = model_settings(args, base)
    llm = OpenAICompatibleClient(settings)
    judge_llm = judge_client(args, base) if args.judge and args.mode == "quality" else None
    http = None
    target: Target
    if args.target == "model":
        target = ModelTarget(llm)
        model = settings.llm_model
    else:
        http = httpx2.AsyncClient(base_url=args.api_url, timeout=30)

        async def sessions(groups: tuple[str, ...], email: str) -> str:
            return await mint_session(email, list(groups), hours=1.0)

        target = ApiTarget(http, sessions, args.origin or base.public_url, secrets.token_hex(3))
        model = f"{base.llm_model} (the service's)"
    judge = Judge(judge_llm) if judge_llm else None
    try:
        results = await run(cases, target, mode=args.mode, repeat=args.repeat, judge=judge)
    finally:
        await llm.aclose()
        if judge_llm:
            await judge_llm.aclose()
        if http:
            await http.aclose()

    summary = summarize(results, target=args.target, mode=args.mode, model=model, judge=judge)
    reasons = gate(summary, min_pass_rate=args.min_pass_rate)
    regressed: list[str] = []
    if args.baseline:
        regressed = regressions(summary, json.loads(args.baseline.read_text()))
        reasons += [f"regressed against {args.baseline.name}: {r}" for r in regressed]
    report = args.report or HERE / "reports" / (
        f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{args.target}-{args.mode}.json"
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({**summary, "gate": reasons}, indent=2, ensure_ascii=False))
    print(markdown(summary, reasons, regressed))
    print(f"\nreport: {report}")
    return 1 if reasons else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(parse(argv)))


if __name__ == "__main__":
    sys.exit(main())
