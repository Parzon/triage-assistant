"""Runtime configuration, read from environment variables only.

Validated once at startup: a missing or malformed variable stops the
process with a readable error instead of failing on the first request.
Adding a setting means touching four places: this class, .env.example,
the compose file that passes it, and the env-var table in the handbook.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Path prefix the reverse proxy strips (nginx and the Vite dev proxy
    # both serve the api under /api). Used for generated URLs: /docs,
    # the OpenAPI schema, redirects.
    root_path: str = ""
    docs_enabled: bool = True

    # A plain postgresql:// URL (what psql, RDS and secret stores hand out);
    # the async driver is chosen in async_database_url.
    database_url: SecretStr
    db_pool_size: int = Field(5, ge=1)
    db_max_overflow: int = Field(5, ge=0)
    db_pool_timeout_s: float = Field(5.0, gt=0)
    db_connect_timeout_s: float = Field(5.0, gt=0)
    # Client-side cap per query. The server-side cap is statement_timeout on
    # the app's database role (transaction pooling ignores session SETs).
    db_command_timeout_s: float = Field(10.0, gt=0)

    redis_url: SecretStr
    # Budget for one rate-limit round trip. Past it the limiter fails open.
    ratelimit_timeout_s: float = Field(0.05, gt=0)
    ratelimit_window_s: int = Field(60, ge=1)
    alerts_rate_limit: int = Field(60, ge=1)
    chat_rate_limit: int = Field(10, ge=1)

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
    llm_max_output_tokens: int = Field(800, ge=1)
    chat_context_alerts: int = Field(20, ge=0)
    # SSE comment sent when nothing else has been for this long, so proxies
    # and load balancers (idle timeouts of ~60s) keep the stream open while
    # the model is still thinking.
    sse_heartbeat_s: float = Field(15.0, gt=0)

    # Shared secret Alertmanager sends as a bearer token. Unset = the
    # webhook endpoint does not exist (404).
    alertmanager_webhook_token: SecretStr | None = None

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


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
