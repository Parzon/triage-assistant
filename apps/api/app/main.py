"""Application factory. Everything a request needs (engine, Redis client,
limiter, model and identity-provider clients, settings) is created in the
lifespan and hung on app.state, so tests build an app with their own
settings and nothing is created at import time."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from redis.asyncio import Redis

from app.config import Settings, get_settings
from app.db import create_engine, create_sessionmaker, watch_db_pool
from app.errors import install_error_handlers
from app.llm import OpenAICompatibleClient
from app.metrics import app_info, watch_event_loop_lag
from app.middleware import RequestContextMiddleware
from app.oidc import OIDCClient, watch_identity_provider
from app.ratelimit import RateLimiter
from app.routes import alerts, audit, auth, chat, health, runbooks
from app.tracing import configure_tracing, shutdown_tracing, tracing_on
from app.triage import PROMPT

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        if settings.app_env == "prod" and settings.prod_checks_waived:
            # Meant for a laptop or CI; on a server, someone should notice.
            log.warning(
                "production checks waived", extra={"checks": sorted(settings.prod_checks_waived)}
            )
        # Per worker, after gunicorn's fork (see configure_tracing).
        owns_tracing = configure_tracing(settings)
        app_info.labels(
            settings.app_version,
            f"{PROMPT.name}:{PROMPT.version}",
            settings.llm_model,
            settings.embedding_model or "",
        ).set(1)
        app.state.engine = create_engine(settings)
        if tracing_on():
            # A span per statement, under the request's span: the SQL text
            # with its placeholders, never the bound values. Not the SQL
            # commenter (enable_commenter): it writes the trace id into each
            # statement's text, so no two statements match and the prepared-
            # statement caches of asyncpg and PgBouncer churn.
            SQLAlchemyInstrumentor().instrument(
                engine=app.state.engine.sync_engine, enable_commenter=False
            )
        app.state.sessionmaker = create_sessionmaker(app.state.engine)
        app.state.redis = Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_timeout=settings.ratelimit_timeout_s,
            socket_connect_timeout=settings.ratelimit_timeout_s,
            # Bounded: when Redis is slow, requests fail open instead of
            # opening connections without limit.
            max_connections=settings.redis_max_connections,
        )
        app.state.limiter = RateLimiter(app.state.redis, timeout_s=settings.ratelimit_timeout_s)
        # One client per process: it holds the HTTP connection pool to the
        # provider, so connections (and TLS handshakes) are reused.
        app.state.llm = OpenAICompatibleClient(settings)
        # The identity provider is read lazily, at the first sign-in: the api
        # starts (and serves existing sessions) while it is down.
        app.state.oidc = OIDCClient(settings)
        watchers = [
            asyncio.create_task(watch_event_loop_lag()),
            asyncio.create_task(
                watch_db_pool(app.state.engine, settings.db_pool_size + settings.db_max_overflow)
            ),
            asyncio.create_task(watch_identity_provider(app.state.oidc)),
        ]
        try:
            yield
        finally:
            for watcher in watchers:
                watcher.cancel()
            await asyncio.gather(*watchers, return_exceptions=True)
            await app.state.llm.aclose()
            await app.state.oidc.aclose()
            await app.state.redis.aclose()
            await app.state.engine.dispose()
            if tracing_on():
                SQLAlchemyInstrumentor().uninstrument()
            if owns_tracing:
                shutdown_tracing()

    app = FastAPI(
        title="triage-assistant",
        lifespan=lifespan,
        root_path=settings.root_path,
        # "/alerts/" is a 404, not a 307 to "/alerts": behind a proxy that
        # strips /api, Starlette builds that redirect without the prefix
        # (Location: http://host/alerts - wrong path, and wrong port too,
        # since nginx's $host drops it). APIs should not redirect anyway.
        redirect_slashes=False,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(alerts.router)
    app.include_router(chat.router)
    app.include_router(runbooks.router)
    app.include_router(audit.router)
    return app
