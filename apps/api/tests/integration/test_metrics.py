"""/metrics reflects real traffic. The test process runs one worker, so the
default registry is used; multiprocess mode is exercised by the production
image (docs/handbook/operations.md has the side-by-side)."""

import asyncio
import re

from fastapi import FastAPI
from httpx import AsyncClient
from prometheus_client import REGISTRY
from sqlalchemy import text

from app.db import watch_db_pool
from tests.integration.conftest import ClientFactory, MockLLM


def sample(text: str, name: str, **labels: str) -> float:
    wanted = ",".join(f'{k}="{v}"' for k, v in labels.items())
    for line in text.splitlines():
        if line.startswith(name + "{") and all(f'{k}="{v}"' in line for k, v in labels.items()):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"no sample {name}{{{wanted}}}")


async def test_http_metrics_use_route_templates(client: AsyncClient) -> None:
    before = await client.get("/metrics")
    base = _or_zero(before.text, route="/alerts/{alert_id}", status="404")
    await client.get("/alerts/424242")
    after = (await client.get("/metrics")).text
    assert (
        sample(after, "http_requests_total", route="/alerts/{alert_id}", status="404") == base + 1
    )
    assert 'route="/alerts/424242"' not in after  # raw paths would explode cardinality
    assert 'route="/metrics"' not in after  # scrapes are not traffic


async def test_llm_and_ratelimit_metrics_after_a_chat(
    client: AsyncClient, mock_llm: MockLLM
) -> None:
    before = (await client.get("/metrics")).text
    ok_before = _or_zero(before, "llm_requests_total", outcome="ok")
    await client.post("/chat/stream", json={"message": "metrics please"})
    text = (await client.get("/metrics")).text
    assert sample(text, "llm_requests_total", outcome="ok") == ok_before + 1
    assert sample(text, "llm_tokens_total", kind="completion") > 0
    assert sample(text, "llm_time_to_first_token_seconds_count") >= 1
    assert sample(text, "ratelimit_decisions_total", scope="chat", decision="allowed") >= 1


async def test_pool_gauge_counts_connections_in_use(app: FastAPI) -> None:
    def in_use() -> float | None:
        return REGISTRY.get_sample_value("db_pool_connections_in_use")

    watcher = asyncio.create_task(watch_db_pool(app.state.engine, capacity=5, interval_s=0.01))
    try:
        async with app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            await asyncio.sleep(0.05)
            assert in_use() == 1
        await asyncio.sleep(0.05)
        assert in_use() == 0
        assert REGISTRY.get_sample_value("db_pool_connections_max") == 5
    finally:
        watcher.cancel()


async def test_refused_requests_are_counted_by_reason(
    client: AsyncClient, anonymous: ClientFactory
) -> None:
    before = (await client.get("/metrics")).text
    async with anonymous() as nobody:
        await nobody.get("/alerts")
    await client.post("/alerts", json={}, headers={"Origin": "https://evil.example"})
    after = (await client.get("/metrics")).text
    for reason in ("no_session", "cross_origin"):
        was = _or_zero(before, "auth_rejections_total", reason=reason)
        assert sample(after, "auth_rejections_total", reason=reason) == was + 1


def _or_zero(text: str, name: str = "http_requests_total", **labels: str) -> float:
    try:
        return sample(text, name, **labels)
    except AssertionError:
        return 0.0


def test_sample_helper_matches_labels() -> None:
    text = 'x_total{a="1",b="2"} 3.0\nx_total{a="1",b="9"} 4.0'
    assert sample(text, "x_total", b="9") == 4.0
    assert re.search("x_total", text)
