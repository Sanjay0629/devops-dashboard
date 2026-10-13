"""Database engines and the per-request session.

Two engines, deliberately:

* an **async** engine for request handling, and
* a **sync** engine for the maintenance tasks and Alembic, which have no reason
  to be concurrent.

Both use the same ``postgresql+psycopg://`` URL, because psycopg3 serves sync
and async behind one dialect — one driver, one dependency, no second code path.

``statement_timeout`` is set per connection rather than per query, so a future
query cannot forget it (docs/ARCHITECTURE.md §10.1).
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def _connect_args() -> dict[str, str | int]:
    """Per-connection options.

    ``connect_timeout`` matters as much as ``statement_timeout``: without it a
    TCP connect has no deadline, so a host that silently drops packets (a
    loopback port published on IPv4 only, reached via an IPv6-first
    ``localhost``) blocks for the OS default of ~20s. A readiness probe that
    inherits that unbounded wait reports every dependency as down
    (docs/ARCHITECTURE.md §10.1: an operation without a deadline is a bug).
    """
    settings = get_settings()
    return {
        "options": f"-c statement_timeout={settings.database_statement_timeout_ms}",
        "connect_timeout": max(2, int(settings.readiness_timeout_seconds)),
    }


@functools.lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args=_connect_args(),
        # Never echo SQL: statements can carry parameter values.
        echo=False,
    )


@functools.lru_cache(maxsize=1)
def get_sync_engine() -> Engine:
    """Synchronous engine for scheduled maintenance work.

    A deliberately small pool: this path serves scheduled jobs, not request
    traffic, and must not be able to starve the async pool.
    """
    settings = get_settings()
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
        connect_args=_connect_args(),
        echo=False,
    )


@functools.lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request.

    Commits on success, rolls back on any exception. Putting the boundary here
    rather than in each handler is what makes "the audit row and the state
    change share one transaction" structural, instead of a convention every new
    endpoint has to remember (docs/DATABASE.md §12).

    ``autoflush=False`` is deliberate: an implicit flush mid-handler can emit a
    partial write whose constraint violation then surfaces from a confusing
    place. Flushes are explicit.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Close pooled connections on shutdown."""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
    if get_sync_engine.cache_info().currsize:
        get_sync_engine().dispose()
