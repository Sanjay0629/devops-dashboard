"""``python -m app.inventory.seed --sample-log-host`` against a real database."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.inventory import seed
from app.inventory.models import Application, ApplicationServer, Server


@pytest.fixture
def session(migrated: Engine) -> Iterator[Session]:
    with Session(migrated, future=True) as db:
        yield db
        db.rollback()
        with db.begin():
            seed.remove(db)


def test_the_sample_host_is_created_with_the_ids_the_shipper_uses_and_is_idempotent(
    session: Session,
) -> None:
    with session.begin():
        first = seed.seed_sample_log_host(session)
    with session.begin():
        second = seed.seed_sample_log_host(session)

    assert (first, second) == (2, 0)
    server = session.get(Server, seed.SAMPLE_LOG_SERVER_ID)
    application = session.get(Application, seed.SAMPLE_LOG_APPLICATION_ID)
    assert server is not None and application is not None
    assert server.hostname == seed.SAMPLE_LOG_HOSTNAME
    assert server.provider_metadata["synthetic"] is True  # visibly synthetic, removable
    assert server.display_name.startswith("[DEMO]")
    assert session.get(ApplicationServer, (application.id, server.id)) is not None


def test_remove_deletes_exactly_what_the_flag_created(session: Session) -> None:
    with session.begin():
        seed.seed_sample_log_host(session)
    with session.begin():
        seed.remove(session)

    assert session.get(Server, seed.SAMPLE_LOG_SERVER_ID) is None
    assert session.get(Application, seed.SAMPLE_LOG_APPLICATION_ID) is None
    assert (
        session.scalar(
            select(func.count())
            .select_from(ApplicationServer)
            .where(ApplicationServer.server_id == seed.SAMPLE_LOG_SERVER_ID)
        )
        == 0
    )
