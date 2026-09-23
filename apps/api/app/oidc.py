"""OpenID Connect: signing users in with the organisation's identity provider.

The authorization code flow with PKCE, as a confidential client (ADR-0013):
the browser only ever carries a one-time code; this server exchanges it,
with its client secret, for an ID token it verifies itself, then issues its
own session. No token ever reaches the browser, so none can be stolen from
it. The only module that knows the protocol: routes/auth.py calls it,
nothing else does.

What an ID token must pass before anyone is signed in (OIDC Core 3.1.3.7):
a signature by one of the provider's published keys, with an asymmetric
algorithm from ALGORITHMS (never "none", never HMAC); `iss` equal to the
configured issuer; `aud` containing our client id (and `azp` equal to it
when present); `exp`/`iat` within the clock-skew leeway; `nonce` equal to
the one this sign-in sent.

The provider is an external dependency, like the model: the api starts
without it, and only new sign-ins fail while it is down - sessions already
issued are this service's own and keep working.
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import httpx2
import jwt

from app.config import Settings
from app.metrics import identity_provider_up

log = logging.getLogger(__name__)

ALGORITHMS = ("RS256", "PS256", "ES256")
# Provider and verifier clocks differ; tokens are checked with this slack.
LEEWAY_S = 60
METADATA_TTL_S = 3600
# A token signed with an unknown key id triggers one refetch of the key set
# (the provider rotated its keys) - at most this often, so tokens with made-up
# key ids cannot make us hammer the provider.
JWKS_MIN_REFRESH_S = 60


class OIDCError(Exception):
    """A sign-in that cannot complete. `code` is safe to show and to count."""

    code = "login_failed"


class ProviderUnavailable(OIDCError):
    code = "idp_unavailable"


class InvalidToken(OIDCError):
    code = "invalid_token"


@dataclass(frozen=True)
class ProviderMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    # Absent for providers without RP-initiated logout (Google): signing out
    # then ends only this service's session.
    end_session_endpoint: str | None


@dataclass(frozen=True)
class Identity:
    """The verified claims this service uses, from one ID token."""

    subject: str
    email: str | None
    name: str | None
    groups: tuple[str, ...]
    # Kept for the provider's sign-out; None for sessions minted by app.cli.
    id_token: str | None


def pkce_challenge(verifier: str) -> str:
    """S256 code challenge (RFC 7636): the provider only hands the tokens to
    whoever can show the verifier behind it - a stolen code alone is useless."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OIDCClient:
    def __init__(self, settings: Settings, http: httpx2.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = http or httpx2.AsyncClient(
            timeout=httpx2.Timeout(settings.oidc_timeout_s), follow_redirects=False
        )
        self._metadata: ProviderMetadata | None = None
        self._metadata_at = 0.0
        self._keys: dict[str, jwt.PyJWK] = {}
        self._keys_at = 0.0
        # The last check's verdict (watch_identity_provider); None = not yet.
        self.reachable: bool | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def check(self) -> bool:
        """Does the provider answer, with metadata we can trust? Fetches it
        afresh, which also keeps the cached copy current."""
        try:
            await self.metadata(refresh=True)
        except ProviderUnavailable:
            self.reachable = False
        else:
            self.reachable = True
        return self.reachable

    async def metadata(self, *, refresh: bool = False) -> ProviderMetadata:
        if (
            refresh
            or self._metadata is None
            or time.monotonic() - self._metadata_at > METADATA_TTL_S
        ):
            doc = await self._get_json(self._settings.oidc_metadata_url)
            # A provider that names another issuer is misconfigured or not
            # the provider we meant: never trust its endpoints (OIDC
            # Discovery 4.3 - this is what stops IdP mix-up attacks).
            if doc.get("issuer") != self._settings.oidc_issuer:
                log.error(
                    "identity provider metadata names another issuer",
                    extra={"expected": self._settings.oidc_issuer, "got": doc.get("issuer")},
                )
                raise ProviderUnavailable("the identity provider is misconfigured")
            try:
                self._metadata = ProviderMetadata(
                    issuer=doc["issuer"],
                    authorization_endpoint=doc["authorization_endpoint"],
                    token_endpoint=doc["token_endpoint"],
                    jwks_uri=doc["jwks_uri"],
                    end_session_endpoint=doc.get("end_session_endpoint"),
                )
            except KeyError as exc:
                raise ProviderUnavailable(f"provider metadata lacks {exc}") from exc
            self._metadata_at = time.monotonic()
        return self._metadata

    async def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        meta = await self.metadata()
        params = {
            "response_type": "code",
            "client_id": self._settings.oidc_client_id,
            "redirect_uri": self._settings.oidc_redirect_uri,
            "scope": self._settings.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{meta.authorization_endpoint}?{urlencode(params)}"

    async def exchange_code(self, code: str, *, code_verifier: str) -> str:
        """The code for tokens, over the back channel; returns the raw ID token."""
        meta = await self.metadata()
        # client_secret_basic. RFC 6749 2.3.1: id and secret are form-encoded
        # BEFORE base64 - a secret with "+" or "%" fails against strict
        # providers if this step is skipped (most libraries skip it).
        client = quote(self._settings.oidc_client_id, safe="")
        secret = quote(self._settings.oidc_client_secret.get_secret_value(), safe="")
        basic = base64.b64encode(f"{client}:{secret}".encode()).decode()
        try:
            response = await self._http.post(
                meta.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._settings.oidc_redirect_uri,
                    "code_verifier": code_verifier,
                },
                headers={"Authorization": f"Basic {basic}", "Accept": "application/json"},
            )
        except httpx2.HTTPError as exc:
            raise ProviderUnavailable("the identity provider did not answer") from exc
        if response.status_code >= 500:
            raise ProviderUnavailable(f"token endpoint answered {response.status_code}")
        body = _json(response)
        if response.status_code != 200:
            # invalid_grant: the code expired or was already used (a reload of
            # the callback page, or a replayed code). Logged, never shown raw.
            log.warning("code exchange refused", extra={"error": body.get("error")})
            raise OIDCError("the sign-in could not be completed")
        id_token = body.get("id_token")
        if not isinstance(id_token, str):
            raise InvalidToken("the token response has no ID token")
        return id_token

    async def verify(self, id_token: str, *, nonce: str) -> Identity:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise InvalidToken("malformed ID token") from exc
        algorithm = header.get("alg")
        if algorithm not in ALGORITHMS:
            raise InvalidToken(f"ID token algorithm {algorithm!r} is not accepted")
        key = await self._signing_key(header.get("kid"))
        client_id = self._settings.oidc_client_id
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key=key.key,
                algorithms=[algorithm],
                audience=client_id,
                issuer=self._settings.oidc_issuer,
                leeway=LEEWAY_S,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidToken(f"ID token rejected: {exc}") from exc
        audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
        if claims.get("azp", client_id) != client_id or (
            len(audiences) > 1 and "azp" not in claims
        ):
            raise InvalidToken("ID token was issued to another client")
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise InvalidToken("ID token nonce does not match this sign-in")
        groups = claims.get(self._settings.oidc_groups_claim) or []
        return Identity(
            subject=str(claims["sub"]),
            email=_str_or_none(claims.get("email")),
            name=_str_or_none(claims.get("name") or claims.get("preferred_username")),
            groups=tuple(g for g in groups if isinstance(g, str))
            if isinstance(groups, list)
            else (),
            id_token=id_token,
        )

    async def end_session_url(self, *, id_token: str | None) -> str | None:
        """Where the browser goes to end its session at the provider too."""
        try:
            meta = await self.metadata()
        except ProviderUnavailable:
            return None  # signed out here; the provider's session lives on
        if meta.end_session_endpoint is None:
            return None
        params = {
            "client_id": self._settings.oidc_client_id,
            "post_logout_redirect_uri": f"{self._settings.public_url}/",
        }
        if id_token:
            params["id_token_hint"] = id_token
        return f"{meta.end_session_endpoint}?{urlencode(params)}"

    async def _signing_key(self, kid: object) -> jwt.PyJWK:
        if not isinstance(kid, str):
            raise InvalidToken("ID token has no key id")
        if kid not in self._keys and time.monotonic() - self._keys_at > JWKS_MIN_REFRESH_S:
            meta = await self.metadata()
            jwks = await self._get_json(meta.jwks_uri)
            self._keys = _signing_keys(jwks)
            self._keys_at = time.monotonic()
        try:
            return self._keys[kid]
        except KeyError:
            raise InvalidToken("ID token signed with an unknown key") from None

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            response = await self._http.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
        except httpx2.HTTPError as exc:
            log.warning(
                "identity provider unavailable", extra={"url": url, "error": type(exc).__name__}
            )
            raise ProviderUnavailable("the identity provider did not answer") from exc
        return _json(response)


async def watch_identity_provider(client: OIDCClient, interval_s: float = 30.0) -> None:
    """Check the provider every interval_s. Sign-in redirects are built from
    cached metadata, so while the provider is down users are sent to a
    login page that never loads - no request here fails, and signed-in users
    are unaffected. Without this gauge (and its alert) nobody would know."""
    while True:
        identity_provider_up.set(1 if await client.check() else 0)
        await asyncio.sleep(interval_s)


def _signing_keys(jwks: dict[str, Any]) -> dict[str, jwt.PyJWK]:
    """Signature keys by key id. Keycloak also publishes an encryption key
    (use "enc", alg RSA-OAEP) in the same set; PyJWK cannot load it, and it
    must never verify a signature anyway."""
    keys: dict[str, jwt.PyJWK] = {}
    for jwk in jwks.get("keys", []):
        if not isinstance(jwk, dict) or jwk.get("use", "sig") != "sig" or "kid" not in jwk:
            continue
        try:
            keys[jwk["kid"]] = jwt.PyJWK(jwk)
        except jwt.PyJWTError:
            log.warning("skipping an unusable provider key", extra={"kid": jwk.get("kid")})
    return keys


def _json(response: httpx2.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderUnavailable("the identity provider sent something other than JSON") from exc
    if not isinstance(body, dict):
        raise ProviderUnavailable("the identity provider sent unexpected JSON")
    return body


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
