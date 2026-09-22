"""/metrics reflects real traffic. The test process runs one worker, so the
default registry is used; multiprocess mode is exercised by the production
image (see the observability chapter for the side-by-side)."""

import re

from httpx import AsyncClient

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
    client_for: ClientFactory, mock_llm: MockLLM
) -> None:
    async with client_for("192.0.2.90") as client:
        before = (await client.get("/metrics")).text
        ok_before = _or_zero(before, "llm_requests_total", outcome="ok")
        await client.post("/chat/stream", json={"message": "metrics please"})
        text = (await client.get("/metrics")).text
    assert sample(text, "llm_requests_total", outcome="ok") == ok_before + 1
    assert sample(text, "llm_tokens_total", kind="completion") > 0
    assert sample(text, "llm_time_to_first_token_seconds_count") >= 1
    assert sample(text, "ratelimit_decisions_total", scope="chat", decision="allowed") >= 1


def _or_zero(text: str, name: str = "http_requests_total", **labels: str) -> float:
    try:
        return sample(text, name, **labels)
    except AssertionError:
        return 0.0


def test_sample_helper_matches_labels() -> None:
    text = 'x_total{a="1",b="2"} 3.0\nx_total{a="1",b="9"} 4.0'
    assert sample(text, "x_total", b="9") == 4.0
    assert re.search("x_total", text)
