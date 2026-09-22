"""Locust reference implementation.

    make load-tool TOOL=locust                 # AlertReader: the shared read scenario
    make load-tool TOOL=locust USERS=50 CLASS=ChatUser   # streaming, real time to first token

Locust tests are plain Python classes, which is its main strength for a
Python team: any client logic is just code. Here that means reading the SSE
stream line by line and recording the *client-side* time to the first
token - something k6, vegeta and oha cannot do, because they only see a
response once it has fully arrived.

Two caveats that shape the numbers:
- Locust is a closed model: a slow response delays that user's next
  request. constant_throughput() paces each user, but when the server is
  slower than the pacing, the offered rate drops (coordinated omission).
- One Locust process uses one CPU core (gevent). Beyond a few hundred
  requests/s per core, run `--processes -1` (one worker per core) or the
  load generator becomes the bottleneck.
"""

import os
import random
import time

import gevent
from locust import FastHttpUser, HttpUser, constant_throughput, events, task


class AlertReader(FastHttpUser):
    """The shared scenario: one list request per user per second, so
    USERS = requests per second. FastHttpUser (geventhttpclient) is several
    times cheaper per request than HttpUser (requests)."""

    wait_time = constant_throughput(1)

    def on_start(self) -> None:
        # Users are spawned in batches on whole-second boundaries; with a
        # fixed 1s pace they then all fire in the same instant, every second
        # - a thundering herd, not a steady rate. A random start offset
        # spreads them over the second. SYNC=1 keeps the herd (to see it).
        if os.environ.get("SYNC") != "1":
            gevent.sleep(random.random())

    @task
    def list_alerts(self) -> None:
        self.client.get("/api/alerts?limit=50", name="/api/alerts")


class ChatUser(HttpUser):
    """One streaming answer every 2 seconds per user. HttpUser (requests)
    because it can iterate a streamed body; FastHttpUser cannot."""

    wait_time = constant_throughput(0.5)

    @task
    def ask(self) -> None:
        start = time.perf_counter()
        with self.client.post(
            "/api/chat/stream",
            json={"message": "what is on fire?"},
            stream=True,
            catch_response=True,
            name="/api/chat/stream (whole answer)",
        ) as response:
            first_token = False
            for line in response.iter_lines():
                if line.startswith(b"event: token") and not first_token:
                    first_token = True
                    events.request.fire(
                        request_type="SSE",
                        name="time to first token",
                        response_time=(time.perf_counter() - start) * 1000,
                        response_length=0,
                        exception=None,
                        context={},
                    )
                elif line.startswith(b"event: done"):
                    response.success()
                    return
                elif line.startswith(b"event: error"):
                    response.failure("the stream ended with an error event")
                    return
            response.failure("the stream ended without a done event")
