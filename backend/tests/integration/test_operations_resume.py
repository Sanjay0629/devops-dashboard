"""Resuming an operation whose worker died after the provider took the command (M4.3, ``OPEN-84``).

The rule being proven: a resumer **polls**; it never sends. Everything here runs against real
PostgreSQL and the scripted provider, counting what the provider was asked.
"""

from __future__ import annotations

import inspect
import uuid

import pytest
from sqlalchemy import Engine, text

from app.operations import execute
from app.operations.store import resume_claim_statement
from tests.integration.inventory_helpers import revoke_all
from tests.integration.test_operations_worker import (  # noqa: F401 -- fixture used by name
    World,
    no_sleep,
    world,
)
from tests.operations.fakes import Scripted


def running(world: World, *, handle: str | None = "cmd-1", stale: bool = True) -> uuid.UUID:  # noqa: F811
    """A ``RUNNING`` row whose worker has (or has not) gone quiet."""
    world.adapter = Scripted(before="ACTIVE", after=("ACTIVE",), statuses=("COMPLETED",))
    operation = world.operation(world.add(), status="RUNNING", handle=handle)
    world.set(
        "UPDATE operations SET"
        " started_at = CASE WHEN :quiet THEN now() - interval '10 minutes' ELSE now() END,"
        " heartbeat_at = CASE WHEN :quiet THEN now() - interval '10 minutes' ELSE now() END,"
        " observed_state_before = 'INACTIVE', sanitized_result = CAST(:r AS jsonb)"
        " WHERE id = :i",
        i=operation,
        quiet=stale,
        r='{"steps": ["claimed", "validated", "submitted"]}',
    )
    return operation


def audit_actions(world: World, operation: uuid.UUID) -> list[str]:  # noqa: F811
    with world.engine.connect() as connection:
        rows = connection.execute(
            text("SELECT action_type FROM audit_events WHERE target_id = :t"), {"t": operation}
        )
        return sorted(r[0] for r in rows)


# --- the happy path: poll from the handle, verify, and send nothing ----------------------------


def test_a_quiet_running_row_is_polled_from_its_handle_and_verified_without_a_second_send(
    world: World,  # noqa: F811
) -> None:
    operation = running(world)

    assert execute.resume(world.engine, operation, sleep=no_sleep) == "SUCCEEDED"

    row = world.row(operation)
    assert row["execution_status"] == "SUCCEEDED" and row["observed_state_after"] == "ACTIVE"
    assert row["external_execution_id"] == "cmd-1"  # the handle that was persisted, unchanged
    assert row["observed_state_before"] == "INACTIVE"  # kept from the first worker
    assert row["sanitized_result"]["steps"] == [
        "claimed",
        "validated",
        "submitted",
        "resumed",
        "completed",
        "verified",
    ]
    # THE assertion: the provider was never asked to act.
    assert world.adapter.submit_attempts == 0 and world.adapter.submitted == []
    assert world.adapter.status_calls >= 1
    assert "SERVICE_OPERATION_RESUMED" in audit_actions(world, operation)
    assert audit_actions(world, operation).count("SERVICE_OPERATION_COMPLETED") == 1


def test_a_provider_that_reports_failure_ends_a_resumed_row_failed(world: World) -> None:  # noqa: F811
    operation = running(world)
    world.adapter = Scripted(
        before="ACTIVE", statuses=("FAILED",), provider_reason="command_failed"
    )
    assert (
        execute.resume(world.engine, operation, sleep=no_sleep)
        == "FAILED:PROVIDER_REPORTED_FAILURE"
    )
    assert world.adapter.submit_attempts == 0


def test_a_resumed_row_that_does_not_reach_the_end_state_is_a_failed_verification(
    world: World,  # noqa: F811
) -> None:
    operation = running(world)
    world.adapter = Scripted(before="INACTIVE", after=("INACTIVE",), statuses=("COMPLETED",))
    assert execute.resume(world.engine, operation, sleep=no_sleep) == "FAILED:VERIFICATION_MISMATCH"


def test_the_worker_writes_a_heartbeat_while_it_works(world: World) -> None:  # noqa: F811
    world.adapter = Scripted()
    operation = world.operation(world.add())
    assert world.run(operation) == "SUCCEEDED"
    assert world.row(operation)["heartbeat_at"] is not None


# --- when it must NOT resume -----------------------------------------------------------------


def test_a_row_whose_worker_is_still_alive_is_left_alone(world: World) -> None:  # noqa: F811
    operation = running(world, stale=False)
    assert execute.resume(world.engine, operation, sleep=no_sleep) == "lost_claim"
    assert world.adapter.status_calls == 0 and world.row(operation)["execution_status"] == "RUNNING"


def test_a_row_without_a_handle_is_not_resumable(world: World) -> None:  # noqa: F811
    operation = running(world, handle=None)
    assert execute.resume(world.engine, operation, sleep=no_sleep) == "not_resumable"
    assert world.adapter.status_calls == 0 and world.adapter.submit_attempts == 0


def test_a_row_past_its_deadline_is_left_to_the_reaper(world: World) -> None:  # noqa: F811
    operation = running(world)
    world.set(
        "UPDATE operations SET deadline_at = now() - interval '1 minute' WHERE id = :i", i=operation
    )
    assert execute.resume(world.engine, operation, sleep=no_sleep) == "past_deadline"
    assert world.row(operation)["execution_status"] == "RUNNING"


@pytest.mark.parametrize("status", ["PENDING", "SUCCEEDED", "FAILED"])
def test_only_a_running_row_can_be_resumed(world: World, status: str) -> None:  # noqa: F811
    world.adapter = Scripted()
    operation = world.operation(world.add(), status=status, handle="cmd-1")
    assert execute.resume(world.engine, operation, sleep=no_sleep) == "not_resumable"


def test_an_unknown_row_is_not_resumable(world: World) -> None:  # noqa: F811
    assert execute.resume(world.engine, uuid.uuid4(), sleep=no_sleep) == "not_resumable"


# --- exactly one resumer wins ---------------------------------------------------------------


def test_of_two_resumers_that_saw_the_same_heartbeat_exactly_one_wins(world: World) -> None:  # noqa: F811
    operation = running(world)
    seen = world.row(operation)["heartbeat_at"]

    def claim(heartbeat: object) -> bool:
        with world.engine.begin() as connection:
            return (
                connection.execute(
                    resume_claim_statement(
                        operation,
                        heartbeat,
                        stale_after_seconds=120,
                        steps=["resumed"],  # type: ignore[arg-type]
                    )
                ).first()
                is not None
            )

    assert claim(seen) is True
    # The row is quiet again (as if the first resumer died too) but the second resumer holds the
    # value it read BEFORE the first one wrote: only the compare-and-set can refuse it.
    world.set(
        "UPDATE operations SET heartbeat_at = now() - interval '10 minutes' WHERE id = :i",
        i=operation,
    )
    assert claim(seen) is False
    assert claim(world.row(operation)["heartbeat_at"]) is True


def test_a_second_resume_while_the_first_is_polling_does_nothing(world: World) -> None:  # noqa: F811
    operation = running(world)
    outcomes: list[str] = []

    def second_resumer() -> None:
        outcomes.append(execute.resume(world.engine, operation, sleep=no_sleep))

    world.adapter = Scripted(before="ACTIVE", statuses=("COMPLETED",), on_status=second_resumer)
    assert execute.resume(world.engine, operation, sleep=no_sleep) == "SUCCEEDED"
    assert outcomes == ["lost_claim"]
    assert audit_actions(world, operation).count("SERVICE_OPERATION_RESUMED") == 1


# --- the re-check ---------------------------------------------------------------------------


def test_a_permission_revoked_while_the_worker_was_dead_stops_the_resume(world: World) -> None:  # noqa: F811
    operation = running(world)
    revoke_all(world.engine, world.user)

    assert execute.resume(world.engine, operation, sleep=no_sleep) == "FAILED:RESUME_REFUSED"

    row = world.row(operation)
    assert row["execution_status"] == "FAILED" and row["failure_reason"] == "RESUME_REFUSED"
    # A command may still be running that nobody follows: that must be flagged, not hidden.
    assert row["sanitized_result"]["reconciliation_required"] is True
    assert row["sanitized_result"]["reason_code"] == "permission_revoked"
    assert world.adapter.status_calls == 0 and world.adapter.submit_attempts == 0
    assert "SERVICE_OPERATION_DENIED" in audit_actions(world, operation)


def test_a_disabled_account_stops_the_resume(world: World) -> None:  # noqa: F811
    operation = running(world)
    world.set("UPDATE cloud_accounts SET status = 'DISABLED' WHERE id = :a", a=world.account)
    try:
        assert execute.resume(world.engine, operation, sleep=no_sleep) == "FAILED:RESUME_REFUSED"
        assert world.row(operation)["sanitized_result"]["reason_code"] == "account_disabled"
    finally:
        world.set("UPDATE cloud_accounts SET status = 'ACTIVE' WHERE id = :a", a=world.account)


# --- the healer -----------------------------------------------------------------------------


def test_the_healer_hands_only_stalled_running_rows_with_a_handle_to_the_resumer(
    world: World,  # noqa: F811
) -> None:
    stalled = running(world)
    alive = running(world, stale=False)
    no_handle = running(world, handle=None)
    overdue = running(world)
    world.set(
        "UPDATE operations SET deadline_at = now() - interval '1 minute' WHERE id = :i", i=overdue
    )
    pending = world.operation(world.add())

    resumed: list[uuid.UUID] = []
    summary = execute.reap(world.engine, enqueue=lambda _: None, enqueue_resume=resumed.append)

    assert stalled in resumed
    for other in (alive, no_handle, overdue, pending):
        assert other not in resumed
    assert summary["resume_requeued"] == len(resumed) >= 1
    # The reaper's own job is unchanged: the overdue row is timed out, never resumed.
    assert world.row(overdue)["execution_status"] == "TIMED_OUT"
    assert world.adapter.status_calls == 0  # the healer asks the provider nothing


def test_without_a_resume_callback_the_healer_resumes_nothing(world: World) -> None:  # noqa: F811
    running(world)
    assert execute.reap(world.engine, enqueue=lambda _: None)["resume_requeued"] == 0


# --- the region boundary --------------------------------------------------------------------


def test_a_server_outside_the_accounts_allowed_regions_is_refused_before_anything_is_sent(
    world: World,  # noqa: F811
) -> None:
    world.set(
        "UPDATE cloud_accounts SET region_configuration = CAST(:c AS jsonb) WHERE id = :a",
        a=world.account,
        c='{"allowed_regions": ["eu-west-1"], "default_region": "eu-west-1"}',
    )
    try:
        target = world.add()
        world.set("UPDATE servers SET region = 'us-east-1' WHERE id = :s", s=target.server)
        operation = world.operation(target)
        assert world.run(operation) == "FAILED:TARGET_VALIDATION_FAILED"
        assert world.row(operation)["sanitized_result"]["reason_code"] == "region_not_allowed"
        assert world.adapter.submit_attempts == 0
        world.set("UPDATE servers SET region = 'eu-west-1' WHERE id = :s", s=target.server)
        assert world.run(world.operation(target)) == "SUCCEEDED"
    finally:
        world.set(
            "UPDATE cloud_accounts SET region_configuration = '{}' WHERE id = :a", a=world.account
        )


def test_a_server_with_no_region_cannot_pass_a_regional_boundary(world: World) -> None:  # noqa: F811
    world.set(
        "UPDATE cloud_accounts SET region_configuration = CAST(:c AS jsonb) WHERE id = :a",
        a=world.account,
        c='{"allowed_regions": ["eu-west-1"]}',
    )
    try:
        target = world.add()
        world.set("UPDATE servers SET region = NULL WHERE id = :s", s=target.server)
        assert world.run(world.operation(target)) == "FAILED:TARGET_VALIDATION_FAILED"
    finally:
        world.set(
            "UPDATE cloud_accounts SET region_configuration = '{}' WHERE id = :a", a=world.account
        )


# --- the task -------------------------------------------------------------------------------


def test_the_resume_task_takes_an_id_and_nothing_else(migrated: Engine) -> None:
    from app.workers.celery_app import celery_app
    from app.workers.tasks import operations as tasks

    assert list(inspect.signature(tasks.resume_operation).parameters) == ["operation_id"]
    task = celery_app.tasks["dcp.operations.resume"]
    assert task.queue == "operations"
    assert tasks.resume_operation("not-a-uuid") == {"outcome": "bad_id"}


def test_heartbeat_at_is_a_nullable_timestamptz_column(migrated: Engine) -> None:
    with migrated.connect() as connection:
        row = connection.execute(
            text(
                "SELECT data_type, is_nullable FROM information_schema.columns"
                " WHERE table_name = 'operations' AND column_name = 'heartbeat_at'"
            )
        ).one()
    assert tuple(row) == ("timestamp with time zone", "YES")
