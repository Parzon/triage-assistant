"""Steady reads through nginx for DURATION seconds; one summary line.

Run by scripts/failure-drills.sh on the compose network while a dependency
is frozen mid-run. 20 client threads, each sending its next request as
soon as the last one ends, so every thread is waiting on a request when
the freeze begins: those in-flight requests are what the drill is about.
    python steady_reads.py DURATION [--detail]
"""

import collections
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

URL = "http://web:8080/api/alerts?limit=5"
# A signed-in user's session ("name=value"), minted by failure-drills.sh.
HEADERS = {"Cookie": os.environ["SESSION_COOKIE"]}
THREADS = 20
duration = float(sys.argv[1])
t0 = time.monotonic()
results: list[tuple[float, float, str]] = []
lock = threading.Lock()


def one() -> None:
    start = time.monotonic()
    try:
        request = urllib.request.Request(URL, headers=HEADERS)  # noqa: S310
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            status = str(response.status)
    except urllib.error.HTTPError as e:
        status = str(e.code)
    except Exception as e:  # noqa: BLE001 - any failure is a result here
        status = type(e).__name__
    with lock:
        results.append((start - t0, time.monotonic() - start, status))


def worker() -> None:
    while time.monotonic() - t0 < duration:
        one()


with ThreadPoolExecutor(THREADS) as pool:
    for _ in range(THREADS):
        pool.submit(worker)

by_status: dict[str, list[float]] = collections.defaultdict(list)
for _, took, status in results:
    by_status[status].append(took)
print(" · ".join(f"{len(t)}×{s} (slowest {max(t):.1f}s)" for s, t in sorted(by_status.items())))

if "--detail" in sys.argv:
    buckets: dict[int, list[tuple[float, str]]] = collections.defaultdict(list)
    for start, took, status in results:
        buckets[int(start // 5) * 5].append((took, status))
    for b in sorted(buckets):
        counts = collections.Counter(s for _, s in buckets[b])
        print(f"  started {b:>3}-{b + 5:<3}s  slowest {max(t for t, _ in buckets[b]):5.1f}s  {dict(counts)}")
