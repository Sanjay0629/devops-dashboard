"""The application's Redis client.

One pooled client per process, cached. Redis carries the Celery broker, the
session store (AD-02, OPEN-04), the OIDC login state and the rate-limit
counters, so a second connection pool per feature would multiply connections
for no benefit.

Every call is given a deadline. ``app/health/checks.py`` deliberately keeps
building its own short-lived client instead of using this one: a readiness
probe must not report healthy because a pooled connection happens to be warm.
"""

from __future__ import annotations

import functools

import redis.asyncio as aioredis

from app.core.config import get_settings


@functools.lru_cache(maxsize=1)
def get_redis() -> aioredis.Redis:
    settings = get_settings()
    client: aioredis.Redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url.get_secret_value(),
        # Keys and values here are ids, counters and JSON — never binary.
        decode_responses=True,
        socket_connect_timeout=settings.readiness_timeout_seconds,
        socket_timeout=settings.readiness_timeout_seconds,
        health_check_interval=30,
    )
    return client


async def dispose_redis() -> None:
    """Close the pool on shutdown."""
    if get_redis.cache_info().currsize:
        await get_redis().aclose()
        get_redis.cache_clear()
