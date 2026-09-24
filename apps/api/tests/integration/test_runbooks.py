"""Runbooks through the api (app/routes/runbooks.py, RFC-0001): who may
write, read and search them; saves that cost nothing when nothing changed;
search that falls back to keywords when the embedding model fails; and the
assistant getting the asker's runbook sections, and no one else's."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.cli import reembed
from app.main import create_app
from tests.integration.conftest import MockLLM, SignIn, signed_in, started
from tests.integration.test_chat import events_of

DISK = """Steps for a database host that runs out of disk.

## Free space
Delete old WAL archives after a successful backup, then rotate the logs.

## Expand the volume
Grow the disk in the cloud console, then the filesystem with resize2fs.
"""
CERTS = """## Renew a certificate
Run the renewal job, then check the expiry date with openssl."""


async def save(client: AsyncClient, team: str, title: str, body: str = DISK) -> dict[str, object]:
    response = await client.post("/runbooks", json={"team": team, "title": title, "body": body})
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_a_team_admin_saves_a_runbook_and_an_unchanged_save_costs_nothing(
    sign_in_as: SignIn, mock_llm: MockLLM
) -> None:
    admin = await sign_in_as("team:payments:admin")
    first = await save(admin, "payments", "Disk full")
    assert (first["team"], first["sections"], first["changed"]) == ("payments", 3, True)
    embedded = (await mock_llm.stats())["embedded_texts"]

    again = await save(admin, "payments", "Disk full")
    assert (again["id"], again["changed"]) == (first["id"], False)
    assert (await mock_llm.stats())["embedded_texts"] == embedded  # nothing re-embedded

    edited = await save(admin, "payments", "Disk full", DISK + "\n## Call someone\nPage the DBA.")
    assert (edited["id"], edited["sections"], edited["changed"]) == (first["id"], 4, True)


async def test_only_a_team_admin_writes_that_teams_runbooks(sign_in_as: SignIn) -> None:
    body = {"team": "payments", "title": "x", "body": DISK}
    responder = await sign_in_as("team:payments:responder")
    assert (await responder.post("/runbooks", json=body)).status_code == 403
    outsider = await sign_in_as("team:platform:admin")
    assert (await outsider.post("/runbooks", json=body)).status_code == 404
    unknown = {**body, "team": "no-such-team"}
    assert (await outsider.post("/runbooks", json=unknown)).status_code == 404


async def test_writes_need_this_sites_origin(sign_in_as: SignIn) -> None:
    admin = await sign_in_as("team:payments:admin")
    response = await admin.post(
        "/runbooks",
        json={"team": "payments", "title": "x", "body": DISK},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


async def test_invalid_runbooks_are_refused(sign_in_as: SignIn) -> None:
    admin = await sign_in_as("team:payments:admin")
    for body in (
        {"team": "payments", "title": "", "body": DISK},
        {"team": "payments", "title": "x", "body": ""},
        {"team": "payments", "title": "x", "body": DISK, "source_url": "javascript:alert(1)"},
    ):
        assert (await admin.post("/runbooks", json=body)).status_code == 422, body


async def test_members_list_and_read_only_their_teams_runbooks(sign_in_as: SignIn) -> None:
    admin = await sign_in_as("team:payments:admin", "team:platform:admin")
    ids = {
        team: (await save(admin, team, f"{team} disk"))["id"] for team in ("payments", "platform")
    }
    viewer = await sign_in_as("team:payments:viewer")
    listed = (await viewer.get("/runbooks")).json()["items"]
    assert [(r["team"], r["title"], r["sections"]) for r in listed] == [
        ("payments", "payments disk", 3)
    ]
    assert (await viewer.get(f"/runbooks/{ids['payments']}")).json()["body"] == DISK
    assert (await viewer.get(f"/runbooks/{ids['platform']}")).status_code == 404
    nobody = await sign_in_as()
    assert (await nobody.get("/runbooks")).json() == {"items": []}


async def test_deleting_a_runbook_removes_its_sections_from_search(sign_in_as: SignIn) -> None:
    admin = await sign_in_as("team:payments:admin")
    runbook = await save(admin, "payments", "Disk full")
    viewer = await sign_in_as("team:payments:viewer")
    assert (await viewer.delete(f"/runbooks/{runbook['id']}")).status_code == 403
    assert (await admin.delete(f"/runbooks/{runbook['id']}")).status_code == 204
    assert (await admin.delete(f"/runbooks/{runbook['id']}")).status_code == 404
    search = await viewer.post("/runbooks/search", json={"query": "free disk space"})
    assert search.json()["hits"] == []


async def test_search_finds_the_right_section_among_the_askers_teams_only(
    sign_in_as: SignIn,
) -> None:
    admin = await sign_in_as("team:payments:admin", "team:platform:admin")
    await save(admin, "payments", "Disk full")
    await save(admin, "payments", "Certificates", CERTS)
    await save(admin, "platform", "Disk full")
    viewer = await sign_in_as("team:payments:viewer")

    found = (
        await viewer.post("/runbooks/search", json={"query": "how do I free disk space?", "k": 3})
    ).json()
    assert found["mode"] == "hybrid"
    first = found["hits"][0]
    assert first["heading"] == "Disk full > Free space"
    assert first["keyword_rank"] is not None
    assert first["semantic_rank"] is not None
    assert {hit["team"] for hit in found["hits"]} == {"payments"}

    other = await viewer.post("/runbooks/search", json={"query": "disk", "team": "platform"})
    assert other.status_code == 404


async def test_an_org_admin_searches_every_team(sign_in_as: SignIn) -> None:
    admin = await sign_in_as("team:payments:admin", "team:platform:admin")
    await save(admin, "payments", "Disk full")
    await save(admin, "platform", "Disk full")
    org = await sign_in_as("org:admin")
    found = await org.post("/runbooks/search", json={"query": "free disk space", "k": 10})
    assert {hit["team"] for hit in found.json()["hits"]} == {"payments", "platform"}


async def test_changing_how_vectors_are_made_needs_a_reembed(
    app: FastAPI, sign_in_as: SignIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = await sign_in_as("team:payments:admin")
    await save(admin, "payments", "Disk full")
    # A new document prefix: the stored vectors were made another way.
    changed = app.state.settings.model_copy(update={"embedding_document_prefix": "doc: "})
    app.state.settings = changed
    semantic = {"query": "free disk space", "mode": "semantic"}
    assert (await admin.post("/runbooks/search", json=semantic)).json()["hits"] == []
    # A hybrid search in name only says so.
    hybrid = (await admin.post("/runbooks/search", json={"query": "free disk space"})).json()
    assert (hybrid["mode"], hybrid["embedding_error"]) == ("keyword_only", "no_current_vectors")

    monkeypatch.setattr("app.cli.get_settings", lambda: changed)
    assert await reembed() == (1, 1)
    hits = (await admin.post("/runbooks/search", json=semantic)).json()["hits"]
    assert hits[0]["heading"] == "Disk full > Free space"
    assert await reembed() == (0, 1)  # nothing left to do


async def test_search_falls_back_to_keywords_when_the_embedding_model_fails(
    sign_in_as: SignIn, mock_llm: MockLLM
) -> None:
    admin = await sign_in_as("team:payments:admin")
    await save(admin, "payments", "Disk full")
    await mock_llm.configure(embed_fail_mode="http_500")

    found = (await admin.post("/runbooks/search", json={"query": "free disk space"})).json()
    assert (found["mode"], found["embedding_error"]) == ("keyword_only", "llm_unavailable")
    assert found["hits"][0]["heading"] == "Disk full > Free space"
    assert found["hits"][0]["semantic_rank"] is None

    failed = await admin.post(
        "/runbooks", json={"team": "payments", "title": "New", "body": "## Step\nDo it."}
    )
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "llm_unavailable"


async def test_runbooks_off_is_a_clear_503(app: FastAPI) -> None:
    off = create_app(app.state.settings.model_copy(update={"embedding_model": None}))
    async with started(off):
        admin = await signed_in(off, "team:payments:admin")
        response = await admin.post(
            "/runbooks", json={"team": "payments", "title": "x", "body": DISK}
        )
        await admin.aclose()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "runbooks_off"


async def test_the_assistant_gets_the_askers_runbook_sections_and_no_one_elses(
    sign_in_as: SignIn, mock_llm: MockLLM
) -> None:
    admin = await sign_in_as("team:payments:admin", "team:platform:admin")
    await save(admin, "platform", "Platform disk full")
    asker = await sign_in_as("team:payments:viewer")

    meta = events_of((await asker.post("/chat/stream", json={"message": "disk full?"})).text)[0]
    assert (meta[1]["runbooks_in_context"], meta[1]["retrieval"]) == (0, "hybrid")

    await save(admin, "payments", "Payments disk full")
    body = (await asker.post("/chat/stream", json={"message": "disk full?"})).text
    events = events_of(body)
    assert events[0][1]["runbooks_in_context"] == 3
    done = events[-1][1]
    assert done["citations"] == []  # the mock cites nothing
    assert done["invalid_citations"] == []
