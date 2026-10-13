"""Fixtures for tests that need a real PostgreSQL.

Never SQLite. Everything these tests exist to verify — partial unique indexes,
CHECK constraints, partitioning, triggers, grants, ON CONFLICT behaviour — is
PostgreSQL-specific, and SQLite would pass tests that PostgreSQL fails
(docs/TESTING.md §1).

Set ``TEST_DATABASE_URL`` to run these. Unset, they skip with a reason rather
than silently reporting success. Set but unreachable, they fail loudly: "the
database was configured and did not work" is a result, not a reason to skip.

The variable is deliberately **not** ``DCP_``-prefixed. That prefix is reserved
for application settings, and an unrecognised ``DCP_*`` variable is a startup
error by design (app/core/config.py), so a test-harness variable wearing it
would force the guard to be loosened — which is the opposite of what is wanted.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text

from app.health.checks import ALEMBIC_INI

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark everything under tests/integration/ as `integration`.

    A module-level ``pytestmark`` in a conftest does not reach sibling modules,
    so without this hook ``-m integration`` silently selects nothing — which
    looks like a passing run.
    """
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(pytest.mark.integration)


def _alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    # -x url= is honoured by alembic/env.py, so settings are not consulted.
    config.cmd_opts = None  # type: ignore[assignment]
    config.attributes["configure_logger"] = False
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(autouse=True)
def _point_settings_at_the_test_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Override the root conftest's placeholder DSN.

    The root fixture injects an unreachable placeholder so unit tests can never
    accidentally touch a database. Integration tests need the opposite, and
    anything reading settings (``get_sync_engine``, the readiness checks) must
    see the real URL.
    """
    if not TEST_DATABASE_URL:
        return
    monkeypatch.setenv("DCP_DATABASE_URL", TEST_DATABASE_URL)
    from app.core.config import get_settings
    from app.db.session import get_engine, get_session_factory, get_sync_engine

    for cached in (get_settings, get_engine, get_session_factory, get_sync_engine):
        cached.cache_clear()


@pytest.fixture(scope="session")
def database_url() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL is not set; integration tests need a real "
            "PostgreSQL (see docs/TESTING.md §16)"
        )
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    # connect_timeout is not optional here. Without it a sync psycopg connect
    # sits in selectors.select() with no deadline, so a DSN whose host resolves
    # IPv6-first against a loopback-IPv4-only published port hangs the whole
    # suite instead of failing. That is exactly what happened before this was
    # added, and it is why TEST_DATABASE_URL should use 127.0.0.1 rather than
    # localhost.
    created = create_engine(database_url, future=True, connect_args={"connect_timeout": 10})
    # Fail loudly if a configured database is unreachable.
    with created.connect() as connection:
        connection.execute(text("SELECT 1"))
    yield created
    created.dispose()


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    return _alembic_config(database_url)


@pytest.fixture(scope="session")
def migrated(engine: Engine, alembic_config: Config) -> Iterator[Engine]:
    """A schema at head, built by running the real migrations.

    Starts from an empty database every session, so the tests verify the
    migrations rather than whatever state a previous run left behind.
    """
    _drop_everything(engine)
    command.upgrade(alembic_config, "head")
    yield engine
    _drop_everything(engine)


def _drop_everything(engine: Engine) -> None:
    """Reset to an empty public schema.

    DROP SCHEMA CASCADE also removes the audit immutability triggers, which
    would otherwise block dropping their own table.
    """
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture
def connection(migrated: Engine) -> Iterator[object]:
    """A connection in a transaction that is always rolled back.

    Keeps tests independent without re-running migrations per test.
    """
    with migrated.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()
