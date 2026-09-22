"""Application factory. Everything a request needs (engine, Redis client,
limiter, settings) is created in the lifespan and hung on app.state, so
tests build an app with their own settings and nothing is created at
import time."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.config import Settings, get_settings
from app.db import create_engine, create_sessionmaker
from app.errors import install_error_handlers
from app.llm import OpenAICompatibleClient
from app.middleware import RequestContextMiddleware
from app.ratelimit import RateLimiter
from app.routes import alerts, chat, health


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = create_engine(settings)
        app.state.sessionmaker = create_sessionmaker(app.state.engine)
        app.state.redis = Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_timeout=settings.ratelimit_timeout_s,
            socket_connect_timeout=settings.ratelimit_timeout_s,
            # Bounded: when Redis is slow, requests fail open instead of
            # opening connections without limit.
            max_connections=64,
        )
        app.state.limiter = RateLimiter(app.state.redis, timeout_s=settings.ratelimit_timeout_s)
        # One client per process: it holds the HTTP connection pool to the
        # provider, so connections (and TLS handshakes) are reused.
        app.state.llm = OpenAICompatibleClient(settings)
        try:
            yield
        finally:
            await app.state.llm.aclose()
            await app.state.redis.aclose()
            await app.state.engine.dispose()

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
    app.include_router(alerts.router)
    app.include_router(chat.router)
    return app
