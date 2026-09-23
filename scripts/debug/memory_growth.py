"""Memory growth per request, measured with tracemalloc (standard library).

Runs the app in-process (lifespan included) against the dev stack's
services, warms it up, then sends N requests between two heap snapshots
and prints the source lines whose live allocations grew the most. A few
KiB after thousands of requests is caches settling; growth that scales
with N is a leak. Run it twice with different N to tell them apart.

    docker compose run --rm -T -e N=2000 -e ALERTS_RATE_LIMIT=1000000 \\
        -e LOG_LEVEL=WARNING api python - < scripts/debug/memory_growth.py
"""

import asyncio
import gc
import os
import tracemalloc

import httpx

from app.main import create_app

N = int(os.environ.get("N", "2000"))
REQUEST = os.environ.get("REQUEST", "/alerts?limit=20")


def snapshot() -> tracemalloc.Snapshot:
    # Objects in reference cycles stay allocated until the cyclic GC runs:
    # collect first, or they look like growth. The in-process test client
    # (the httpx package) is not part of the server: leave it out.
    gc.collect()
    return tracemalloc.take_snapshot().filter_traces([tracemalloc.Filter(False, "*/httpx/*")])


async def main() -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            for _ in range(200):  # warm-up: pools, caches, lazy imports
                (await client.get(REQUEST)).raise_for_status()
            tracemalloc.start(25)
            before = snapshot()
            for _ in range(N):
                (await client.get(REQUEST)).raise_for_status()
            after = snapshot()
    stats = after.compare_to(before, "lineno")
    net = sum(s.size_diff for s in stats)
    print(f"{N} x GET {REQUEST}: {net / 1024:+.1f} KiB still allocated ({net / N:+.1f} B per request)")
    for s in stats[:8]:
        print(f"  {s.size_diff / 1024:+8.1f} KiB {s.count_diff:+7d} blocks  {s.traceback[0]}")


asyncio.run(main())
