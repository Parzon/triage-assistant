"""The readiness probe answers within its deadline, whatever the check does."""

import asyncio
import time

from app.routes import health
from app.routes.health import probe


async def succeeds() -> None:
    await asyncio.sleep(0)


async def fails() -> None:
    raise ConnectionError("refused")


async def test_success_and_failure() -> None:
    assert await probe(succeeds(), timeout_s=1) is True
    assert await probe(fails(), timeout_s=1) is False


async def test_answers_on_time_and_leaves_a_slow_check_running() -> None:
    finished = asyncio.Event()

    async def slow() -> None:
        await asyncio.sleep(0.2)
        finished.set()

    started = time.monotonic()
    assert await probe(slow(), timeout_s=0.05) is False
    assert time.monotonic() - started < 0.15
    (task,) = health._abandoned
    assert not task.cancelled()  # a cancel would make asyncpg wait on the server
    await finished.wait()
    await task
    await asyncio.sleep(0)  # done-callbacks run on the next loop iteration
    assert not health._abandoned


async def test_a_check_failing_after_its_deadline_is_retrieved_quietly() -> None:
    async def late_failure() -> None:
        await asyncio.sleep(0.1)
        raise ConnectionError("gone")

    assert await probe(late_failure(), timeout_s=0.01) is False
    (task,) = health._abandoned
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert not health._abandoned
