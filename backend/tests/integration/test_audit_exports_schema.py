"""The ``audit_exports`` table: what the schema enforces whatever the application does (M4.6).

Each test asserts that **the database** refuses the thing, not that the application remembers to
(docs/DATABASE.md §8.1, ``OPEN-112``). The table holds a file somebody asked for; the guarantees
that matter are that the bytes exist exactly while the export is ``READY``, that a requester has
one export in flight, and that the 31-day / 100 000-row / 64 MiB budgets hold for any writer.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DataError

from tests.integration.test_alert_schema import a_user
from tests.integration.test_operation_schema import refused

EXPORT_SQL = text(
    """
    INSERT INTO audit_exports (id, requested_by, idempotency_key, format, from_at, to_at, status,
                               failure_code, row_count, size_bytes, sha256, content, completed_at,
                               expires_at, retrieved_at)
    VALUES (:id, :requester, :key, :format, now() - :span * interval '1 day', now(), :status,
            :failure_code, :row_count, :size_bytes, :sha256, :content,
            CASE WHEN :completed THEN now() END, now() + interval '24 hours',
            CASE WHEN :retrieved THEN now() END)
    """
)

#: What each status needs to be a legitimate row; the tests then break one thing at a time.
COMPLETE: dict[str, dict[str, Any]] = {
    "QUEUED": {},
    "RUNNING": {},
    "READY": {
        "row_count": 3,
        "size_bytes": 12,
        "sha256": "0" * 64,
        "content": b"{}\n{}\n{}\n",
        "completed": True,
    },
    "FAILED": {"failure_code": "export_too_large", "completed": True},
    "RETRIEVED": {
        "row_count": 3,
        "size_bytes": 12,
        "sha256": "0" * 64,
        "completed": True,
        "retrieved": True,
    },
    "EXPIRED": {"row_count": 3, "size_bytes": 12, "sha256": "0" * 64, "completed": True},
}


def export(
    connection: Connection, requester: uuid.UUID, status: str = "QUEUED", **overrides: Any
) -> uuid.UUID:
    """Insert one row, complete for its status unless a test breaks a field on purpose."""
    export_id = uuid.uuid4()
    connection.execute(
        EXPORT_SQL,
        {
            "id": export_id,
            "requester": requester,
            "key": str(uuid.uuid4()),
            "format": "jsonl",
            "span": 7,
            "status": status,
            "failure_code": None,
            "row_count": None,
            "size_bytes": None,
            "sha256": None,
            "content": None,
            "completed": False,
            "retrieved": False,
            **COMPLETE.get(status, {}),
            **overrides,
        },
    )
    return export_id


@pytest.fixture
def requester(connection: Connection) -> tuple[Connection, uuid.UUID]:
    return connection, a_user(connection)


Requester = tuple[Connection, uuid.UUID]


@pytest.mark.parametrize("status", list(COMPLETE))
def test_every_status_has_a_legitimate_shape(requester: Requester, status: str) -> None:
    connection, user = requester
    export(connection, user, status)


# --- closed sets ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "DONE"},
        {"status": "ready"},
        {"status": "VERIFYING"},
        {"format": "csv"},
        {"format": "JSONL"},
    ],
)
def test_a_value_outside_a_closed_set_is_refused(
    requester: Requester, changes: dict[str, Any]
) -> None:
    connection, user = requester
    with refused(connection):
        export(connection, user, changes.pop("status", "QUEUED"), **changes)


def test_a_failure_code_outside_the_closed_set_is_refused(requester: Requester) -> None:
    connection, user = requester
    with refused(connection):
        export(connection, user, "FAILED", failure_code="SELECT * FROM users")


def test_a_failed_export_must_say_why_and_only_a_failed_one_may(requester: Requester) -> None:
    connection, user = requester
    with refused(connection):
        export(connection, user, "FAILED", failure_code=None)
    with refused(connection):
        export(connection, user, "QUEUED", failure_code="internal_error")


# --- the budgets, whoever writes the row ------------------------------------------------------


def test_a_window_of_thirty_one_days_is_accepted_and_thirty_two_is_refused(
    requester: Requester,
) -> None:
    connection, user = requester
    export(connection, user, span=31)
    with refused(connection):
        export(connection, user, span=32)


def test_an_empty_or_reversed_window_is_refused(requester: Requester) -> None:
    connection, user = requester
    with refused(connection):
        export(connection, user, span=0)  # from == to
    with refused(connection):
        export(connection, user, span=-1)  # from after to


@pytest.mark.parametrize("key", ["", "short", "x" * 7])
def test_an_idempotency_key_outside_eight_to_sixty_four_characters_is_refused(
    requester: Requester, key: str
) -> None:
    connection, user = requester
    with refused(connection):
        export(connection, user, key=key)


def test_a_key_longer_than_sixty_four_characters_does_not_fit(requester: Requester) -> None:
    connection, user = requester
    savepoint = connection.begin_nested()
    with pytest.raises(DataError):
        export(connection, user, key="k" * 65)
    savepoint.rollback()


def test_the_row_and_byte_budgets_hold(requester: Requester) -> None:
    connection, user = requester
    export(connection, user, "EXPIRED", row_count=100_000, size_bytes=64 * 1024 * 1024)
    with refused(connection):
        export(connection, user, "EXPIRED", row_count=100_001)
    with refused(connection):
        export(connection, user, "EXPIRED", size_bytes=64 * 1024 * 1024 + 1)
    with refused(connection):
        export(connection, user, "EXPIRED", row_count=-1)


# --- the bytes exist exactly while the export is READY ----------------------------------------


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "FAILED", "RETRIEVED", "EXPIRED"])
def test_a_file_cannot_sit_in_an_export_that_is_not_ready(
    requester: Requester, status: str
) -> None:
    connection, user = requester
    with refused(connection):
        export(connection, user, status, content=b"leaked")


def test_a_ready_export_without_its_file_is_refused(requester: Requester) -> None:
    connection, user = requester
    with refused(connection):
        export(connection, user, "READY", content=None)


@pytest.mark.parametrize("missing", ["row_count", "size_bytes", "sha256"])
def test_a_ready_export_must_describe_its_file(requester: Requester, missing: str) -> None:
    connection, user = requester
    with refused(connection):
        export(connection, user, "READY", **{missing: None})


def test_spending_an_export_must_delete_the_file_in_the_same_statement(
    requester: Requester,
) -> None:
    connection, user = requester
    export_id = export(connection, user, "READY")
    with refused(connection):
        connection.execute(
            text(
                "UPDATE audit_exports SET status = 'RETRIEVED', retrieved_at = now() WHERE id = :id"
            ),
            {"id": export_id},
        )
    connection.execute(
        text(
            "UPDATE audit_exports SET status = 'RETRIEVED', retrieved_at = now(), content = NULL"
            " WHERE id = :id"
        ),
        {"id": export_id},
    )


def test_expiring_an_export_must_delete_the_file_too(requester: Requester) -> None:
    connection, user = requester
    export_id = export(connection, user, "READY")
    with refused(connection):
        connection.execute(
            text("UPDATE audit_exports SET status = 'EXPIRED' WHERE id = :id"), {"id": export_id}
        )


@pytest.mark.parametrize("status", ["READY", "FAILED", "RETRIEVED"])
def test_a_finished_export_has_a_completion_time(requester: Requester, status: str) -> None:
    connection, user = requester
    with refused(connection):
        export(connection, user, status, completed=False)


def test_retrieved_at_is_set_exactly_when_the_export_is_retrieved(requester: Requester) -> None:
    connection, user = requester
    with refused(connection):
        export(connection, user, "RETRIEVED", retrieved=False)
    with refused(connection):
        export(connection, user, "EXPIRED", retrieved=True)


# --- who may have what in flight --------------------------------------------------------------


@pytest.mark.parametrize("first", ["QUEUED", "RUNNING"])
@pytest.mark.parametrize("second", ["QUEUED", "RUNNING"])
def test_a_requester_has_one_export_in_flight(
    requester: Requester, first: str, second: str
) -> None:
    connection, user = requester
    export(connection, user, first)
    with refused(connection):
        export(connection, user, second)


def test_a_finished_export_frees_the_requester_to_ask_again(requester: Requester) -> None:
    connection, user = requester
    for done in ("READY", "FAILED", "RETRIEVED", "EXPIRED"):
        export(connection, user, done)
    export(connection, user, "QUEUED")


def test_two_requesters_each_have_their_own_slot(requester: Requester) -> None:
    connection, user = requester
    other = a_user(connection)
    export(connection, user, "QUEUED")
    export(connection, other, "QUEUED")


def test_one_key_is_one_export_per_requester_and_not_across_requesters(
    requester: Requester,
) -> None:
    connection, user = requester
    other = a_user(connection)
    export(connection, user, "EXPIRED", key="shared-key-1")
    export(connection, other, "EXPIRED", key="shared-key-1")
    with refused(connection):
        export(connection, user, "EXPIRED", key="shared-key-1")


def test_an_export_belongs_to_a_real_user(requester: Requester) -> None:
    connection, _ = requester
    with refused(connection):
        export(connection, uuid.uuid4())


# --- the indexes the queue and the sweep rely on ---------------------------------------------


def test_the_expiry_sweep_has_a_partial_index_over_ready_exports(requester: Requester) -> None:
    connection, _ = requester
    definition = connection.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_audit_exports_expiry'")
    ).scalar_one()
    assert "expires_at" in definition and "READY" in definition
