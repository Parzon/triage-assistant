import pytest
from pydantic import ValidationError

from app.config import Settings


def make(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql://app:s3cret@pgbouncer:5432/triage",
        "redis_url": "redis://redis:6379/0",
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
