"""Signing in through a real identity provider: the Keycloak of the test
stack (compose.test.yaml), with the realm the other environments use.

The test plays the browser. The app is in-process at https://test; the
browser half of the flow (Keycloak's login page) goes over the network to
http://keycloak:8080, where the api's own back-channel calls go too.
"""

import asyncio
import os
import re
from collections.abc import AsyncIterator
from html import unescape
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.sessions import session_cookie
from tests.integration.conftest import BASE_URL, started


@pytest.fixture(scope="module")
def keycloak_url() -> str:
    if "KEYCLOAK_URL" not in os.environ:
        pytest.skip("needs the compose test stack's Keycloak: run `make test`")
    return os.environ["KEYCLOAK_URL"]


@pytest.fixture
async def idp_ready(keycloak_url: str) -> None:
    """Keycloak takes ~10-30s to start; `make test-api` starts it first."""
    url = f"{keycloak_url}/realms/triage/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=2) as http:
        for _ in range(120):
            try:
                if (await http.get(url)).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    pytest.fail(f"Keycloak not ready at {url} after 120s")


@pytest.fixture
async def browser(app: FastAPI, idp_ready: None) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=BASE_URL, headers={"Origin": BASE_URL}
    ) as client:
        yield client


async def idp_submit(authorize_url: str, username: str, password: str) -> httpx.Response:
    """Keycloak's side of the browser: its login page, and the form on it
    submitted."""
    async with httpx.AsyncClient(follow_redirects=False, timeout=10) as idp:
        page = await idp.get(authorize_url)
        assert page.status_code == 200, page.text[:300]
        action = re.search(r'id="kc-form-login"[^>]*action="([^"]+)"', page.text)
        assert action, "no login form in Keycloak's page"
        return await idp.post(
            unescape(action.group(1)), data={"username": username, "password": password}
        )


async def idp_login(authorize_url: str, username: str) -> str:
    """A successful sign-in at the provider: where it sends the browser next
    (our callback, with code and state)."""
    submitted = await idp_submit(authorize_url, username, os.environ["DEMO_USER_PASSWORD"])
    assert submitted.status_code == 302, submitted.text[:300]
    return submitted.headers["location"]


async def start(browser: AsyncClient, next_path: str = "/") -> str:
    response = await browser.get("/auth/login", params={"next": next_path})
    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    return response.headers["location"]


async def finish(browser: AsyncClient, callback_url: str) -> httpx.Response:
    url = urlsplit(callback_url)
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{BASE_URL}/api/auth/callback"
    return await browser.get("/auth/callback", params=parse_qs(url.query))


async def test_sign_in_end_to_end(app: FastAPI, browser: AsyncClient) -> None:
    authorize = await start(browser, "/after")
    params = parse_qs(urlsplit(authorize).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == [f"{BASE_URL}/api/auth/callback"]

    landed = await finish(browser, await idp_login(authorize, "alice"))
    assert landed.status_code == 302
    assert landed.headers["location"] == "/after"
    name = session_cookie(app.state.settings)
    assert name.startswith("__Host-")  # Secure, Path=/, no Domain: no subdomain can set it
    session = next(c for c in landed.headers.get_list("set-cookie") if c.startswith(f"{name}="))
    for flag in ("HttpOnly", "Secure", "Path=/", "SameSite=lax"):
        assert flag.lower() in session.lower(), flag

    me = (await browser.get("/me")).json()
    assert me["email"] == "alice@example.com"
    assert me["name"] == "Alice Payments"
    assert {(t["slug"], t["role"]) for t in me["teams"]} == {
        ("payments", "responder"),
        ("platform", "viewer"),
    }


async def test_org_admin_from_the_groups_claim(browser: AsyncClient) -> None:
    await finish(browser, await idp_login(await start(browser), "carol"))
    assert (await browser.get("/me")).json()["org_admin"] is True


async def test_a_code_works_once(browser: AsyncClient) -> None:
    callback = await idp_login(await start(browser), "alice")
    assert (await finish(browser, callback)).headers["location"] == "/"
    replay = await finish(browser, callback)  # the same code and state again
    assert replay.headers["location"] == "/?auth_error=invalid_state"


async def test_callback_from_another_browser_is_refused(app: FastAPI, idp_ready: None) -> None:
    """Login CSRF: an attacker's own code, delivered to a victim's browser,
    must not sign the victim in as the attacker."""
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as attacker,
        AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as victim,
    ):
        callback = await idp_login(await start(attacker), "bob")
        response = await finish(victim, callback)  # victim never started a sign-in
        assert response.headers["location"] == "/?auth_error=invalid_state"
        assert (await victim.get("/me")).status_code == 401


async def test_wrong_password_never_reaches_the_app(browser: AsyncClient) -> None:
    refused = await idp_submit(await start(browser), "alice", "wrong-password")
    assert refused.status_code == 200  # the provider shows its form again
    assert "Invalid username or password" in refused.text
    assert (await browser.get("/me")).status_code == 401


async def test_provider_errors_and_mix_ups_land_on_the_sign_in_page(browser: AsyncClient) -> None:
    await start(browser)
    cancelled = await browser.get("/auth/callback", params={"error": "access_denied"})
    assert cancelled.headers["location"] == "/?auth_error=access_denied"
    mixed_up = await browser.get(
        "/auth/callback", params={"code": "x", "state": "y", "iss": "https://evil.example"}
    )
    assert mixed_up.headers["location"] == "/?auth_error=login_failed"


@pytest.mark.parametrize("target", ["//evil.example/x", "/\\evil.example", "https://evil.example"])
async def test_next_is_never_another_site(browser: AsyncClient, target: str) -> None:
    landed = await finish(browser, await idp_login(await start(browser, target), "alice"))
    assert landed.headers["location"] == "/"


async def test_sign_out_also_ends_the_providers_session(browser: AsyncClient) -> None:
    await finish(browser, await idp_login(await start(browser), "alice"))
    logout_url = (await browser.post("/auth/logout")).json()["logout_url"]
    params = parse_qs(urlsplit(logout_url).query)
    assert urlsplit(logout_url).path.endswith("/protocol/openid-connect/logout")
    assert params["post_logout_redirect_uri"] == [f"{BASE_URL}/"]
    assert params["id_token_hint"]  # no "are you sure?" page at the provider
    assert (await browser.get("/me")).status_code == 401
    async with httpx.AsyncClient(follow_redirects=False) as idp:
        ended = await idp.get(logout_url)
    assert ended.status_code == 302
    assert ended.headers["location"] == f"{BASE_URL}/"


async def test_provider_down_blocks_new_sign_ins_only(
    browser: AsyncClient, settings: Settings, blackhole_port: int
) -> None:
    """Sessions are this service's own: an identity provider outage stops
    new sign-ins, never the people already signed in."""
    await finish(browser, await idp_login(await start(browser), "alice"))
    down = create_app(
        settings.model_copy(
            update={
                "oidc_discovery_url": f"http://127.0.0.1:{blackhole_port}/.well-known/x",
                "oidc_timeout_s": 0.5,
            }
        )
    )
    async with (
        started(down),
        AsyncClient(transport=ASGITransport(app=down), base_url=BASE_URL) as client,
    ):
        client.cookies = browser.cookies
        assert (await client.get("/me")).status_code == 200
        response = await client.get("/auth/login")
    assert response.headers["location"] == "/?auth_error=idp_unavailable"
