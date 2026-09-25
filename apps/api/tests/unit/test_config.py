import pytest
from pydantic import ValidationError

from app.config import PRODUCTION_CHECKS, Settings, production_problems


@pytest.fixture(autouse=True)
def code_defaults_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests are about the code's defaults: the test container's own
    settings (from its environment) must not stand in for them."""
    for name in (
        "DOCS_ENABLED",
        "OTEL_TRACES_SAMPLER",
        "OTEL_TRACES_SAMPLER_ARG",
        "CHAT_RATE_LIMIT_FAIL_CLOSED",
        "PROD_CHECKS_WAIVED",
        "TRACE_CONTENT",
        "EMBEDDING_MODEL",
        "SESSION_COOKIE_SECURE",
        "OIDC_DISCOVERY_URL",
        "ALERTMANAGER_WEBHOOK_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


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


# --- Production, safe by default (ADR-0023) ----------------------------------


def production(**overrides: object) -> Settings:
    """A production configuration that passes every check: each test below
    breaks one thing in it."""
    values: dict[str, object] = {
        "app_env": "prod",
        "database_url": "postgresql://app:0f3b9c2e7a51@pgbouncer:5432/triage",
        "redis_url": "redis://redis:6379/0",
        "llm_base_url": "https://api.openai.com/v1",
        "llm_api_key": "sk-proj-not-a-real-key",
        "llm_model": "gpt-5-mini",
        "embedding_model": "text-embedding-3-small",
        "public_url": "https://triage.example.com",
        "oidc_issuer": "https://login.example.com/realms/x",
        "oidc_discovery_url": "",
        "oidc_client_id": "triage-web",
        "oidc_client_secret": "oidc-s3cret",
        "alertmanager_webhook_token": "not-a-real-webhook-token",
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


def test_a_production_configuration_that_passes_every_check_starts() -> None:
    assert production_problems(production()) == {}


def test_defaults_that_differ_in_production() -> None:
    dev, prod = make(app_env="dev"), production()
    assert (dev.docs_enabled, prod.docs_enabled) == (True, False)
    assert (dev.chat_rate_limit_fail_closed, prod.chat_rate_limit_fail_closed) == (False, True)
    assert (dev.otel_traces_sampler, dev.otel_traces_sampler_arg) == ("parentbased_always_on", 1.0)
    assert (prod.otel_traces_sampler, prod.otel_traces_sampler_arg) == (
        "parentbased_traceidratio",
        0.1,
    )


def test_a_setting_that_is_set_wins_over_the_production_default() -> None:
    prod = production(
        docs_enabled=True, otel_traces_sampler="always_on", chat_rate_limit_fail_closed=False
    )
    assert (prod.docs_enabled, prod.otel_traces_sampler) == (True, "always_on")
    assert prod.chat_rate_limit_fail_closed is False


def test_an_empty_sampler_setting_means_the_default() -> None:
    # What compose passes for OTEL_TRACES_SAMPLER= in .env.
    prod = production(otel_traces_sampler="", otel_traces_sampler_arg="")
    assert (prod.otel_traces_sampler, prod.otel_traces_sampler_arg) == (
        "parentbased_traceidratio",
        0.1,
    )


@pytest.mark.parametrize(
    ("check", "unsafe"),
    [
        ("localhost_url", {"public_url": "https://localhost"}),
        ("localhost_url", {"public_url": "https://127.0.0.1:8443"}),
        ("insecure_cookies", {"session_cookie_secure": False}),
        ("insecure_cookies", {"public_url": "http://triage.example.com"}),
        ("mock_model", {"llm_base_url": "http://mock-llm:8020/v1"}),
        ("mock_model", {"embedding_model": "mock-embed"}),
        (
            "demo_identity_provider",
            {"oidc_discovery_url": "http://keycloak:8080/auth/realms/triage/.well-known/x"},
        ),
        ("example_secret", {"oidc_client_secret": "change-me-oidc-client-secret"}),
        ("example_secret", {"database_url": "postgresql://app:change-me-app@db:5432/triage"}),
        ("trace_content", {"trace_content": True}),
    ],
)
def test_production_refuses_each_unsafe_setting_unless_waived_by_name(
    check: str, unsafe: dict[str, object]
) -> None:
    with pytest.raises(ValidationError, match=f"refuses to start.*\n  {check}: ") as refused:
        production(**unsafe)
    assert [name for name in PRODUCTION_CHECKS if f"  {name}: " in str(refused.value)] == [check]
    assert production(**unsafe, prod_checks_waived=check).app_env == "prod"


def test_every_check_can_be_waived_by_its_name() -> None:
    unsafe = production(
        public_url="http://localhost",
        session_cookie_secure=False,
        llm_model="mock-1",
        oidc_discovery_url="http://keycloak:8080/d",
        oidc_client_secret="change-me-oidc-client-secret",
        trace_content=True,
        prod_checks_waived=", ".join(PRODUCTION_CHECKS),
    )
    assert set(production_problems(unsafe)) == set(PRODUCTION_CHECKS)


def test_a_refusal_never_prints_a_secret() -> None:
    # It goes to the log of a process that will not start.
    with pytest.raises(ValidationError) as refused:
        production(oidc_client_secret="change-me-and-leak")
    assert "0f3b9c2e7a51" not in str(refused.value)  # the database password
    assert "sk-proj-not-a-real-key" not in str(refused.value)
    assert "change-me-and-leak" not in str(refused.value)


def test_a_misspelled_waiver_is_refused() -> None:
    # It would otherwise waive nothing, silently.
    with pytest.raises(ValidationError, match="names no check: mock_modle"):
        production(prod_checks_waived="mock_modle")


def test_the_checks_are_for_production_only() -> None:
    dev = make(
        app_env="dev",
        public_url="http://localhost:5173",
        session_cookie_secure=False,
        oidc_client_secret="change-me-oidc-client-secret",
        trace_content=True,
    )
    assert set(production_problems(dev)) >= {"localhost_url", "insecure_cookies", "trace_content"}
