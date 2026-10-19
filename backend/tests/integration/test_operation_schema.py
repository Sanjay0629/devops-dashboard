"""The ``operations`` table: what the schema enforces whatever the application does (M4.2).

Each test asserts that **the database** refuses the thing -- not that the application remembers
to. The guarded-transition tests at the bottom run :func:`transition_statement` against real
PostgreSQL, including two sessions racing one row.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from app.operations import states
from app.operations.store import transition_statement
from tests.integration.test_alert_schema import a_user
from tests.integration.test_constraints import _account, _server

SERVICE_SQL = text(
    """
    INSERT INTO services (id, server_id, service_name, monitoring_enabled,
                          last_observed_state, created_at, updated_at)
    VALUES (:id, :server_id, :name, true, 'UNKNOWN', now(), now())
    """
)

OPERATION_SQL = text(
    """
    INSERT INTO operations (id, target_server_id, target_service_id, environment, action_type,
                            requested_by, approval_status, execution_status, idempotency_key,
                            correlation_id, provider, observed_state_after, deadline_at,
                            completed_at)
    VALUES (:id, :server_id, :service_id, :environment, :action, :user, 'NOT_REQUIRED', :status,
            :key, :correlation, 'scripted', :after, now() + interval '300 seconds',
            CASE WHEN :finished THEN now() END)
    """
)


def _service(
    connection: Connection, server_id: uuid.UUID, name: str = "nginx.service"
) -> uuid.UUID:
    service_id = uuid.uuid4()
    connection.execute(SERVICE_SQL, {"id": service_id, "server_id": server_id, "name": name})
    return service_id


def _operation(
    connection: Connection,
    server_id: uuid.UUID,
    service_id: uuid.UUID | None,
    user: uuid.UUID,
    *,
    status: str = "PENDING",
    key: str | None = None,
    environment: str = "staging",
    action: str = "restart",
    after: str | None = None,
    completed: bool | None = None,
) -> uuid.UUID:
    """Insert one row. ``completed`` defaults to "yes exactly when the status is terminal"."""
    operation_id = uuid.uuid4()
    finished = states.is_terminal(status) if completed is None else completed
    connection.execute(
        OPERATION_SQL,
        {
            "id": operation_id,
            "server_id": server_id,
            "service_id": service_id,
            "environment": environment,
            "action": action,
            "user": user,
            "status": status,
            "key": key or str(uuid.uuid4()),
            "correlation": uuid.uuid4(),
            "after": after,
            "finished": finished,
        },
    )
    return operation_id


@contextmanager
def refused(connection: Connection) -> Iterator[None]:
    """Expect an IntegrityError, inside a savepoint so the surrounding transaction survives."""
    savepoint = connection.begin_nested()
    try:
        with pytest.raises(IntegrityError):
            yield
    finally:
        if savepoint.is_active:
            savepoint.rollback()


@pytest.fixture
def target(connection: Connection) -> tuple[Connection, uuid.UUID, uuid.UUID, uuid.UUID]:
    server = _server(connection, _account(connection))
    return connection, server, _service(connection, server), a_user(connection)


Target = tuple[Connection, uuid.UUID, uuid.UUID, uuid.UUID]


# --- the enum CHECKs --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("execution_status", "VERIFYING"),  # never stored (OPEN-69)
        ("execution_status", "DONE"),
        ("execution_status", "succeeded"),
        ("action_type", "exec"),
        ("action_type", "RESTART"),
        ("approval_status", "AUTO"),
        ("environment", "prod"),
        ("environment", "*"),
    ],
)
def test_a_value_outside_a_closed_set_is_refused(target: Target, column: str, value: str) -> None:
    connection, server, service, user = target
    operation = _operation(connection, server, service, user)
    with refused(connection):
        connection.execute(
            text(f"UPDATE operations SET {column} = :value WHERE id = :id"),  # noqa: S608
            {"value": value, "id": operation},
        )


# --- mandatory rule 8, in storage -------------------------------------------------------------


def test_a_success_without_its_verification_evidence_is_refused(target: Target) -> None:
    connection, server, service, user = target
    with refused(connection):
        _operation(connection, server, service, user, status="SUCCEEDED", after=None)


def test_a_success_with_evidence_is_accepted(target: Target) -> None:
    connection, server, service, user = target
    _operation(connection, server, service, user, status="SUCCEEDED", after="ACTIVE")


@pytest.mark.parametrize("status", sorted(states.TERMINAL))
def test_a_terminal_row_without_a_completion_time_is_refused(target: Target, status: str) -> None:
    connection, server, service, user = target
    with refused(connection):
        _operation(
            connection, server, service, user, status=status, after="ACTIVE", completed=False
        )


@pytest.mark.parametrize("status", ["PENDING", "AWAITING_APPROVAL", "APPROVED", "RUNNING"])
def test_a_row_still_in_flight_needs_no_completion_time(target: Target, status: str) -> None:
    connection, server, service, user = target
    _operation(connection, server, service, user, status=status)


# --- the one-in-flight guarantee --------------------------------------------------------------


@pytest.mark.parametrize("held", ["PENDING", "AWAITING_APPROVAL", "APPROVED", "RUNNING"])
@pytest.mark.parametrize("second", ["PENDING", "RUNNING"])
def test_a_second_operation_in_flight_for_one_target_is_refused(
    target: Target, held: str, second: str
) -> None:
    connection, server, service, user = target
    _operation(connection, server, service, user, status=held)
    with refused(connection):
        _operation(connection, server, service, user, status=second)


def test_the_guard_is_per_target_and_released_by_any_terminal_state(target: Target) -> None:
    connection, server, service, user = target
    other_service = _service(connection, server, "cron.service")
    other_server = _server(
        connection,
        _account(connection, "000000000009"),
        resource_id="i-placeholder0000009",
        hostname="other.internal.example",
    )
    other_server_service = _service(connection, other_server)

    _operation(connection, server, service, user, status="RUNNING")
    _operation(connection, server, other_service, user, status="RUNNING")  # another service
    _operation(
        connection, other_server, other_server_service, user, status="RUNNING"
    )  # another host

    for terminal in sorted(states.TERMINAL):  # many finished rows are fine
        _operation(connection, server, service, user, status=terminal, after="ACTIVE")


def test_a_finished_target_can_be_operated_on_again(target: Target) -> None:
    connection, server, service, user = target
    first = _operation(connection, server, service, user, status="PENDING")
    connection.execute(
        text("UPDATE operations SET execution_status='CANCELLED', completed_at=now() WHERE id=:i"),
        {"i": first},
    )
    _operation(connection, server, service, user, status="PENDING")


# --- idempotency ------------------------------------------------------------------------------


def test_the_same_requester_and_key_is_one_operation(target: Target) -> None:
    connection, server, service, user = target
    _operation(connection, server, service, user, status="CANCELLED", key="key-1")
    with refused(connection):
        _operation(connection, server, service, user, status="CANCELLED", key="key-1")


def test_two_requesters_may_use_the_same_key(target: Target) -> None:
    connection, server, service, user = target
    _operation(connection, server, service, user, status="CANCELLED", key="key-1")
    _operation(connection, server, service, a_user(connection), status="CANCELLED", key="key-1")


# --- nothing is ever deleted ------------------------------------------------------------------


@pytest.mark.parametrize(
    "delete",
    [
        "DELETE FROM servers WHERE id = :server",
        "DELETE FROM services WHERE id = :service",
        "DELETE FROM users WHERE id = :user",
    ],
)
def test_a_target_or_requester_with_history_cannot_be_deleted(target: Target, delete: str) -> None:
    connection, server, service, user = target
    _operation(connection, server, service, user, status="CANCELLED")
    with refused(connection):
        connection.execute(text(delete), {"server": server, "service": service, "user": user})


def test_a_parent_operation_must_exist(target: Target) -> None:
    connection, server, service, user = target
    operation = _operation(connection, server, service, user, status="CANCELLED")
    with refused(connection):
        connection.execute(
            text("UPDATE operations SET parent_operation_id = :p WHERE id = :i"),
            {"p": uuid.uuid4(), "i": operation},
        )


# --- the guarded transition against a real database -------------------------------------------


def _run(connection: Connection, statement: Any) -> bool:
    return connection.execute(statement).first() is not None


def test_a_claim_sets_the_start_time_and_a_second_claim_finds_nothing(target: Target) -> None:
    connection, server, service, user = target
    operation = _operation(connection, server, service, user)
    assert _run(connection, transition_statement(operation, "PENDING", "RUNNING"))
    row = connection.execute(
        text("SELECT execution_status, started_at, completed_at FROM operations WHERE id = :i"),
        {"i": operation},
    ).one()
    assert (row.execution_status, row.completed_at) == ("RUNNING", None)
    assert row.started_at is not None
    assert not _run(connection, transition_statement(operation, "PENDING", "RUNNING"))


def test_a_verified_success_is_written_with_its_evidence_and_merged_result(target: Target) -> None:
    connection, server, service, user = target
    operation = _operation(connection, server, service, user)
    connection.execute(
        text("UPDATE operations SET sanitized_result = CAST(:r AS jsonb) WHERE id = :i"),
        {"r": '{"adapter_id": "scripted", "steps": ["submitted"]}', "i": operation},
    )
    assert _run(connection, transition_statement(operation, "PENDING", "RUNNING"))
    assert _run(
        connection,
        transition_statement(
            operation,
            "VERIFYING",  # stored as RUNNING
            "SUCCEEDED",
            action="restart",
            observed_state_after="ACTIVE",
            result={"observed_after": "ACTIVE"},
        ),
    )
    row = connection.execute(
        text(
            "SELECT execution_status, observed_state_after, completed_at, sanitized_result "
            "FROM operations WHERE id = :i"
        ),
        {"i": operation},
    ).one()
    assert (row.execution_status, row.observed_state_after) == ("SUCCEEDED", "ACTIVE")
    assert row.completed_at is not None
    assert row.sanitized_result == {
        "adapter_id": "scripted",
        "steps": ["submitted"],  # kept: the result is merged, not replaced
        "observed_after": "ACTIVE",
    }


def test_an_ambiguous_failure_is_flagged_for_reconciliation_by_the_store(target: Target) -> None:
    connection, server, service, user = target
    operation = _operation(connection, server, service, user, status="RUNNING")
    assert _run(
        connection,
        transition_statement(operation, "RUNNING", "TIMED_OUT", failure_reason="PROVIDER_TIMEOUT"),
    )
    row = connection.execute(
        text("SELECT failure_reason, sanitized_result FROM operations WHERE id = :i"),
        {"i": operation},
    ).one()
    assert row.failure_reason == "PROVIDER_TIMEOUT"
    assert row.sanitized_result["reconciliation_required"] is True


def test_a_refusal_before_anything_was_sent_is_not_flagged(target: Target) -> None:
    connection, server, service, user = target
    operation = _operation(connection, server, service, user, status="RUNNING")
    assert _run(
        connection,
        transition_statement(operation, "RUNNING", "FAILED", failure_reason="PERMISSION_REVOKED"),
    )
    flagged = connection.execute(
        text("SELECT sanitized_result FROM operations WHERE id = :i"), {"i": operation}
    ).scalar_one()
    assert flagged["reconciliation_required"] is False


def test_a_terminal_row_cannot_be_moved_by_the_guarded_update(target: Target) -> None:
    connection, server, service, user = target
    operation = _operation(connection, server, service, user, status="RUNNING")
    assert _run(
        connection,
        transition_statement(operation, "RUNNING", "TIMED_OUT", failure_reason="DEADLINE_EXCEEDED"),
    )
    # A worker that lost the race to the reaper asks to finish; the row is already terminal.
    late = transition_statement(
        operation, "VERIFYING", "SUCCEEDED", action="restart", observed_state_after="ACTIVE"
    )
    assert not _run(connection, late)
    status = connection.execute(
        text("SELECT execution_status FROM operations WHERE id = :i"), {"i": operation}
    ).scalar_one()
    assert status == "TIMED_OUT"


# --- two sessions racing one row (committed data, so removed afterwards) ----------------------


@pytest.fixture
def committed_row(migrated: Engine) -> Iterator[uuid.UUID]:
    with migrated.begin() as connection:
        server = _server(
            connection, _account(connection, "000000000777"), resource_id="i-race0000001"
        )
        service = _service(connection, server)
        user = a_user(connection)
        operation = _operation(connection, server, service, user)
    try:
        yield operation
    finally:
        with migrated.begin() as connection:
            row = connection.execute(
                text(
                    "SELECT target_server_id, target_service_id, requested_by "
                    "FROM operations WHERE id = :i"
                ),
                {"i": operation},
            ).one()
            connection.execute(text("DELETE FROM operations WHERE id = :i"), {"i": operation})
            connection.execute(
                text("DELETE FROM services WHERE id = :i"), {"i": row.target_service_id}
            )
            account = connection.execute(
                text("SELECT cloud_account_id FROM servers WHERE id = :i"),
                {"i": row.target_server_id},
            ).scalar_one()
            connection.execute(
                text("DELETE FROM servers WHERE id = :i"), {"i": row.target_server_id}
            )
            connection.execute(text("DELETE FROM cloud_accounts WHERE id = :i"), {"i": account})
            connection.execute(text("DELETE FROM users WHERE id = :i"), {"i": row.requested_by})


def test_two_workers_racing_one_row_produce_exactly_one_claim(
    migrated: Engine, committed_row: uuid.UUID
) -> None:
    workers = 12
    barrier = threading.Barrier(workers)
    claims: list[bool] = []
    errors: list[BaseException] = []

    def work() -> None:
        try:
            with migrated.begin() as connection:
                barrier.wait(timeout=10)
                claims.append(
                    connection.execute(
                        transition_statement(committed_row, "PENDING", "RUNNING")
                    ).first()
                    is not None
                )
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=work) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert sorted(claims) == [False] * (workers - 1) + [True]


def test_a_second_claim_waits_for_the_first_and_then_loses(
    migrated: Engine, committed_row: uuid.UUID
) -> None:
    """Deterministic version of the race: A claims uncommitted, B must wait on the row lock.

    If the guard were a read followed by a write, B would read ``PENDING``, write, and both
    would think they had the row. With the guarded UPDATE, B blocks, re-evaluates the WHERE
    after A commits, and finds nothing.
    """
    outcome: dict[str, Any] = {}
    first = migrated.connect()
    first_txn = first.begin()
    assert first.execute(transition_statement(committed_row, "PENDING", "RUNNING")).first()

    def second() -> None:
        try:
            with migrated.begin() as connection:
                outcome["claimed"] = (
                    connection.execute(
                        transition_statement(committed_row, "PENDING", "RUNNING")
                    ).first()
                    is not None
                )
        except BaseException as error:
            outcome["error"] = error

    thread = threading.Thread(target=second)
    thread.start()
    time.sleep(1.0)
    assert thread.is_alive(), "B should be blocked on A's uncommitted claim"
    first_txn.commit()
    first.close()
    thread.join(timeout=20)

    assert outcome.get("error") is None, outcome
    assert outcome["claimed"] is False
