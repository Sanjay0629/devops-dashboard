"""Shared fixtures.

No test in this file touches a network, a database or a real broker. Dependency
checks are stubbed; the real ones are exercised by the integration suite that
arrives with M1.2 (docs/TESTING.md §2).
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.db.session import get_engine, get_session_factory

# Where the unit suite points PostgreSQL and Redis: **a port nothing listens on**,
# not the default ones. The unit tests never open a socket, and some of them
# (the fail-closed rate limiter, readiness) assert what happens when a backing
# service is *unreachable* — which is only true if nothing answers. `localhost`
# on 5432 and 6379 is where a CI service container, or a developer's own
# PostgreSQL and Redis, actually answer, and the suite then fails for a reason
# that has nothing to do with the code (found when CI first ran: the `backend`
# job has both as service containers). Port 1 (tcpmux) is closed on any machine
# that matters and refuses at once, so a stray connection fails fast, not slow.
UNREACHABLE = "127.0.0.1:1"

# Placeholder credentials. Nothing here reaches a real service; the password is
# literally the word "placeholder" so a secret scanner has nothing to find.
TEST_ENV = {
    "DCP_ENVIRONMENT": "local",
    "DCP_LOG_LEVEL": "INFO",
    "DCP_DEMO_MODE": "true",
    "DCP_DATABASE_URL": f"postgresql+psycopg://placeholder:placeholder@{UNREACHABLE}/placeholder",
    "DCP_REDIS_URL": f"redis://{UNREACHABLE}/0",
}


def pytest_asyncio_loop_factories() -> dict[str, object]:
    """Force a selector-based event loop on Windows.

    psycopg's async driver refuses ``ProactorEventLoop``, which is what asyncio
    picks by default on Windows — so without this, every test that touches the
    async engine fails with ``InterfaceError`` (PROGRESS.md issue 22). The
    sync-engine integration tests from M1.2 never hit it, which is why this
    only surfaced with M1.3's first async-database tests.

    This is a **test-harness** fix and does not contradict the note in issue 22
    that an asyncio *policy* fix was removed from application code: uvicorn
    selects its loop through a factory and ignores the policy, so that change
    fixed nothing while looking like it did. pytest-asyncio takes a loop
    factory, which is exactly what is needed. The application-side answer on
    Windows is still `--reload`, or Linux.
    """
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


def _clear_caches() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test a clean, fully specified environment."""
    for key in list(TEST_ENV):
        monkeypatch.delenv(key, raising=False)
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    _clear_caches()
    yield
    _clear_caches()


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    from app.main import create_app

    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def all_checks_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub every readiness check to succeed."""
    import app.health.checks as checks

    async def ok() -> bool:
        return True

    monkeypatch.setattr(checks, "check_database", ok)
    monkeypatch.setattr(checks, "check_broker", ok)
    monkeypatch.setattr(checks, "check_migrations", ok)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration-profile",
        action="store",
        default=None,
        help="name of a NON-PRODUCTION profile; required to run real_integration tests",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """A real-integration test never runs by accident (docs/TESTING.md §2.1)."""
    if config.getoption("--integration-profile"):
        return
    skip = pytest.mark.skip(
        reason="real_integration: pass --integration-profile=<non-production profile> to run"
    )
    for item in items:
        if "real_integration" in item.keywords:
            item.add_marker(skip)
