import pytest
from pydantic import ValidationError

from app.config import Settings


def make(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql://app:s3cret@pgbouncer:5432/triage",
        "redis_url": "redis://redis:6379/0",
        "llm_base_url": "http://mock-llm:8020/v1",
        "llm_api_key": "k",
        "llm_model": "m",
        "public_url": "https://triage.example.com",
        "oidc_issuer": "https://login.example.com/realms/x",
        "oidc_client_id": "triage-web",
        "oidc_client_secret": "oidc-s3cret",
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


def test_async_url_uses_asyncpg_and_keeps_credentials() -> None:
    url = make().async_database_url
    assert url == "postgresql+asyncpg://app:s3cret@pgbouncer:5432/triage"


def test_rejects_non_postgres_urls() -> None:
    with pytest.raises(ValidationError, match="postgresql"):
        make(database_url="mysql://app:pw@db/triage")


def test_secrets_never_appear_in_repr() -> None:
    settings = make()
    assert "s3cret" not in repr(settings)
    assert "s3cret" not in str(settings.model_dump())


def test_rejects_nonsense_limits() -> None:
    with pytest.raises(ValidationError):
        make(alerts_rate_limit=0)


def test_secrets_never_appear_in_repr_including_the_client_secret() -> None:
    assert "oidc-s3cret" not in repr(make())


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("https://triage.example.com", "https://triage.example.com"),
        ("https://triage.example.com/", "https://triage.example.com"),
        ("http://localhost:5173", "http://localhost:5173"),
    ],
)
def test_public_url_is_an_origin(given: str, stored: str) -> None:
    settings = make(public_url=given)
    assert settings.public_url == stored
    assert settings.oidc_redirect_uri == f"{stored}/api/auth/callback"


@pytest.mark.parametrize("bad", ["triage.example.com", "https://x.example/app", "ftp://x.example"])
def test_public_url_with_a_path_or_no_scheme_is_refused(bad: str) -> None:
    with pytest.raises(ValidationError, match="PUBLIC_URL"):
        make(public_url=bad)


def test_discovery_url_defaults_to_the_issuers() -> None:
    assert make(oidc_discovery_url="").oidc_metadata_url == (
        "https://login.example.com/realms/x/.well-known/openid-configuration"
    )
    assert make(oidc_discovery_url="http://kc:8080/d").oidc_metadata_url == "http://kc:8080/d"


@pytest.mark.parametrize("field", ["oidc_client_secret", "oidc_issuer", "oidc_client_id"])
def test_empty_sign_in_settings_stop_the_process(field: str) -> None:
    # Compose turns a variable missing from .env into "": that must fail at
    # startup, not at the first sign-in.
    with pytest.raises(ValidationError, match=field):
        make(**{field: ""})
