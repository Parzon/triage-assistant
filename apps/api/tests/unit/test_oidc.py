"""app/oidc.py against a fake identity provider (httpx2.MockTransport) with
real RSA keys: every check an ID token must pass, failed one at a time."""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.config import Settings
from app.oidc import (
    InvalidToken,
    OIDCClient,
    OIDCError,
    ProviderUnavailable,
    pkce_challenge,
)

ISSUER = "https://login.example.com/realms/triage"
CLIENT_ID = "triage-web"
NONCE = "n-0S6_WzA2Mj"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql://a:b@db/x",
        "redis_url": "redis://r:6379/0",
        "llm_base_url": "http://llm/v1",
        "llm_api_key": "k",
        "llm_model": "m",
        "public_url": "https://triage.example.com",
        "oidc_issuer": ISSUER,
        "oidc_client_id": CLIENT_ID,
        "oidc_client_secret": "s3cret+/%",
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


def jwk(key: rsa.RSAPrivateKey, kid: str, **extra: str) -> dict[str, Any]:
    return {**json.loads(RSAAlgorithm.to_jwk(key.public_key())), "kid": kid, **extra}


class FakeProvider:
    """Discovery, keys and token endpoint; counts requests per path."""

    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/auth",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/certs",
            "end_session_endpoint": f"{ISSUER}/logout",
        }
        # Keycloak publishes an encryption key next to the signing key.
        encryption = {"kty": "RSA", "use": "enc", "kid": "enc-1", "alg": "RSA-OAEP", "n": "x"}
        self.keys = [jwk(KEY, "sig-1"), {**encryption, "e": "AQAB"}]
        self.token_response: tuple[int, dict[str, Any]] = (200, {})
        self.hits: dict[str, int] = {}
        self.last_token_request: httpx2.Request | None = None

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        self.hits[path] = self.hits.get(path, 0) + 1
        if path == "openid-configuration":
            return httpx2.Response(200, json=self.metadata)
        if path == "certs":
            return httpx2.Response(200, json={"keys": self.keys})
        if path == "token":
            self.last_token_request = request
            status, body = self.token_response
            return httpx2.Response(status, json=body)
        return httpx2.Response(404)


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def client(provider: FakeProvider) -> OIDCClient:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(provider.handler))
    return OIDCClient(settings(), http=http)


def token(
    key: rsa.RSAPrivateKey = KEY,
    kid: str = "sig-1",
    algorithm: str = "RS256",
    **claims: Any,
) -> str:
    now = int(time.time())
    body = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "user-123",
        "iat": now,
        "exp": now + 300,
        "nonce": NONCE,
        "email": "alice@example.com",
        "name": "Alice",
        "groups": ["team:payments:responder", "org:admin", 7],
        **claims,
    }
    body = {k: v for k, v in body.items() if v is not None}
    return jwt.encode(body, key, algorithm=algorithm, headers={"kid": kid})


async def test_a_valid_id_token_gives_the_identity(client: OIDCClient) -> None:
    identity = await client.verify(token(), nonce=NONCE)
    assert identity.subject == "user-123"
    assert identity.email == "alice@example.com"
    assert identity.groups == ("team:payments:responder", "org:admin")  # non-strings dropped


BAD_TOKENS: dict[str, Callable[[], str]] = {
    "another issuer": lambda: token(iss="https://evil.example"),
    "another audience": lambda: token(aud="someone-else"),
    "expired": lambda: token(exp=int(time.time()) - 3600),
    "no expiry": lambda: token(exp=None),
    "another nonce (replayed token)": lambda: token(nonce="other"),
    "no nonce": lambda: token(nonce=None),
    "signed by another key": lambda: token(key=OTHER_KEY),
    "issued to another client (azp)": lambda: token(azp="someone-else"),
    "several audiences, no azp": lambda: token(aud=[CLIENT_ID, "other"]),
    "unknown key id": lambda: token(kid="nope"),
    "not a JWT": lambda: "not.a.jwt",
}


@pytest.mark.parametrize("make", BAD_TOKENS.values(), ids=BAD_TOKENS.keys())
async def test_bad_tokens_are_refused(client: OIDCClient, make: Callable[[], str]) -> None:
    with pytest.raises(InvalidToken):
        await client.verify(make(), nonce=NONCE)


async def test_unsigned_and_hmac_tokens_are_refused(client: OIDCClient) -> None:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "x", "iat": now, "exp": now + 60}
    claims["nonce"] = NONCE
    unsigned = jwt.encode(claims, key=None, algorithm="none", headers={"kid": "sig-1"})
    # HS256 with the client secret: legal in OIDC, refused here - anyone who
    # holds the secret could then mint identities. (With the provider's
    # public key as the HMAC secret, it is the classic algorithm-confusion
    # attack; the algorithm allow-list stops both before any key is used.)
    secret = "a-client-secret-long-enough-for-hs256!"
    hmac_signed = jwt.encode(claims, secret, algorithm="HS256", headers={"kid": "sig-1"})
    for forged in (unsigned, hmac_signed):
        with pytest.raises(InvalidToken, match="algorithm"):
            await client.verify(forged, nonce=NONCE)


async def test_rotated_keys_are_fetched_once_not_per_token(
    client: OIDCClient, provider: FakeProvider
) -> None:
    await client.verify(token(), nonce=NONCE)
    provider.keys.append(jwk(OTHER_KEY, "sig-2"))  # the provider rotates
    # A new key id is looked up immediately only if the last fetch is older
    # than JWKS_MIN_REFRESH_S; pretend it is.
    client._keys_at -= 3600
    await client.verify(token(key=OTHER_KEY, kid="sig-2"), nonce=NONCE)
    for _ in range(5):  # made-up key ids cannot make us hammer the provider
        with pytest.raises(InvalidToken):
            await client.verify(token(kid="made-up"), nonce=NONCE)
    assert provider.hits["certs"] == 2


async def test_metadata_naming_another_issuer_is_not_trusted(
    client: OIDCClient, provider: FakeProvider
) -> None:
    provider.metadata["issuer"] = "https://evil.example"
    with pytest.raises(ProviderUnavailable, match="misconfigured"):
        await client.authorization_url(state="s", nonce="n", code_challenge="c")


async def test_authorization_url_asks_for_a_code_with_pkce(client: OIDCClient) -> None:
    url = await client.authorization_url(state="st", nonce="no", code_challenge="ch")
    assert url.startswith(f"{ISSUER}/auth?")
    for part in (
        "response_type=code",
        f"client_id={CLIENT_ID}",
        "redirect_uri=https%3A%2F%2Ftriage.example.com%2Fapi%2Fauth%2Fcallback",
        "scope=openid+profile+email",
        "state=st",
        "nonce=no",
        "code_challenge=ch",
        "code_challenge_method=S256",
    ):
        assert part in url


async def test_code_exchange_authenticates_with_an_encoded_secret(
    client: OIDCClient, provider: FakeProvider
) -> None:
    provider.token_response = (200, {"id_token": "the-id-token", "token_type": "Bearer"})
    assert await client.exchange_code("the-code", code_verifier="v" * 43) == "the-id-token"
    request = provider.last_token_request
    assert request is not None
    # RFC 6749 2.3.1: "s3cret+/%" is form-encoded before base64.
    assert request.headers["authorization"] == "Basic dHJpYWdlLXdlYjpzM2NyZXQlMkIlMkYlMjU="
    form = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
    assert form["grant_type"] == "authorization_code"
    assert form["code_verifier"] == "v" * 43


@pytest.mark.parametrize(
    ("status", "error"), [(400, OIDCError), (401, OIDCError), (503, ProviderUnavailable)]
)
async def test_code_exchange_failures(
    client: OIDCClient, provider: FakeProvider, status: int, error: type[Exception]
) -> None:
    provider.token_response = (status, {"error": "invalid_grant"})
    with pytest.raises(error):
        await client.exchange_code("used-code", code_verifier="v" * 43)


async def test_an_unreachable_provider_is_unavailable_not_a_crash() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    client = OIDCClient(settings(), http=httpx2.AsyncClient(transport=httpx2.MockTransport(refuse)))
    with pytest.raises(ProviderUnavailable):
        await client.authorization_url(state="s", nonce="n", code_challenge="c")
    assert await client.end_session_url(id_token="t") is None  # sign-out still works locally


async def test_sign_out_url_carries_the_id_token_hint(client: OIDCClient) -> None:
    url = await client.end_session_url(id_token="the-id-token")
    assert url is not None
    assert url.startswith(f"{ISSUER}/logout?")
    assert "id_token_hint=the-id-token" in url
    assert "post_logout_redirect_uri=https%3A%2F%2Ftriage.example.com%2F" in url


async def test_check_reports_the_provider_and_refreshes_the_cache(
    client: OIDCClient, provider: FakeProvider
) -> None:
    assert client.reachable is None  # not checked yet
    assert await client.check() is True
    assert await client.check() is True
    assert provider.hits["openid-configuration"] == 2  # each check fetches afresh
    provider.metadata["issuer"] = "https://evil.example"  # misconfigured = not usable
    assert await client.check() is False
    assert client.reachable is False


async def test_the_watcher_exports_the_verdict(client: OIDCClient, provider: FakeProvider) -> None:
    from prometheus_client import REGISTRY

    from app.oidc import watch_identity_provider

    watcher = asyncio.create_task(watch_identity_provider(client, interval_s=0.01))
    await asyncio.sleep(0.05)
    assert REGISTRY.get_sample_value("identity_provider_up") == 1
    provider.metadata["issuer"] = "https://evil.example"
    await asyncio.sleep(0.05)
    assert REGISTRY.get_sample_value("identity_provider_up") == 0
    watcher.cancel()


def test_pkce_challenge_matches_rfc_7636_appendix_b() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
