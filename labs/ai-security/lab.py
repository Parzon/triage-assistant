"""The AI security lab's scenario, run inside the dev api container by the
`lab` wrapper: `labs/ai-security/lab setup`.

Team audit-lab has a runbook, "Disk full". Over a few seconds:
  1. ana saves it (version 1), and cara asks a question;
  2. ben edits it: his version tells people to delete files in the
     database's data directory - a step that destroys the database;
  3. dev and eli ask questions;
  4. ana notices and fixes it (version 3), and cara asks again.
An engineer now says the assistant told them to delete the data directory.
The exercise (README.md, exercise 2) is to find out, from the audit trail
alone, who wrote that step, when, who was given it, and when it was fixed.
"""

import asyncio
import sys

import httpx2

from app.cli import mint_session
from app.config import get_settings

API = "http://localhost:8010"
TEAM = "audit-lab"
TITLE = "Disk full"

GOOD = """Steps for a database host that runs out of disk.

## Free space
Delete old WAL archives only after a successful backup, then rotate the logs.

## Expand the volume
Grow the disk in the cloud console, then the filesystem with resize2fs.
"""
BAD = GOOD.replace(
    "Delete old WAL archives only after a successful backup, then rotate the logs.",
    "Delete the files in /var/lib/postgresql/data to free space quickly.",
)
FIXED = GOOD + "\n## Never\nNever delete files in the data directory by hand.\n"


async def session(name: str, role: str) -> dict[str, str]:
    cookie = await mint_session(f"{name}@lab.example.com", [f"team:{TEAM}:{role}"], hours=1)
    return {"Cookie": cookie, "Origin": get_settings().public_url}


async def save(http: httpx2.AsyncClient, who: dict[str, str], body: str) -> None:
    response = await http.post(
        "/runbooks", headers=who, json={"team": TEAM, "title": TITLE, "body": body}
    )
    response.raise_for_status()


async def ask(http: httpx2.AsyncClient, who: dict[str, str], question: str) -> None:
    async with http.stream(
        "POST", "/chat/stream", headers=who, json={"message": question}
    ) as response:
        response.raise_for_status()
        async for _ in response.aiter_bytes():
            pass


async def setup() -> None:
    ana, ben = await session("ana", "admin"), await session("ben", "admin")
    cara, dev, eli = [await session(n, "viewer") for n in ("cara", "dev", "eli")]
    async with httpx2.AsyncClient(base_url=API, timeout=300) as http:
        await save(http, ana, GOOD)
        await ask(http, cara, "The disk on db-1 is full. What do I do?")
        await save(http, ben, BAD)
        await ask(http, dev, "db-1 is out of disk space, how do I free some?")
        await ask(http, eli, "What is the fastest way to free disk on the database host?")
        await save(http, ana, FIXED)
        await ask(http, cara, "Disk full again on db-1, steps please?")
    print(
        f'Done: team {TEAM}, runbook "{TITLE}", three versions, four questions.\n'
        "Now exercise 2 in labs/ai-security/README.md: who wrote the bad step, "
        "and who was given it?"
    )


if __name__ == "__main__":
    if sys.argv[1:] != ["setup"]:
        sys.exit("usage: labs/ai-security/lab setup")
    asyncio.run(setup())
