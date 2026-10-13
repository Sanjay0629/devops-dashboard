"""Health and readiness endpoints (docs/API_CONTRACTS.md §11)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_healthz_is_up_without_touching_dependencies(client: TestClient) -> None:
    """Liveness must not depend on PostgreSQL or Redis being reachable."""
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_ok_when_required_checks_pass(client: TestClient, all_checks_pass: None) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": True, "broker": True, "migrations": True}


@pytest.mark.parametrize("failing", ["database", "broker", "migrations"])
def test_readyz_503_when_a_required_check_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    import app.health.checks as checks

    async def ok() -> bool:
        return True

    async def fail() -> bool:
        return False

    for name in ("database", "broker", "migrations"):
        monkeypatch.setattr(checks, f"check_{name}", fail if name == failing else ok)

    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["checks"][failing] is False


def test_migrations_now_gate_readiness(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """M1.2 promoted `migrations` into REQUIRED_CHECKS.

    An instance whose schema is behind the code it runs must not serve traffic:
    every query it makes is against the wrong shape. This replaces M1.1's
    assertion that the check was reported but not required.
    """
    import app.health.checks as checks

    assert "migrations" in checks.REQUIRED_CHECKS

    async def ok() -> bool:
        return True

    async def fail() -> bool:
        return False

    monkeypatch.setattr(checks, "check_database", ok)
    monkeypatch.setattr(checks, "check_broker", ok)
    monkeypatch.setattr(checks, "check_migrations", fail)

    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["migrations"] is False


def test_expected_head_is_readable_from_the_migration_scripts() -> None:
    """The readiness check needs the Alembic scripts present in the image."""
    from app.health.checks import expected_head

    expected_head.cache_clear()
    head = expected_head()
    assert head is not None, "alembic head unreadable; is alembic/ in the image?"
    assert head.isalnum()


@pytest.mark.security
def test_readyz_leaks_no_connection_detail(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Booleans only: no hostnames, versions, drivers or error text."""
    import app.health.checks as checks

    async def boom() -> bool:
        raise RuntimeError("connection to host db.internal:5432 failed: bad password")

    monkeypatch.setattr(checks, "check_database", boom)

    response = client.get("/readyz")
    body = response.text
    for forbidden in ("db.internal", "5432", "password", "psycopg", "Traceback"):
        assert forbidden not in body
    assert set(response.json()["checks"].values()) <= {True, False}


def test_readyz_does_not_raise_when_a_check_explodes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe returns a verdict; it never 500s."""
    import app.health.checks as checks

    async def boom() -> bool:
        raise RuntimeError("placeholder failure")

    monkeypatch.setattr(checks, "check_broker", boom)
    assert client.get("/readyz").status_code == 503
