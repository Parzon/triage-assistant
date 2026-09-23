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

from sqlalchemy import select

from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.models import User
from app.oidc import Identity
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
    args = parser.parse_args(argv)

    if args.command == "session":
        print(asyncio.run(mint_session(args.email, args.group, args.hours)))
    else:
        ended = asyncio.run(revoke(args.email))
        print(f"ended {ended} session(s) of {args.email}", file=sys.stderr)


if __name__ == "__main__":
    main()
