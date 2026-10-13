"""Database engine and session factory.

M1.1 needs only connectivity for the readiness probe. Models, the declarative
base and the per-request session dependency arrive in M1.2 together with the
Alembic migrations (docs/IMPLEMENTATION_PLAN.md M1.2).

``statement_timeout`` is set per connection here rather than per query, so a
future query cannot forget it (docs/ARCHITECTURE.md §10.1).
"""

from __future__ import annotations

import functools

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@functools.lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"options": f"-c statement_timeout={settings.database_statement_timeout_ms}"},
        # Never echo SQL: statements can carry parameter values.
        echo=False,
    )


@functools.lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False)


async def dispose_engine() -> None:
    """Close pooled connections on shutdown."""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
