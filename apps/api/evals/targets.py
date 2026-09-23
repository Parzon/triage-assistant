"""Where a case's question goes: the model directly, or the running service."""

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx2

from app.llm import Finish, LLMClient, LLMError, Usage
from app.schemas import AlertOut
from app.triage import build_messages
from evals.cases import Case


@dataclass(frozen=True)
class Answer:
    text: str
    latency_s: float
    ttft_s: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # What the service put in the prompt (api target), else None.
    alerts_in_context: int | None = None
    error: str | None = None
    # "stop", "length" (cut off by the output limit)...: why the model stopped.
    finish_reason: str | None = None


class Target(Protocol):
    name: str

    async def ask(self, case: Case) -> Answer: ...


def context_alerts(case: Case) -> list[AlertOut]:
    """The case's alerts as the service would hand them to the prompt:
    newest first, a minute apart."""
    now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    return [
        AlertOut(
            id=n + 1,
            team=a.team,
            source=a.source,
            severity=a.severity,
            message=a.message,
            created_at=now - timedelta(minutes=n),
        )
        for n, a in enumerate(case.alerts)
    ]


async def collect(llm: LLMClient, messages: list[dict[str, str]]) -> Answer:
    """One streamed answer, timed like the service times it."""
    start = time.perf_counter()
    parts: list[str] = []
    ttft: float | None = None
    usage: Usage | None = None
    finish: str | None = None
    try:
        async for item in llm.stream(messages):
            if isinstance(item, Usage):
                usage = item
                continue
            if isinstance(item, Finish):
                finish = item.reason
                continue
            if ttft is None:
                ttft = time.perf_counter() - start
            parts.append(item)
    except LLMError as exc:
        # The provider's own words ("does not support thinking") are what
        # make a misconfiguration fixable; the code alone is not.
        cause = f" - {str(exc.__cause__)[:200]}" if exc.__cause__ else ""
        error = f"{exc.code}: {exc}{cause}"
        return Answer("".join(parts), time.perf_counter() - start, ttft, error=error)
    return Answer(
        "".join(parts),
        time.perf_counter() - start,
        ttft,
        usage.prompt_tokens if usage else None,
        usage.completion_tokens if usage else None,
        finish_reason=finish,
    )


class ModelTarget:
    """The production prompt, straight to the model."""

    name = "model"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def ask(self, case: Case) -> Answer:
        return await collect(self._llm, build_messages(case.question, context_alerts(case)))


# (groups, email) -> a session cookie "name=value"
SessionMaker = Callable[[tuple[str, ...], str], Awaitable[str]]


class ApiTarget:
    """The running service, signed in. Writes the case's alerts (as an org
    admin), then asks as a user with the case's groups. Every case gets its
    own teams (`ev<run>-<case>-<team>`), so no case sees another's alerts.
    Point it at a dev, test or staging stack - never production."""

    name = "api"

    def __init__(
        self, http: httpx2.AsyncClient, sessions: SessionMaker, origin: str, run_id: str
    ) -> None:
        self._http = http
        self._sessions = sessions
        self._origin = origin
        self._run = run_id
        self._cases = 0

    def _team(self, case_no: int, team: str) -> str:
        return f"ev{self._run}-{case_no}-{team}"[:63]

    def _groups(self, case_no: int, groups: tuple[str, ...]) -> tuple[str, ...]:
        out = []
        for group in groups:
            parts = group.split(":")
            if len(parts) == 3 and parts[0] == "team":
                out.append(f"team:{self._team(case_no, parts[1])}:{parts[2]}")
            else:
                out.append(group)
        return tuple(out)

    async def ask(self, case: Case) -> Answer:
        self._cases += 1
        n = self._cases
        teams = {a.team for a in case.alerts}
        # A seeder per case: rate limits count per user, and one seeder for a
        # whole run hit the alerts limit (429) within a minute.
        seeder = await self._sessions(
            ("org:admin", *(f"team:{self._team(n, t)}:viewer" for t in sorted(teams))),
            f"evals-seeder-{self._run}-{n}@example.com",
        )
        for alert in reversed(case.alerts):  # oldest first: the list is newest first
            response = await self._http.post(
                "/alerts",
                json={
                    "team": self._team(n, alert.team),
                    "source": alert.source,
                    "severity": alert.severity,
                    "message": alert.message,
                },
                headers={"Cookie": seeder, "Origin": self._origin},
            )
            response.raise_for_status()
        groups = case.asker_groups or tuple(f"team:{t}:viewer" for t in sorted(teams))
        asker = await self._sessions(self._groups(n, groups), f"evals-{self._run}-{n}@example.com")
        return await self._stream(case.question, asker)

    async def _stream(self, question: str, cookie: str) -> Answer:
        start = time.perf_counter()
        parts: list[str] = []
        ttft: float | None = None
        meta: dict[str, object] = {}
        done: dict[str, object] = {}
        error: str | None = None
        async with self._http.stream(
            "POST",
            "/chat/stream",
            json={"message": question},
            headers={"Cookie": cookie, "Origin": self._origin},
            timeout=180,
        ) as response:
            if response.status_code != 200:
                await response.aread()
                return Answer("", time.perf_counter() - start, error=f"http_{response.status_code}")
            event = ""
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event = line.removeprefix("event: ")
                elif line.startswith("data: "):
                    data = json.loads(line.removeprefix("data: "))
                    if event == "meta":
                        meta = data
                    elif event == "token":
                        if ttft is None:
                            ttft = time.perf_counter() - start
                        parts.append(data["delta"])
                    elif event == "done":
                        done = data
                    elif event == "error":
                        error = f"{data.get('code')}: {data.get('message')}"
        usage = done.get("usage") if isinstance(done.get("usage"), dict) else None
        in_context = meta.get("alerts_in_context")
        return Answer(
            "".join(parts),
            time.perf_counter() - start,
            ttft,
            int(usage["prompt_tokens"]) if usage else None,  # type: ignore[index]
            int(usage["completion_tokens"]) if usage else None,  # type: ignore[index]
            alerts_in_context=int(in_context) if isinstance(in_context, int) else None,
            error=error or (None if done else "stream_incomplete"),
            finish_reason=str(done["finish_reason"]) if done.get("finish_reason") else None,
        )
