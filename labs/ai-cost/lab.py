"""The AI cost lab's helper, run inside the dev api container by the `lab`
wrapper (README.md). The model and embedding settings are the api's own:
point them at a real model first (`lab up`).

    lab seed                 alerts for team "lab", so prompts carry some
    lab measure [n]          n benchmark questions through the api: tokens per answer
    lab tokens "<question>"  where one prompt's tokens go, part by part
    lab cache                would a semantic cache answer the right question?
    lab throughput <n> [s]   the model server alone, n answers at a time
"""

import argparse
import asyncio
import json
import statistics
import time
import tomllib
from pathlib import Path

import httpx2
from sqlalchemy import select

from app.cli import mint_session
from app.config import get_settings
from app.db import create_engine, create_sessionmaker, set_transaction_settings
from app.llm import OpenAICompatibleClient
from app.models import Alert, Team
from app.queries import newest_alerts
from app.runbooks import search_runbooks
from app.triage import build_messages

API = "http://localhost:8010"
TEAM = "lab"
QUESTIONS = Path("/api/evals/retrieval.toml")

# List prices in USD per million tokens (input, output), read from the
# providers' pages on 2026-09-24. Prices change: check before quoting.
# https://platform.claude.com/docs/en/about-claude/pricing
# https://developers.openai.com/api/docs/pricing
PRICES = {
    "Claude Haiku 4.5": (1.00, 5.00),
    "Claude Sonnet 5": (2.00, 10.00),
    "GPT-5 mini": (0.25, 2.00),
}

ALERTS = [
    ("critical", "prometheus", "disk 96% full on db-1: /var/lib/postgresql at 192G of 200G"),
    ("warning", "prometheus", "WAL archiving on db-1 is 14 minutes behind"),
    ("high", "grafana", "p95 latency 2.4s on checkout-api for 10 minutes"),
    ("warning", "prometheus", "pgbouncer cl_waiting at 38 on pool payments"),
    ("info", "deploy-bot", "checkout-api v2.31.0 deployed to production by ana"),
    ("warning", "blackbox", "TLS certificate for api.example.com expires in 9 days"),
    ("high", "prometheus", "5xx rate 4.1% on payments-api (threshold 1%)"),
    ("warning", "prometheus", "replication lag on db-2 is 42 seconds"),
    ("info", "cron", "nightly backup of db-1 finished in 48 minutes"),
    ("critical", "blackbox", "health check failing on refund-worker for 5 minutes"),
    ("warning", "prometheus", "memory 91% on cache-1 (valkey), evictions rising"),
    ("warning", "prometheus", "node disk 85% on ci-runner-3"),
    ("info", "deploy-bot", "payments-api v1.9.4 deployed to production by ben"),
    ("high", "prometheus", "queue depth 12,400 on refunds, consumers 0"),
    ("warning", "grafana", "error budget for login 40% spent this week"),
    ("warning", "prometheus", "certificate renewal job failed twice on edge-1"),
    ("info", "prometheus", "autovacuum running for 3 hours on table events"),
    ("high", "blackbox", "DNS resolution slow from eu-west: 900 ms p95"),
    ("warning", "prometheus", "connection count 180 of 200 on db-1"),
    ("info", "cron", "log rotation freed 12G on web-2"),
    ("warning", "prometheus", "CPU steal 25% on worker-7"),
    ("critical", "prometheus", "db-1 transaction id wraparound warning: 150M left"),
    ("warning", "blackbox", "status page check slow: 3.2 s"),
    ("info", "deploy-bot", "feature flag new-checkout enabled for 10% of users"),
    ("warning", "prometheus", "inode usage 88% on db-1 /var/log"),
]


def questions() -> list[dict[str, object]]:
    return list(tomllib.loads(QUESTIONS.read_text())["question"])


async def session(role: str) -> dict[str, str]:
    cookie = await mint_session(f"cost-{role}@lab.example.com", [f"team:{TEAM}:{role}"], hours=2)
    return {"Cookie": cookie, "Origin": get_settings().public_url}


async def seed() -> None:
    engine = create_engine(get_settings())
    async with create_sessionmaker(engine)() as db:
        await set_transaction_settings(db, {"app.org_admin": "on"})
        team = await db.scalar(select(Team.id).where(Team.slug == TEAM))
        have = 0
        if team is not None:
            have = len((await db.scalars(select(Alert.id).where(Alert.team_id == team))).all())
    await engine.dispose()
    if have >= len(ALERTS):
        print(f"team {TEAM} already has {have} alerts")
        return
    who = await session("responder")
    async with httpx2.AsyncClient(base_url=API, timeout=30) as http:
        for severity, source, message in ALERTS:
            body = {"team": TEAM, "severity": severity, "source": source, "message": message}
            (await http.post("/alerts", headers=who, json=body)).raise_for_status()
    print(f"added {len(ALERTS)} alerts to team {TEAM}")


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


async def billed(http: httpx2.AsyncClient) -> dict[str, float]:
    """The api's own token counters (llm_tokens_total), summed by kind:
    they count failed answers too, which the provider bills all the same."""
    totals: dict[str, float] = {"prompt": 0.0, "completion": 0.0}
    for line in (await http.get("/metrics")).text.splitlines():
        if line.startswith("llm_tokens_total{"):
            kind = line.split('kind="')[1].split('"')[0]
            totals[kind] = totals.get(kind, 0.0) + float(line.rsplit(" ", 1)[1])
    return totals


async def measure(n: int) -> None:
    who = await session("viewer")
    rows = []
    failed: dict[str, int] = {}
    async with httpx2.AsyncClient(base_url=API, timeout=300) as http:
        before = await billed(http)
        for q in questions()[:n]:
            meta: dict[str, object] = {}
            done: dict[str, object] = {}
            start = time.perf_counter()
            async with http.stream(
                "POST", "/chat/stream", headers=who, json={"message": q["query"]}
            ) as response:
                response.raise_for_status()
                event = ""
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        event = line.removeprefix("event: ")
                    elif line.startswith("data: ") and event in ("meta", "done", "error"):
                        data = json.loads(line.removeprefix("data: "))
                        if event == "meta":
                            meta = data
                        elif event == "done":
                            done = data
                        else:
                            code = str(data.get("error", {}).get("code", data))
                            failed[code] = failed.get(code, 0) + 1
                            print(f"  {q['id']}: error {code}")
            usage = done.get("usage") or {}
            row = {
                "id": q["id"],
                "alerts": meta.get("alerts_in_context"),
                "sections": meta.get("runbooks_in_context"),
                "prompt": usage.get("prompt_tokens"),
                "completion": usage.get("completion_tokens"),
                "seconds": round(time.perf_counter() - start, 2),
            }
            rows.append(row)
            print(json.dumps(row))
        after = await billed(http)
    ok = [r for r in rows if r["prompt"] is not None]
    asked = len(rows)
    spent = {kind: after[kind] - before.get(kind, 0.0) for kind in after}
    print(f"\n{asked} questions: {len(ok)} answered; failed: {failed or 'none'}")
    print(
        f"Billed per question, failed answers included (llm_tokens_total): "
        f"prompt {spent['prompt'] / asked:.0f}, completion {spent['completion'] / asked:.0f}"
    )
    if not ok:
        return
    prompt = [float(r["prompt"]) for r in ok]
    completion = [float(r["completion"]) for r in ok]
    print("Tokens per answered question (p50 / p95 / mean):")
    for label, values in (("prompt", prompt), ("completion", completion)):
        p50, p95, mean = pct(values, 0.5), pct(values, 0.95), statistics.mean(values)
        print(f"  {label:<10} {p50:6.0f} / {p95:6.0f} / {mean:6.0f}")
    print("\nWhat 1,000 such questions would cost at list prices (2026-09-24), as billed:")
    for name, (usd_in, usd_out) in PRICES.items():
        per_question = (spent["prompt"] * usd_in + spent["completion"] * usd_out) / 1e6 / asked
        print(f"  {name:<18} ${1000 * per_question:7.2f}")


async def prompt_tokens(llm: OpenAICompatibleClient, messages: list[dict[str, str]]) -> int:
    """The prompt's size as the model counts it: one call, one output token."""
    response = await llm._client.chat.completions.create(
        model=llm.model,
        messages=messages,  # type: ignore[arg-type]
        max_tokens=1,
        extra_body=llm._extra or None,
    )
    return int(response.usage.prompt_tokens) if response.usage else 0


async def tokens(question: str) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    llm = OpenAICompatibleClient(settings)
    try:
        async with create_sessionmaker(engine)() as db:
            team = await db.scalar(select(Team.id).where(Team.slug == TEAM))
            if team is None:
                raise SystemExit(f"no team {TEAM}: run the RAG lab's setup, then lab seed")
            await set_transaction_settings(db, {"app.read_team_ids": f"{{{team}}}"})
            retrieval = await search_runbooks(
                db, llm, settings, question, [team], k=settings.rag_context_chunks
            )
            alerts = await newest_alerts(db, [team], limit=settings.chat_context_alerts)
        hits = retrieval.hits
        full = await prompt_tokens(llm, build_messages(question, alerts, hits))
        no_alerts = await prompt_tokens(llm, build_messages(question, [], hits))
        no_sections = await prompt_tokens(llm, build_messages(question, alerts, []))
        bare = await prompt_tokens(llm, build_messages(question, [], []))
        print(f"model {llm.model}; {len(alerts)} alerts, {len(hits)} sections in context\n")
        print("Prompt tokens:")
        print(f"  instructions + question  {bare:6d}")
        print(f"  the alerts               {full - no_alerts:6d}")
        print(f"  the runbook sections     {full - no_sections:6d}")
        print(f"  the whole prompt         {full:6d}")

        reasoning = content = 0
        usage = None
        stream = await llm._client.chat.completions.create(
            model=llm.model,
            messages=build_messages(question, alerts, hits),  # type: ignore[arg-type]
            max_tokens=settings.llm_max_output_tokens,
            stream=True,
            stream_options={"include_usage": True},
            extra_body=llm._extra or None,
        )
        async for chunk in stream:
            if chunk.usage is not None:
                usage = chunk.usage
            for choice in chunk.choices:
                delta = choice.delta
                if getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None):
                    reasoning += 1
                if delta.content:
                    content += 1
        if usage is not None:
            print("\nThe answer:")
            print(f"  completion tokens        {usage.completion_tokens:6d}")
            print(f"  streamed as: {reasoning} reasoning chunks, {content} answer chunks")
    finally:
        await llm.aclose()
        await engine.dispose()


async def throughput(concurrency: int, seconds: int) -> None:
    """The model server alone, under `concurrency` simultaneous answers to
    this lab's own prompt, for `seconds`: output tokens per second, and
    what a million of them costs at a cloud GPU's hourly price."""
    settings = get_settings()
    llm = OpenAICompatibleClient(settings)
    question = str(questions()[0]["query"])
    engine = create_engine(settings)
    async with create_sessionmaker(engine)() as db:
        team = await db.scalar(select(Team.id).where(Team.slug == TEAM))
        await set_transaction_settings(db, {"app.read_team_ids": f"{{{team}}}"})
        hits = (await search_runbooks(db, llm, settings, question, [team], k=4)).hits
        alerts = await newest_alerts(db, [team], limit=settings.chat_context_alerts)
    await engine.dispose()
    messages = build_messages(question, alerts, hits)
    done = {"answers": 0, "completion": 0}
    end = time.monotonic() + seconds

    async def worker() -> None:
        while time.monotonic() < end:
            response = await llm._client.chat.completions.create(
                model=llm.model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=settings.llm_max_output_tokens,
                extra_body=llm._extra or None,
            )
            done["answers"] += 1
            done["completion"] += response.usage.completion_tokens if response.usage else 0

    start = time.monotonic()
    try:
        await asyncio.gather(*(worker() for _ in range(concurrency)))
    finally:
        await llm.aclose()
    elapsed = time.monotonic() - start
    tps = done["completion"] / elapsed
    print(
        f"{concurrency} at a time, {elapsed:.0f} s: {done['answers']} answers, "
        f"{tps:.0f} output tokens/s, {done['answers'] / elapsed:.2f} answers/s"
    )
    # One L40S (the same chip class as an RTX 6000 Ada): AWS g6e.xlarge,
    # on demand, us-east-1, $1.861 an hour (2026-09-24).
    print(
        f"  at $1.861 per GPU-hour: ${1.861 / (tps * 3600) * 1e6:.2f} per million output tokens, "
        f"${1.861 / (done['answers'] / elapsed * 3600) * 1000:.2f} per 1,000 answers"
    )


async def cache() -> None:
    """Pairs of benchmark questions: close enough to share a cached answer at
    a given distance cutoff, and whether they should (the same labelled
    sections answer both). Negative questions never should."""
    settings = get_settings()
    llm = OpenAICompatibleClient(settings)
    qs = questions()
    try:
        vectors = await llm.embed([settings.embedding_query_prefix + str(q["query"]) for q in qs])
    finally:
        await llm.aclose()

    def distance(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm = (sum(x * x for x in a) * sum(y * y for y in b)) ** 0.5
        return 1 - dot / norm

    pairs = []
    for i in range(len(qs)):
        for j in range(i + 1, len(qs)):
            a, b = set(qs[i]["relevant"]), set(qs[j]["relevant"])  # type: ignore[arg-type]
            same = bool(a) and a == b
            pairs.append((distance(vectors[i], vectors[j]), same, qs[i]["id"], qs[j]["id"]))
    pairs.sort()
    should = sum(same for _, same, _, _ in pairs)
    print(f"{len(qs)} questions, {len(pairs)} pairs; {should} pairs share their answer.\n")
    print("cutoff  pairs served from cache  right  wrong  of the sharing pairs")
    for cutoff in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30):
        hits = [p for p in pairs if p[0] < cutoff]
        right = sum(same for _, same, _, _ in hits)
        print(
            f"{cutoff:6.2f}  {len(hits):23d}  {right:5d}  {len(hits) - right:5d}  {right}/{should}"
        )
    print("\nThe ten closest pairs:")
    for d, same, a, b in pairs[:10]:
        print(f"  {d:.3f}  {'same answer ' if same else 'DIFFERENT   '} {a}  /  {b}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="lab")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed")
    m = sub.add_parser("measure")
    m.add_argument("n", nargs="?", type=int, default=22)
    t = sub.add_parser("tokens")
    t.add_argument("question")
    sub.add_parser("cache")
    tp = sub.add_parser("throughput")
    tp.add_argument("concurrency", type=int)
    tp.add_argument("seconds", nargs="?", type=int, default=60)
    args = parser.parse_args()
    if args.command == "seed":
        asyncio.run(seed())
    elif args.command == "measure":
        asyncio.run(measure(args.n))
    elif args.command == "tokens":
        asyncio.run(tokens(args.question))
    elif args.command == "cache":
        asyncio.run(cache())
    else:
        asyncio.run(throughput(args.concurrency, args.seconds))


if __name__ == "__main__":
    main()
