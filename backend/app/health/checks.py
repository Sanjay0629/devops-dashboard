"""Dependency checks behind ``/readyz``.

Each check returns a plain ``bool`` and never raises: a readiness probe that
500s tells an orchestrator nothing useful. Failures are logged (redacted) and
reported as ``False``.

``REQUIRED_CHECKS`` is what gates readiness. ``migrations`` is reported but not
yet required, because Alembic arrives in M1.2 — until then no schema exists and
demanding one would keep the stack permanently unready. M1.2 adds "migrations"
to this tuple and upgrades the check from "schema present" to "schema at head".
"""

from __future__ import annotations

import asyncio
from typing import Final

import redis.asyncio as aioredis
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_engine

logger = get_logger(__name__)

#: Checks that must pass for the service to report ready. See module docstring.
REQUIRED_CHECKS: Final[tuple[str, ...]] = ("database", "broker")


async def check_database() -> bool:
    try:
        engine = get_engine()
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception as error:
        logger.warning("readiness_check_failed", check="database", error=str(error))
        return False


async def check_broker() -> bool:
    settings = get_settings()
    client: aioredis.Redis | None = None
    try:
        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url.get_secret_value(),
            socket_connect_timeout=settings.readiness_timeout_seconds,
            socket_timeout=settings.readiness_timeout_seconds,
        )
        return bool(await client.ping())
    except Exception as error:
        logger.warning("readiness_check_failed", check="broker", error=str(error))
        return False
    finally:
        if client is not None:
            await client.aclose()


async def check_migrations() -> bool:
    """M1.1: has the schema been migrated at all?

    Returns ``True`` once Alembic's bookkeeping table exists. This is
    deliberately weaker than "at head" and is not claimed to be more — M1.2
    replaces the body with a real revision comparison.
    """
    try:
        engine = get_engine()
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            )
            return bool(result.scalar())
    except Exception as error:
        logger.warning("readiness_check_failed", check="migrations", error=str(error))
        return False


async def run_checks() -> dict[str, bool]:
    """Run every check concurrently under a single wall-clock bound.

    ``return_exceptions=True`` is load-bearing: the individual checks already
    swallow their own failures, but the aggregator must not depend on that. A
    readiness probe reports a verdict or nothing — it never propagates.
    """
    settings = get_settings()
    names = ("database", "broker", "migrations")
    runners = (check_database(), check_broker(), check_migrations())
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*runners, return_exceptions=True),
            timeout=settings.readiness_timeout_seconds * 2,
        )
    except TimeoutError:
        logger.warning("readiness_checks_timed_out")
        return dict.fromkeys(names, False)

    verdicts: dict[str, bool] = {}
    for name, result in zip(names, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("readiness_check_raised", check=name, error=str(result))
            verdicts[name] = False
        else:
            verdicts[name] = bool(result)
    return verdicts


def is_ready(checks: dict[str, bool]) -> bool:
    return all(checks.get(name, False) for name in REQUIRED_CHECKS)
