"""Dependency checks behind ``/readyz``.

Each check returns a plain ``bool`` and never raises: a readiness probe that
500s tells an orchestrator nothing useful. Failures are logged (redacted) and
reported as ``False``.

``REQUIRED_CHECKS`` is what gates readiness. As of M1.2 it includes
``migrations``: an instance whose schema is behind the code it is running must
not serve traffic, because every query it makes is against the wrong shape.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable
from pathlib import Path
from typing import Final

import redis.asyncio as aioredis
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_engine

#: backend/alembic.ini, resolved relative to this file so it works from any cwd.
ALEMBIC_INI: Final[Path] = Path(__file__).resolve().parents[2] / "alembic.ini"

logger = get_logger(__name__)

#: Checks that must pass for the service to report ready. See module docstring.
REQUIRED_CHECKS: Final[tuple[str, ...]] = ("database", "broker", "migrations")


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


@functools.lru_cache(maxsize=1)
def expected_head() -> str | None:
    """The head revision this build expects, read from the migration scripts.

    Cached: the answer is fixed for the lifetime of the process, and reading the
    script directory on every probe would be pointless I/O.
    """
    try:
        script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
        heads = script.get_heads()
    except Exception as error:
        logger.warning("alembic_head_unreadable", error=str(error))
        return None
    if len(heads) != 1:
        # Multiple heads means an unmerged branch; readiness should not paper
        # over that, so report it rather than picking one.
        logger.warning("alembic_multiple_heads", heads=sorted(heads))
        return None
    return heads[0]


async def check_migrations() -> bool:
    """Is the database schema at the revision this build expects?

    An instance running ahead of its schema will fail every query it makes, so
    this gates readiness (M1.2). "Behind" and "ahead" are both failures: the
    former means migrations have not run, the latter means a rollback left the
    code older than the database.
    """
    head = expected_head()
    if head is None:
        return False
    try:
        engine = get_engine()
        async with engine.connect() as connection:
            present = await connection.execute(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            )
            if not present.scalar():
                logger.warning("migrations_not_applied", expected_head=head)
                return False
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            applied = [row[0] for row in result.fetchall()]
    except Exception as error:
        logger.warning("readiness_check_failed", check="migrations", error=str(error))
        return False

    if applied != [head]:
        logger.warning("migration_revision_mismatch", expected_head=head, applied=applied)
        return False
    return True


async def _bounded(name: str, check: Awaitable[bool], timeout: float) -> bool:
    """Run one check under its own deadline, converting anything else to False.

    Each check gets its *own* timeout rather than sharing one wall clock for the
    whole probe. With a shared budget, a single slow dependency consumed it and
    every check reported False — including ones that were healthy — which turns
    an accurate per-dependency answer into a misleading one.
    """
    try:
        return bool(await asyncio.wait_for(check, timeout=timeout))
    except TimeoutError:
        logger.warning("readiness_check_timed_out", check=name, timeout_seconds=timeout)
        return False
    except Exception as error:
        # The checks swallow their own failures, but the aggregator must not
        # depend on that: a probe reports a verdict or nothing.
        logger.warning("readiness_check_raised", check=name, error=str(error))
        return False


async def run_checks() -> dict[str, bool]:
    """Run every check concurrently, each under its own deadline."""
    timeout = get_settings().readiness_timeout_seconds
    checks: dict[str, Awaitable[bool]] = {
        "database": check_database(),
        "broker": check_broker(),
        "migrations": check_migrations(),
    }
    results = await asyncio.gather(
        *(_bounded(name, check, timeout) for name, check in checks.items())
    )
    return dict(zip(checks, results, strict=True))


def is_ready(checks: dict[str, bool]) -> bool:
    return all(checks.get(name, False) for name in REQUIRED_CHECKS)
