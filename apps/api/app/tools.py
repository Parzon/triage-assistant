"""The assistant's tools: read-only, and acting as the person asking.

Two clients call them (ADR-0020): the agent (CHAT_MODE=agent, app/agent.py),
whose model picks them as it answers, and the MCP server (app/mcp_server.py),
which lets another assistant use them. Both go through call(), under the
rules in docs/handbook/ai-security.md:
- **As the asker.** Each call reads through the api's own queries with the
  asker's principal: their teams, row-level security underneath. No
  argument names a team or a user, so no text in a model's context can
  widen what a call reads.
- **Read-only.** No call changes what anyone reads.
- **Validated and bounded.** Arguments are checked against the schema (at
  most 20 alerts, 8 sections, a 300-character query), and each call has a
  time limit. A bad call gets an error result that says what was wrong, so
  the model can correct it.
- **Recorded.** Every call that reads is an audit event (tool.called: what
  was asked for, the query as a hash, the ids read) and a span
  (execute_tool).

What they return is untrusted data - anyone who can send an alert or edit a
runbook controls its text - with credentials redacted, as in the pipeline.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.access import Principal, tenant_settings
from app.audit import Actor, record, sha256
from app.config import Settings
from app.db import set_transaction_settings
from app.llm import Embedder
from app.metrics import prompt_redactions, tool_calls
from app.queries import newest_alerts
from app.redact import redact
from app.runbooks import Hit, search_runbooks
from app.schemas import Severity
from app.triage import alert_line, section_text

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

TIMEOUT_S = 15.0


class ListAlertsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Severity | None = None
    limit: int = Field(10, ge=1, le=20)


class SearchRunbooksArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=300)
    k: int = Field(4, ge=1, le=8)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    # The arguments' JSON Schema. Written out, not generated from the
    # models above that check them: it is the tool's contract with the
    # model, reviewed like prompt text.
    parameters: dict[str, Any]

    def as_openai(self) -> dict[str, Any]:
        """The tool as the OpenAI-compatible chat API takes it."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


TOOLS = {
    tool.name: tool
    for tool in (
        Tool(
            "list_alerts",
            "The asker's recent alerts, newest first: severity, time, team, source, "
            "message. Optionally only one severity.",
            {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["info", "warning", "high", "critical"],
                        "description": "Only alerts of this severity.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "How many alerts, at most (default 10).",
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            "search_runbooks",
            "Sections of the asker's team runbooks that best match the query, most "
            "relevant first, each numbered for citation, like [R1].",
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look for: symptoms, a component, an error.",
                    },
                    "k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 8,
                        "description": "How many sections, at most (default 4).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
    )
}


@dataclass
class ToolContext:
    """Who the calls act for, how they reach the data, and what they have
    read so far."""

    principal: Principal
    sessionmaker: async_sessionmaker[AsyncSession]
    embedder: Embedder
    settings: Settings
    # Where the calls come from, for the audit trail: the agent in the api,
    # or an MCP client.
    via: Literal["api", "mcp"] = "api"
    # Every section a search returned, in order: [R1] is sections[0].
    # Citations are checked against this list.
    sections: list[Hit] = field(default_factory=list)
    # Every alert a call returned.
    alert_ids: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class Result:
    text: str  # what the model reads
    ok: bool
    summary: str  # what the person is shown: counts, never content


def label(name: str) -> str:
    """The tool's name for metrics, spans and logs. The name is a model's
    output: anything that is not a tool becomes "unknown", never the text."""
    return name if name in TOOLS else "unknown"


async def call(name: str, arguments: str | dict[str, Any] | None, ctx: ToolContext) -> Result:
    """Run one tool call: `arguments` as the model wrote them (JSON text) or
    as a client sent them (an object). Errors come back as results."""
    tool = label(name)
    start = time.perf_counter()
    with tracer.start_as_current_span(
        f"execute_tool {tool}",
        attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool},
    ) as span:
        result = await _run(name, arguments, ctx)
        outcome = "ok" if result.ok else "error"
        span.set_attributes({"app.tool.outcome": outcome, "app.tool.summary": result.summary})
    tool_calls.labels(tool, outcome, ctx.via).inc()
    log.info(
        "tool called",
        extra={
            "tool": tool,
            "outcome": outcome,
            "summary": result.summary,
            "via": ctx.via,
            "duration_ms": round((time.perf_counter() - start) * 1000, 1),
        },
    )
    return result


async def _run(name: str, arguments: str | dict[str, Any] | None, ctx: ToolContext) -> Result:
    if name not in TOOLS:
        return Result(f"Unknown tool. The tools are {', '.join(TOOLS)}.", False, "unknown tool")
    try:
        raw = arguments if isinstance(arguments, dict) else json.loads(arguments or "{}")
        async with asyncio.timeout(TIMEOUT_S):
            if name == "search_runbooks":
                return await _search_runbooks(SearchRunbooksArgs.model_validate(raw), ctx)
            return await _list_alerts(ListAlertsArgs.model_validate(raw), ctx)
    except (json.JSONDecodeError, ValidationError) as exc:
        return Result(f"Invalid arguments: {_first_error(exc)}", False, "invalid arguments")
    except TimeoutError:
        return Result("The tool timed out. Answer with what you have.", False, "timed out")


async def _list_alerts(args: ListAlertsArgs, ctx: ToolContext) -> Result:
    async with ctx.sessionmaker() as db:
        await set_transaction_settings(db, tenant_settings(ctx.principal))
        alerts = await newest_alerts(
            db, ctx.principal.team_ids(), limit=args.limit, severity=args.severity
        )
        await record(
            db,
            Actor(ctx.principal.user_id, ctx.via),
            "tool.called",
            tool="list_alerts",
            severity=args.severity,
            limit=args.limit,
            alerts=[a.id for a in alerts],
        )
        await db.commit()
    ctx.alert_ids.update(a.id for a in alerts)
    if not alerts:
        return Result("(none)", True, "no alerts")
    which = f"{args.severity} " if args.severity else ""
    return Result(
        "\n".join(alert_line(a, _clean) for a in alerts), True, f"{len(alerts)} {which}alerts"
    )


async def _search_runbooks(args: SearchRunbooksArgs, ctx: ToolContext) -> Result:
    async with ctx.sessionmaker() as db:
        await set_transaction_settings(db, tenant_settings(ctx.principal))
        # Embeds the query first, with no transaction open (search_runbooks).
        retrieval = await search_runbooks(
            db, ctx.embedder, ctx.settings, args.query, ctx.principal.team_ids(), k=args.k
        )
        await record(
            db,
            Actor(ctx.principal.user_id, ctx.via),
            "tool.called",
            tool="search_runbooks",
            query_sha256=sha256(args.query),
            k=args.k,
            retrieval=retrieval.mode,
            sections=[
                {
                    "chunk_id": h.chunk_id,
                    "runbook_id": h.runbook_id,
                    "runbook_sha256": h.runbook_sha256,
                }
                for h in retrieval.hits
            ],
        )
        await db.commit()
    if not retrieval.hits:
        return Result("(no runbook sections)", True, "no sections")
    blocks = []
    for hit in retrieval.hits:
        # A section read before keeps its number: one section, one citation.
        number = next(
            (n for n, s in enumerate(ctx.sections, 1) if s.chunk_id == hit.chunk_id), None
        )
        if number is None:
            ctx.sections.append(hit)
            number = len(ctx.sections)
        blocks.append(section_text(number, hit, _clean))
    return Result("\n\n".join(blocks), True, f"{len(retrieval.hits)} runbook sections")


def _clean(text: str) -> str:
    text, found = redact(text)
    if found:
        prompt_redactions.inc(found)
    return text


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        error = exc.errors()[0]
        where = ".".join(str(p) for p in error["loc"]) or "arguments"
        return f"{where}: {error['msg']}"
    return "not valid JSON"
