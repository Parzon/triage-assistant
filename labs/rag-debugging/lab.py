"""The RAG debugging lab's helper: look at each stage on its own.

Run from the repo root through the wrapper, which runs this file inside the
dev api container (its settings, its database, its api on localhost:8010):

    labs/rag-debugging/lab seed                    # the eval corpus, into team "lab"
    labs/rag-debugging/lab sections platform/disk-full.md [--max-words 30] [--naive]
    labs/rag-debugging/lab search "a question" [--mode keyword|semantic|hybrid] [--k 5]
    labs/rag-debugging/lab ask "a question"
    labs/rag-debugging/lab unseed

The exercises are in README.md; what was measured, in answers.md.
"""

import argparse
import asyncio
import json
import re
from pathlib import Path

import httpx2

from app.cli import mint_session
from app.config import get_settings
from app.runbooks import Section, split_sections

API = "http://localhost:8010"
CORPUS = Path("/api/evals/runbooks")
TEAM = "lab"
HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


async def cookie(role: str) -> str:
    return await mint_session(f"rag-lab-{role}@example.com", [f"team:{TEAM}:{role}"], hours=1)


def headers(session: str) -> dict[str, str]:
    return {"Cookie": session, "Origin": get_settings().public_url}


def naive_sections(title: str, markdown: str) -> list[Section]:
    """Splits at every line that looks like a heading - code blocks included."""
    sections, path, lines = [], [title], []
    for line in markdown.splitlines():
        match = HEADING.match(line)
        if match:
            if "\n".join(lines).strip():
                sections.append(Section(" > ".join(path), "\n".join(lines).strip()))
            path, lines = [title, match[2]], []
        else:
            lines.append(line)
    if "\n".join(lines).strip():
        sections.append(Section(" > ".join(path), "\n".join(lines).strip()))
    return sections


def show_sections(args: argparse.Namespace) -> None:
    text = (CORPUS / args.file).read_text()
    title = text.partition("\n")[0][2:].strip()
    split = naive_sections(title, text) if args.naive else split_sections(title, text, args.max_words)
    for n, section in enumerate(split, 1):
        preview = " ".join(section.content.split())[:70]
        print(f"{n:2}. {section.heading}  ({len(section.content.split())} words)\n    {preview}")


async def seed(args: argparse.Namespace) -> None:
    admin = await cookie("admin")
    async with httpx2.AsyncClient(base_url=API, timeout=60) as http:
        for path in sorted(CORPUS.glob("*/*.md")):
            text = path.read_text()
            title = text.partition("\n")[0][2:].strip()
            body = {"team": TEAM, "title": title, "body": text}
            response = await http.post("/runbooks", json=body, headers=headers(admin))
            response.raise_for_status()
            saved = response.json()
            state = "saved" if saved["changed"] else "unchanged"
            print(f"{state}: {title} ({saved['sections']} sections)")


async def unseed(args: argparse.Namespace) -> None:
    admin = await cookie("admin")
    async with httpx2.AsyncClient(base_url=API, timeout=60) as http:
        listed = (await http.get("/runbooks", headers=headers(admin))).json()["items"]
        for runbook in listed:
            if runbook["team"] == TEAM:
                await http.delete(f"/runbooks/{runbook['id']}", headers=headers(admin))
                print(f"deleted: {runbook['title']}")


async def search(args: argparse.Namespace) -> None:
    viewer = await cookie("viewer")
    body = {"query": args.question, "mode": args.mode, "k": args.k, "team": TEAM}
    async with httpx2.AsyncClient(base_url=API, timeout=60) as http:
        response = await http.post("/runbooks/search", json=body, headers=headers(viewer))
        response.raise_for_status()
        found = response.json()
    print(f"mode {found['mode']}" + (f" (embedding: {found['embedding_error']})" if found["embedding_error"] else ""))
    print(" # | score  | keyword | semantic | distance | section")
    for n, hit in enumerate(found["hits"], 1):
        distance = "" if hit["distance"] is None else f"{hit['distance']:.3f}"
        print(
            f"{n:2} | {hit['score']:.4f} | {hit['keyword_rank'] or '':>7} | "
            f"{hit['semantic_rank'] or '':>8} | {distance:>8} | {hit['heading']}"
        )


async def ask(args: argparse.Namespace) -> None:
    viewer = await cookie("viewer")
    answer, meta, done = [], {}, {}
    async with httpx2.AsyncClient(base_url=API, timeout=180) as http:
        async with http.stream(
            "POST", "/chat/stream", json={"message": args.question}, headers=headers(viewer)
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
                        done = data
    print(
        f"in context: {meta.get('alerts_in_context')} alerts, "
        f"{meta.get('runbooks_in_context')} runbook sections (retrieval: {meta.get('retrieval')})\n"
    )
    print("".join(answer).strip() or f"(no answer: {done})")
    print(f"\ncited: {[c['ref'] + ' ' + c['heading'] for c in done.get('citations', [])]}")
    if done.get("invalid_citations"):
        print(f"invented: {done['invalid_citations']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="lab")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed")
    commands.add_parser("unseed")
    sections = commands.add_parser("sections")
    sections.add_argument("file", help="a path under evals/runbooks, e.g. platform/disk-full.md")
    sections.add_argument("--max-words", type=int, default=300)
    sections.add_argument("--naive", action="store_true", help="split inside code blocks too")
    search_cmd = commands.add_parser("search")
    search_cmd.add_argument("question")
    search_cmd.add_argument("--mode", choices=["hybrid", "keyword", "semantic"], default="hybrid")
    search_cmd.add_argument("--k", type=int, default=5)
    ask_cmd = commands.add_parser("ask")
    ask_cmd.add_argument("question")
    args = parser.parse_args()
    if args.command == "sections":
        show_sections(args)
    else:
        asyncio.run({"seed": seed, "unseed": unseed, "search": search, "ask": ask}[args.command](args))


if __name__ == "__main__":
    main()
