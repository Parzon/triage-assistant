"""The AI observability lab's helper, run inside the dev api container by
the `lab` wrapper (which also injects and removes the incidents).

    labs/ai-observability/lab ask "a question" [--as team:lab:viewer]
    labs/ai-observability/lab trace <trace id>

The exercises are in README.md; what was measured, in answers.md.
"""

import argparse
import asyncio
import json
import urllib.request

import httpx2

from app.cli import mint_session
from app.config import get_settings

API = "http://localhost:8010"
JAEGER = "http://jaeger:16686"
# What the tree prints for each span; everything else is in Jaeger's UI.
SHOWN = (
    "app.chat.outcome", "app.chat.retrieval", "app.chat.citations", "app.chat.invalid_citations",
    "app.prompt.alerts", "app.prompt.sections", "app.prompt.redactions",
    "app.retrieval.mode", "app.retrieval.hits", "app.retrieval.embedding_error",
    "app.retrieval.embedding_key", "gen_ai.retrieval.top_k", "gen_ai.retrieval.documents",
    "gen_ai.request.model", "gen_ai.request.max_tokens", "gen_ai.request.reasoning.level",
    "gen_ai.prompt.version", "gen_ai.response.time_to_first_chunk", "gen_ai.response.finish_reasons",
    "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "app.llm.outcome",
    "app.llm.reasoning_chunks", "app.llm.content_chunks", "error.type",
    "gen_ai.input.messages", "gen_ai.retrieval.query.text", "user.id",
)


async def ask(args: argparse.Namespace) -> None:
    settings = get_settings()
    email = args.as_.replace(":", "-") + "@lab.example.com"
    cookie = await mint_session(email, [args.as_], hours=1)
    meta: dict[str, object] = {}
    done: dict[str, object] = {}
    answer: list[str] = []
    async with httpx2.AsyncClient(base_url=API, timeout=180) as http:
        async with http.stream(
            "POST",
            "/chat/stream",
            json={"message": args.question},
            headers={"Cookie": cookie, "Origin": settings.public_url},
        ) as response:
            event = ""
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event = line.removeprefix("event: ")
                elif line.startswith("data: "):
                    data = json.loads(line.removeprefix("data: "))
                    if event == "meta":
                        meta = data
                    elif event == "token":
                        answer.append(data["delta"])
                    elif event in ("done", "error"):
                        done = {"event": event, **data}
    text = "".join(answer).strip()
    print(text[:700] + ("..." if len(text) > 700 else "") or "(no answer)")
    print(f"\nretrieval: {meta.get('retrieval')}, sections: {meta.get('runbooks_in_context')}, "
          f"alerts: {meta.get('alerts_in_context')}")
    print(f"end: {done.get('event')} {done.get('finish_reason') or done.get('code') or ''}")
    print(f"trace: {meta.get('trace_id')}")
    if meta.get("trace_id"):
        print(f"  lab trace {meta['trace_id']}")
        print(f"  http://localhost:16686/trace/{meta['trace_id']}")


def trace(args: argparse.Namespace) -> None:
    with urllib.request.urlopen(f"{JAEGER}/api/traces/{args.trace_id}") as response:
        data = json.load(response)["data"][0]
    spans = {s["spanID"]: s for s in data["spans"]}
    children: dict[str | None, list[dict[str, object]]] = {}
    for span in data["spans"]:
        parent = next(
            (r["spanID"] for r in span.get("references", []) if r["refType"] == "CHILD_OF"), None
        )
        children.setdefault(parent if parent in spans else None, []).append(span)
    start = min(s["startTime"] for s in data["spans"])

    def show(span: dict[str, object], depth: int) -> None:
        pad = "  " * depth
        tags = {t["key"]: t["value"] for t in span["tags"]}  # type: ignore[attr-defined]
        offset = (span["startTime"] - start) / 1000  # type: ignore[operator]
        print(f"{pad}{span['operationName']}  at {offset:.0f} ms, took {span['duration'] / 1000:.1f} ms")  # type: ignore[operator]
        for key in SHOWN:
            if key in tags:
                value = str(tags[key])
                print(f"{pad}    {key} = {value[:160]}{'...' if len(value) > 160 else ''}")
        if not args.all and str(span["operationName"]).split()[0] in ("SELECT", "WITH", "connect"):
            return
        for log in span.get("logs", []):  # type: ignore[attr-defined]
            fields = {f["key"]: str(f["value"])[:120] for f in log["fields"]}
            name = fields.pop("event", "event")
            fields.pop("exception.stacktrace", None)  # in Jaeger's UI, in full
            print(f"{pad}    event '{name}' at {(log['timestamp'] - start) / 1000:.0f} ms {fields or ''}")
        for child in sorted(children.get(str(span["spanID"]), []), key=lambda c: c["startTime"]):  # type: ignore[arg-type,return-value]
            show(child, depth + 1)

    for root in children[None]:
        show(root, 0)
    services = sorted({p["serviceName"] for p in data["processes"].values()})
    print(f"\n{len(data['spans'])} spans from {', '.join(services)}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="lab")
    commands = parser.add_subparsers(dest="command", required=True)
    ask_cmd = commands.add_parser("ask")
    ask_cmd.add_argument("question")
    ask_cmd.add_argument("--as", dest="as_", default="team:lab:viewer", help="the asker's group")
    trace_cmd = commands.add_parser("trace")
    trace_cmd.add_argument("trace_id")
    trace_cmd.add_argument("--all", action="store_true", help="SQL spans' children and events too")
    args = parser.parse_args()
    if args.command == "ask":
        asyncio.run(ask(args))
    else:
        trace(args)


if __name__ == "__main__":
    main()
