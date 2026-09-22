"""CPU cost per streamed chunk: openai SDK vs raw HTTP streaming + json.loads."""
import asyncio, json, resource, time
import httpx2
from openai import AsyncOpenAI

BASE, STREAMS, ROUNDS = "http://mock-llm:8020/v1", 100, 3
BODY = {"model": "mock-1", "messages": [{"role": "user", "content": "bench"}], "stream": True}

def cpu() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF); return r.ru_utime + r.ru_stime

async def via_sdk(client: AsyncOpenAI) -> int:
    n = 0
    stream = await client.chat.completions.create(**BODY)
    async with stream:
        async for chunk in stream:
            n += 1
    return n

async def via_raw(client: httpx2.AsyncClient) -> int:
    n = 0
    async with client.stream("POST", "/chat/completions", json=BODY) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                json.loads(line[6:]); n += 1
    return n

async def run(label, make_one):
    total_chunks, c0 = 0, cpu()
    for _ in range(ROUNDS):
        total_chunks += sum(await asyncio.gather(*(make_one() for _ in range(STREAMS))))
    used = cpu() - c0
    print(f"{label:28s} {total_chunks:6d} chunks  {used*1e6/total_chunks:6.1f} us CPU / chunk")

async def main():
    sdk = AsyncOpenAI(base_url=BASE, api_key="k")
    raw = httpx2.AsyncClient(base_url=BASE, headers={"Authorization": "Bearer k"}, timeout=60)
    await run("openai SDK (typed chunks)", lambda: via_sdk(sdk))
    await run("raw httpx2 + json.loads", lambda: via_raw(raw))

asyncio.run(main())
