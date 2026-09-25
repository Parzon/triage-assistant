"""Runtime configuration, read from environment variables only.

Validated once at startup: a missing or malformed variable stops the
process with a readable error instead of failing on the first request.
Adding a setting means touching four places: this class, .env.example,
the compose file that passes it, and the env-var table in the handbook.

Production is safe by default (ADR-0023). With APP_ENV=prod, the
defaults that suit a laptop change (the API docs off, a tenth of traces
kept, the chat's rate limit failing closed), and the process refuses to
start with a setting that is wrong for real users: production_problems()
below. A deployment that means one of them - the production-shaped stack
on a laptop or in CI, with localhost and the mock - waives it by name.
"""

from functools import lru_cache
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_core import PydanticUseDefault
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url

Sampler = Literal[
    "always_on",
    "always_off",
    "traceidratio",
    "parentbased_always_on",
    "parentbased_always_off",
    "parentbased_traceidratio",
]


def _in_production(data: dict[str, Any]) -> bool:
    return data.get("app_env") == "prod"


def _outside_production(data: dict[str, Any]) -> bool:
    return not _in_production(data)


def _default_sampler(data: dict[str, Any]) -> Sampler:
    return "parentbased_traceidratio" if _in_production(data) else "parentbased_always_on"


def _default_sampler_arg(data: dict[str, Any]) -> float:
    return 0.1 if _in_production(data) else 1.0


class Settings(BaseSettings):
    # hide_input_in_errors: a validation error would otherwise print every
    # value it was given - the database URL's password and the API keys
    # included - into the log of a process that refuses to start.
    model_config = SettingsConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    # First: the defaults of the settings below depend on it.
    app_env: Literal["dev", "test", "prod"] = "dev"
    # The release this process runs: compose passes IMAGE_TAG. Reported on
    # traces (service.version) and in app_info.
    app_version: str = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Traces (app/tracing.py, ADR-0018). Off unless an OTLP endpoint is
    # set; the SDK also reads the other standard OTEL_* variables itself.
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "triage-assistant-api"
    # How long one export may take, retries included. It also bounds a
    # worker's shutdown when the trace backend is down (measured: the
    # exporter's default, 10 s, delayed every stop by 10 s). Our own name,
    # in seconds: the spec's OTEL_EXPORTER_OTLP_TIMEOUT is milliseconds, and
    # the Python exporter reads it as seconds.
    trace_export_timeout_s: float = Field(2.0, gt=0)
    # Which traces are kept (the standard OTEL_TRACES_SAMPLER names). Unset:
    # every one in dev and test; a tenth in production, where each kept
    # trace is storage few will read. With the parentbased_ samplers, a
    # caller's decision (an incoming traceparent) wins.
    otel_traces_sampler: Sampler = Field(default_factory=_default_sampler)
    otel_traces_sampler_arg: float = Field(default_factory=_default_sampler_arg, ge=0, le=1)
    # Questions, prompts, answers and runbook headings on spans, redacted.
    # Development only: a trace store is readable by everyone who debugs,
    # across every team. Production refuses it.
    trace_content: bool = False
    # Path prefix the reverse proxy strips (nginx and the Vite dev proxy
    # both serve the api under /api). Used for generated URLs: /docs,
    # the OpenAPI schema, redirects.
    root_path: str = ""
    # The OpenAPI UI (/api/docs) and schema. Unset: on in dev and test, off
    # in production, which need not advertise every route.
    docs_enabled: bool = Field(default_factory=_outside_production)

    # A plain postgresql:// URL (what psql, RDS and secret stores hand out);
    # the async driver is chosen in async_database_url.
    database_url: SecretStr
    # Per worker. Size the pool for peak concurrency, not the average, and
    # avoid relying on overflow: SQLAlchemy closes overflow connections when
    # they are returned, so irregular arrivals churn them - every new
    # connection pays a SCRAM login (~12ms) and setup queries, requests slow
    # down, concurrency stays high, and the churn sustains itself. Measured:
    # 5+5 gave 196 new connections and ~90ms requests in 20s of bursty load;
    # PgBouncer makes a larger app pool cheap for Postgres.
    db_pool_size: int = Field(20, ge=1)
    db_max_overflow: int = Field(0, ge=0)
    db_pool_timeout_s: float = Field(5.0, gt=0)
    db_connect_timeout_s: float = Field(5.0, gt=0)

    redis_url: SecretStr
    # Budget for one rate-limit round trip; past it the limiter fails open.
    # It is wall-clock time, so it also counts time the reply spends waiting
    # for a busy event loop: at 50ms the load test saw the limiter fail open
    # thousands of times at only ~46% CPU per worker.
    ratelimit_timeout_s: float = Field(0.2, gt=0)
    # Redis connections per worker: roughly the requests one worker holds at
    # once. Too few and requests fail open with MaxConnectionsError.
    redis_max_connections: int = Field(256, ge=1)
    ratelimit_window_s: int = Field(60, ge=1)
    alerts_rate_limit: int = Field(60, ge=1)
    chat_rate_limit: int = Field(10, ge=1)
    # When the limiter's store cannot be reached. Unset: the chat's requests
    # pass in dev and test, like every other route's (ADR-0004); in
    # production they are refused (503), because an unlimited chat is an
    # unlimited model bill (ADR-0023).
    chat_rate_limit_fail_closed: bool = Field(default_factory=_in_production)
    # Runbook writes and searches, each: a write embeds every section, a
    # search the question - model calls, so a cost.
    runbooks_rate_limit: int = Field(60, ge=1)

    # --- LLM: any OpenAI-compatible endpoint (OpenAI, Azure OpenAI, LiteLLM,
    # vLLM, Ollama, tools/mock-llm). The SDK's own defaults are a 600s read
    # timeout and 2 retries - never rely on them for an interactive request.
    llm_base_url: str
    llm_api_key: SecretStr
    llm_model: str
    llm_connect_timeout_s: float = Field(5.0, gt=0)
    # Longest silence tolerated between two streamed chunks (time to first
    # token included). Not the total: that is llm_stream_timeout_s.
    llm_read_timeout_s: float = Field(60.0, gt=0)
    llm_stream_timeout_s: float = Field(120.0, gt=0)
    # Retries happen before the first token only; each one is added latency
    # the user watches, so keep it low for chat.
    llm_max_retries: int = Field(1, ge=0)
    # For reasoning models (OpenAI's o-series and gpt-5, gpt-oss) the limit
    # includes their hidden thinking: measured with gpt-oss:20b at the default
    # effort, 3 of 5 answers spent all 800 tokens thinking and said nothing.
    llm_max_output_tokens: int = Field(800, ge=1)
    # Reasoning models only ("low" | "medium" | "high"): how much they think
    # before answering. Unset = not sent - other models reject the parameter.
    # gpt-oss:20b at "low": 65-308 tokens instead of 460-800.
    llm_reasoning_effort: Literal["low", "medium", "high"] | None = None
    chat_context_alerts: int = Field(20, ge=0)
    # How the assistant answers (ADR-0020). "pipeline": the service reads the
    # asker's newest alerts and the runbook sections matching the question,
    # then calls the model once. "agent": the model gets read-only tools and
    # decides what to read (app/agent.py): more model calls, more tokens.
    chat_mode: Literal["pipeline", "agent"] = "pipeline"
    # Agent mode: rounds of tool calls before the model must answer.
    agent_max_steps: int = Field(3, ge=1, le=10)

    # --- Runbook search (RFC-0001, ADR-0017): embeddings from the same
    # OpenAI-compatible endpoint as the model. Unset = runbooks are off: the
    # /runbooks routes answer 503 and the assistant sees alerts only.
    embedding_model: str | None = None
    # Sent as `dimensions` when set: asks a model with another native size
    # for the database's (models.EMBEDDING_DIM), where the model supports it.
    embedding_dimensions: int | None = Field(None, ge=1)
    # Task prefixes some models are trained with, e.g. nomic-embed-text:
    # "search_query: " and "search_document: ". Without them, recall drops.
    embedding_query_prefix: str = ""
    embedding_document_prefix: str = ""
    # A question's embedding is on the chat's critical path: past this,
    # retrieval falls back to keyword search rather than delay the answer.
    embedding_timeout_s: float = Field(5.0, gt=0)
    # Runbook sections put in the prompt; 0 = none (alerts only).
    rag_context_chunks: int = Field(4, ge=0, le=20)
    # SSE comment sent when nothing else has been for this long, so proxies
    # and load balancers (idle timeouts of ~60s) keep the stream open while
    # the model is still thinking.
    sse_heartbeat_s: float = Field(15.0, gt=0)

    # Shared secret Alertmanager sends as a bearer token. Unset = the
    # webhook endpoint does not exist (404).
    alertmanager_webhook_token: SecretStr | None = None

    # --- Sign-in: OpenID Connect against the organisation's identity
    # provider (Keycloak locally; Entra ID, Okta, Google... in production).
    # ADR-0013 and the security chapter of the handbook.
    #
    # The app's address as the browser sees it, no path: the OIDC redirect
    # URI (<public_url>/api/auth/callback), the page after sign-out and the
    # origin every state-changing request must come from are derived from it.
    public_url: str
    # Must equal the `iss` claim of the provider's ID tokens exactly - trailing
    # slash included - and the `issuer` in its discovery document.
    oidc_issuer: str = Field(min_length=1)
    # Where the api reads the provider's metadata. Empty: <issuer>/.well-known/
    # openid-configuration, right for any real provider. Set only when the
    # issuer URL is not reachable from the api's own network - the bundled
    # Keycloak, which the browser reaches through the edge and the api directly.
    oidc_discovery_url: str | None = None
    oidc_client_id: str = Field(min_length=1)
    oidc_client_secret: SecretStr = Field(min_length=1)
    oidc_scopes: str = "openid profile email"
    # The claim listing the user's groups (Keycloak, Okta) or app roles
    # (Entra ID: "roles"). Values "team:<slug>:<role>" and "org:admin" grant
    # access; everything else in it is ignored.
    oidc_groups_claim: str = "groups"
    oidc_timeout_s: float = Field(5.0, gt=0)
    # A session ends at whichever comes first: this long after sign-in, or
    # this long without a request. Role changes in the identity provider
    # apply at the next sign-in, so the lifetime bounds how long a removed
    # member keeps access - revoke sessions for immediate effect (handbook).
    session_max_age_s: int = Field(12 * 3600, ge=60)
    session_idle_timeout_s: int = Field(2 * 3600, ge=60)
    # False only for plain-HTTP development (http://localhost:5173): browsers
    # drop Secure cookies, and the __Host- prefix requires them, on http://.
    session_cookie_secure: bool = True
    # Per client IP per window: sign-in redirects and callbacks.
    auth_rate_limit: int = Field(30, ge=1)

    # --- Personal data (app/privacy.py, docs/privacy.md). What the retention
    # job deletes (`make retention`, scheduled on a host): users who have not
    # signed in for this many days (they come back at their next sign-in,
    # from the identity provider), and alerts older than this. Empty: keep
    # for ever. Your retention policy decides; these are only defaults.
    user_retention_days: int | None = Field(365, ge=1)
    alert_retention_days: int | None = Field(365, ge=1)

    # Production checks this deployment skips on purpose, by name, comma-
    # separated (PRODUCTION_CHECKS). The production-shaped stack on a laptop
    # or in CI runs with localhost, the mock model and the demo users, and
    # waives those three (.env.example). Each waiver is logged at startup.
    prod_checks_waived: Annotated[frozenset[str], NoDecode] = frozenset()

    @field_validator("user_retention_days", "alert_retention_days", mode="before")
    @classmethod
    def _empty_retention_means_keep(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("otel_traces_sampler", "otel_traces_sampler_arg", mode="before")
    @classmethod
    def _empty_sampler_means_default(cls, value: object) -> object:
        if value == "":
            raise PydanticUseDefault()
        return value

    @field_validator("prod_checks_waived", mode="before")
    @classmethod
    def _comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(name.strip() for name in value.split(",") if name.strip())
        return value

    @field_validator("prod_checks_waived")
    @classmethod
    def _known_checks(cls, value: frozenset[str]) -> frozenset[str]:
        # A misspelled waiver would waive nothing, silently.
        if unknown := value - set(PRODUCTION_CHECKS):
            raise ValueError(
                f"PROD_CHECKS_WAIVED names no check: {', '.join(sorted(unknown))} "
                f"(the checks: {', '.join(PRODUCTION_CHECKS)})"
            )
        return value

    @model_validator(mode="after")
    def _safe_for_production(self) -> "Settings":
        if self.app_env != "prod":
            return self
        problems = {
            name: problem
            for name, problem in production_problems(self).items()
            if name not in self.prod_checks_waived
        }
        if problems:
            raise ValueError(
                "APP_ENV=prod refuses to start with these settings (ADR-0023). Fix each, "
                "or waive it by name in PROD_CHECKS_WAIVED if this deployment means it:\n"
                + "\n".join(f"  {name}: {problem}" for name, problem in problems.items())
            )
        return self

    @field_validator("public_url")
    @classmethod
    def _origin_only(cls, value: str) -> str:
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.netloc or url.path not in ("", "/"):
            raise ValueError("PUBLIC_URL must be scheme://host[:port] with no path")
        return f"{url.scheme}://{url.netloc}"

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def _empty_effort_means_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("oidc_discovery_url", mode="before")
    @classmethod
    def _empty_means_default(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def oidc_metadata_url(self) -> str:
        return self.oidc_discovery_url or (
            self.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
        )

    @property
    def oidc_redirect_uri(self) -> str:
        return f"{self.public_url}/api/auth/callback"

    @field_validator("embedding_model", "embedding_dimensions", mode="before")
    @classmethod
    def _empty_embedding_means_off(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("alertmanager_webhook_token", mode="before")
    @classmethod
    def _empty_token_means_disabled(cls, value: object) -> object:
        # Compose passes ${VAR:-} as "", not "unset": an empty secret must
        # disable the webhook, never match an empty Authorization header.
        return None if value == "" else value

    @field_validator("database_url")
    @classmethod
    def _postgres_url(cls, value: SecretStr) -> SecretStr:
        if make_url(value.get_secret_value()).get_backend_name() != "postgresql":
            raise ValueError("DATABASE_URL must be a postgresql:// URL")
        return value

    @property
    def async_database_url(self) -> str:
        url = make_url(self.database_url.get_secret_value())
        return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


# What production refuses, by the name PROD_CHECKS_WAIVED uses. Each is a
# mistake that has shipped somewhere before; each is checked by code, so
# the pre-launch checklist keeps only what code cannot check.
PRODUCTION_CHECKS = (
    "localhost_url",
    "insecure_cookies",
    "mock_model",
    "demo_identity_provider",
    "example_secret",
    "trace_content",
)
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _secrets(settings: Settings) -> list[str]:
    values = [
        settings.oidc_client_secret.get_secret_value(),
        settings.llm_api_key.get_secret_value(),
    ]
    if settings.alertmanager_webhook_token is not None:
        values.append(settings.alertmanager_webhook_token.get_secret_value())
    for url in (settings.database_url, settings.redis_url):
        if password := urlsplit(url.get_secret_value()).password:
            values.append(password)
    return values


def production_problems(settings: Settings) -> dict[str, str]:
    """The production checks these settings fail: name -> what is wrong."""
    problems: dict[str, str] = {}
    public = _host(settings.public_url)
    if public in _LOCAL_HOSTS or public.endswith(".localhost"):
        problems["localhost_url"] = (
            f"PUBLIC_URL is {settings.public_url}: no other machine reaches it, and "
            "sign-in redirects there. Set the address users open."
        )
    if not settings.session_cookie_secure or settings.public_url.startswith("http://"):
        problems["insecure_cookies"] = (
            "session cookies would travel over plain HTTP: PUBLIC_URL must be https:// "
            "and SESSION_COOKIE_SECURE true."
        )
    if (
        _host(settings.llm_base_url) == "mock-llm"
        or settings.llm_model.startswith("mock")
        or (settings.embedding_model or "").startswith("mock")
    ):
        problems["mock_model"] = (
            "LLM_* or EMBEDDING_MODEL name the mock: set the provider's, and drop "
            '"mock" from COMPOSE_PROFILES.'
        )
    if _host(settings.oidc_metadata_url) == "keycloak":
        problems["demo_identity_provider"] = (
            "sign-in goes to the bundled Keycloak and its demo users: set OIDC_* to the "
            'organisation\'s provider, and drop "idp" from COMPOSE_PROFILES.'
        )
    if any(value.startswith("change-me") for value in _secrets(settings)):
        problems["example_secret"] = (
            # A message about secrets, not one: hence the S105 exemption.
            "a secret still has .env.example's value (change-me...): generate each, "  # noqa: S105
            "e.g. openssl rand -hex 24."
        )
    if settings.trace_content:
        problems["trace_content"] = (
            "TRACE_CONTENT=true puts questions and answers on spans, which everyone who "
            "reads traces can read, across teams."
        )
    return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
