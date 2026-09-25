"""Operator commands, run inside an api container (`make session`, `make
revoke`): they use the app's own settings and database role.

  python -m app.cli session --email load@example.com --group team:default:viewer
      Signs a user in without the identity provider and prints the session
      cookie ("name=value"), for load tests, drills and smoke tests. The
      user is recorded under a separate issuer, so it can never be mistaken
      for - or take over - a real account. Anyone who can run this can
      already read the database, so it grants nothing new.

  python -m app.cli revoke --email alice@example.com
      Ends every session of a user now. Role changes in the identity
      provider apply at the next sign-in; this makes them immediate.

  python -m app.cli reembed
      Embeds again every runbook embedded another way than today's settings
      say (model, dimensions, document prefix). Until then, retrieval ignores
      those sections' vectors - keyword search still finds them - so run it
      after changing any EMBEDDING_* setting but the query prefix.

  python -m app.cli audit [--action runbook.saved] [--target 17] [--hours 24]
      The audit trail, newest first, one JSON object per line (app/audit.py):
      who wrote a runbook's versions, what the assistant was given.

  python -m app.cli assistant [--off --reason "..." | --on]
      The assistant's off switch (app/switch.py): its state, or switch it.
      For operators, and for when nobody can sign in: it needs neither the
      identity provider nor an org admin's session. Audited as the CLI.
"""

import os

# A one-off process shares its container with the server. Its metrics must
# not land in the directory the server's workers share - there they would
# linger, and prometheus_client switches to that mode when the variable
# merely EXISTS (an empty value still counts). Dropped before any app module
# imports prometheus_client, which decides at import time.
os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import func, select

from app import switch
from app.audit import CLI, events, to_out
from app.config import get_settings
from app.db import create_engine, create_sessionmaker, set_transaction_settings
from app.llm import OpenAICompatibleClient
from app.models import AssistantSwitch, Runbook, Team, User
from app.oidc import Identity
from app.runbooks import embedding_key, save_runbook
from app.schemas import AssistantIn
from app.sessions import session_cookie, sign_in, sign_out_everywhere

CLI_ISSUER = "urn:triage-assistant:cli"


async def mint_session(email: str, groups: list[str], hours: float) -> str:
    settings = get_settings().model_copy(update={"session_max_age_s": int(hours * 3600)})
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as db:
            identity = Identity(
                subject=email, email=email, name=email, groups=tuple(groups), id_token=None
            )
            token = await sign_in(db, issuer=CLI_ISSUER, identity=identity, settings=settings)
            await db.commit()
    finally:
        await engine.dispose()
    return f"{session_cookie(settings)}={token}"


async def revoke(email: str) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as db:
            user_ids = list(await db.scalars(select(User.id).where(User.email == email)))
            ended = sum([await sign_out_everywhere(db, user_id) for user_id in user_ids])
            await db.commit()
    finally:
        await engine.dispose()
    return ended


async def reembed() -> tuple[int, int]:
    """(runbooks embedded again, runbooks in total)."""
    settings = get_settings()
    if settings.embedding_model is None:
        raise SystemExit("EMBEDDING_MODEL is not set: runbook search is off")
    key = embedding_key(settings)
    engine = create_engine(settings)
    llm = OpenAICompatibleClient(settings)
    try:
        async with create_sessionmaker(engine)() as db:
            # Every team's runbooks: an operator command, like a migration.
            await set_transaction_settings(db, {"app.org_admin": "on"})
            total = await db.scalar(select(func.count()).select_from(Runbook)) or 0
            stale = (
                await db.execute(
                    select(Runbook.title, Runbook.body, Runbook.source_url, Team)
                    .join(Team, Team.id == Runbook.team_id)
                    .where(Runbook.embedding_key != key)
                )
            ).all()
            for title, body, source_url, team in stale:
                await save_runbook(db, llm, settings, team, title, body, source_url, CLI)
    finally:
        await llm.aclose()
        await engine.dispose()
    return len(stale), total


async def audit_trail(
    action: str | None, target: int | None, actor: int | None, hours: float | None, limit: int
) -> list[str]:
    settings = get_settings()
    engine = create_engine(settings)
    since = None if hours is None else datetime.now(UTC) - timedelta(hours=hours)
    try:
        async with create_sessionmaker(engine)() as db:
            # Row-level security shows audit rows to org admins only.
            await set_transaction_settings(db, {"app.org_admin": "on"})
            stmt = events(
                action=action, target_id=target, actor_user_id=actor, since=since, limit=limit
            )
            rows = (await db.execute(stmt)).tuples().all()
    finally:
        await engine.dispose()
    return [to_out(*row).model_dump_json() for row in rows]


async def assistant(change: AssistantIn | None) -> AssistantSwitch:
    """The switch's state, after `change` if one is given."""
    settings = get_settings()
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as db:
            # Row-level security lets org admins alone change the switch.
            await set_transaction_settings(db, {"app.org_admin": "on"})
            if change is None:
                return await switch.current(db)
            row = await switch.turn(db, CLI, enabled=change.enabled, reason=change.reason)
            await db.commit()
            return row
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    session = commands.add_parser("session", help="print a session cookie for a script")
    session.add_argument("--email", required=True)
    session.add_argument(
        "--group",
        action="append",
        default=[],
        help='as the identity provider would send it: "team:<slug>:<role>" or "org:admin"',
    )
    session.add_argument("--hours", type=float, default=1.0)
    revoke_cmd = commands.add_parser("revoke", help="end every session of a user")
    revoke_cmd.add_argument("--email", required=True)
    commands.add_parser("reembed", help="embed again the runbooks embedded another way")
    audit_cmd = commands.add_parser("audit", help="the audit trail, newest first")
    audit_cmd.add_argument("--action", help="e.g. runbook.saved, chat.asked")
    audit_cmd.add_argument("--target", type=int, help="a runbook's or an alert's id")
    audit_cmd.add_argument("--actor", type=int, help="a user's id")
    audit_cmd.add_argument("--hours", type=float, help="only the last N hours")
    audit_cmd.add_argument("--limit", type=int, default=50)
    switch_cmd = commands.add_parser("assistant", help="the assistant's off switch")
    turn = switch_cmd.add_mutually_exclusive_group()
    turn.add_argument("--off", action="store_true", help="refuse every question (needs --reason)")
    turn.add_argument("--on", action="store_true", help="answer again")
    switch_cmd.add_argument("--reason", help="why it is off: everyone who asks reads it")
    args = parser.parse_args(argv)

    if args.command == "session":
        print(asyncio.run(mint_session(args.email, args.group, args.hours)))
    elif args.command == "revoke":
        ended = asyncio.run(revoke(args.email))
        print(f"ended {ended} session(s) of {args.email}", file=sys.stderr)
    elif args.command == "audit":
        lines = asyncio.run(
            audit_trail(args.action, args.target, args.actor, args.hours, args.limit)
        )
        print("\n".join(lines))
    elif args.command == "assistant":
        change = None
        if args.off or args.on:
            try:
                change = AssistantIn(enabled=args.on, reason=args.reason)
            except ValidationError as exc:
                raise SystemExit(f"assistant: {exc.errors()[0]['msg']}") from exc
        row = asyncio.run(assistant(change))
        state = "on" if row.enabled else f"off: {row.reason}"
        print(f"the assistant is {state} (since {row.changed_at:%Y-%m-%d %H:%M:%S %Z})")
    else:
        done, total = asyncio.run(reembed())
        print(f"embedded again {done} of {total} runbook(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
