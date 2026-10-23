"""``resolve_target``: the M5.1 half of "an unregistered pair gets ``422 not_registered``".

The HTTP route that calls it is §21.2 (M5.2). These rows are committed, so they are removed again:
other suites count applications.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from app.core.errors import AppError
from app.db.session import get_session_factory
from app.deployments.service import NotRegisteredError, resolve_target
from tests.integration.test_deployment_targets_schema import a_target, an_application


@pytest.fixture
def pair(migrated: Engine) -> Iterator[dict[str, uuid.UUID]]:
    ids: dict[str, uuid.UUID] = {}
    with migrated.begin() as connection:
        ids["application"] = an_application(connection)
        ids["staging"] = a_target(connection, ids["application"], environment="staging")
        ids["disabled"] = a_target(
            connection, ids["application"], environment="development", status="DISABLED"
        )
        ids["retired"] = a_target(connection, ids["application"], environment="other", retired=True)
    try:
        yield ids
    finally:
        with migrated.begin() as connection:
            connection.execute(
                text("DELETE FROM deployment_targets WHERE application_id = :a"),
                {"a": ids["application"]},
            )
            connection.execute(
                text("DELETE FROM applications WHERE id = :a"), {"a": ids["application"]}
            )


async def test_a_registered_active_target_resolves(pair: dict[str, uuid.UUID]) -> None:
    async with get_session_factory()() as db:
        target = await resolve_target(db, pair["application"], "staging")
    assert target.id == pair["staging"]


@pytest.mark.parametrize(
    "environment", ["production", "development", "other", "prod", "", "*", "staging; --"]
)
async def test_everything_else_is_not_registered(
    pair: dict[str, uuid.UUID], environment: str
) -> None:
    """Never registered, disabled, retired, unknown or malformed: all the same answer."""
    async with get_session_factory()() as db:
        with pytest.raises(NotRegisteredError) as raised:
            await resolve_target(db, pair["application"], environment)
    error: AppError = raised.value
    assert (error.status_code, error.code) == (422, "not_registered")


async def test_an_unknown_application_is_not_registered_either() -> None:
    async with get_session_factory()() as db:
        with pytest.raises(NotRegisteredError):
            await resolve_target(db, uuid.uuid4(), "staging")
