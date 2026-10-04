"""Check that lock_timeout holds for a whole migration run.

It once did not: env.py set it with SET LOCAL, and the first autocommit block
(CREATE INDEX CONCURRENTLY) committed it away - 5s at the start of a run, 0
for everything after. This runs two probe migrations through this
directory's env.py against a scratch database: the first records the setting
before, inside and after an autocommit block, the second records it again.
Any value but 5s fails.

    python migrations/check_lock_timeout.py     (make test-api runs it)

Needs MIGRATIONS_DATABASE_URL: the schema owner, allowed to create a database.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
from sqlalchemy.engine import make_url

HERE = Path(__file__).parent
SCRATCH = "lock_timeout_check"
EXPECTED = "5s"

FIRST = """from alembic import op
revision = "probe1"
down_revision = None
def upgrade():
    op.execute("CREATE TABLE probe (step text, value text)")
    op.execute("INSERT INTO probe SELECT 'start', current_setting('lock_timeout')")
    op.execute("CREATE TABLE t (x int)")
    with op.get_context().autocommit_block():
        op.execute("INSERT INTO probe SELECT 'in the block', current_setting('lock_timeout')")
        op.execute("CREATE INDEX CONCURRENTLY ix_t ON t (x)")
    op.execute("INSERT INTO probe SELECT 'after the block', current_setting('lock_timeout')")
"""
SECOND = """from alembic import op
revision = "probe2"
down_revision = "probe1"
def upgrade():
    op.execute("INSERT INTO probe SELECT 'the next migration', current_setting('lock_timeout')")
"""


def plain(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def admin(url: str, *statements: str) -> None:
    conn = await asyncpg.connect(plain(url))
    try:
        for statement in statements:
            await conn.execute(statement)
    finally:
        await conn.close()


async def read(url: str) -> list[tuple[str, str]]:
    conn = await asyncpg.connect(plain(url))
    try:
        return [(r["step"], r["value"]) for r in await conn.fetch("SELECT step, value FROM probe")]
    finally:
        await conn.close()


def main() -> int:
    owner = os.environ["MIGRATIONS_DATABASE_URL"]
    scratch = make_url(owner).set(database=SCRATCH).render_as_string(hide_password=False)
    drop = f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)'
    asyncio.run(admin(owner, drop, f'CREATE DATABASE "{SCRATCH}"'))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            shutil.copy(HERE / "env.py", work / "env.py")
            (work / "versions").mkdir()
            (work / "versions" / "probe1.py").write_text(FIRST)
            (work / "versions" / "probe2.py").write_text(SECOND)
            (work / "alembic.ini").write_text(f"[alembic]\nscript_location = {work}\n")
            env = {**os.environ, "MIGRATIONS_DATABASE_URL": scratch, "PYTHONPATH": str(HERE.parent)}
            # Fixed arguments, this interpreter: nothing here comes from input.
            ini = str(work / "alembic.ini")
            alembic = [sys.executable, "-m", "alembic", "-c", ini, "upgrade", "head"]
            subprocess.run(alembic, env=env, check=True)  # noqa: S603
        rows = asyncio.run(read(scratch))
    finally:
        asyncio.run(admin(owner, drop))
    for step, value in rows:
        print(f"lock_timeout {step}: {value}")
    wrong = [step for step, value in rows if value != EXPECTED]
    if wrong or len(rows) != 4:
        print(f"FAIL: lock_timeout must be {EXPECTED} for the whole run", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
