"""The ``deployments`` table: what the schema enforces whatever the application does (M5.2).

Each test asserts that **the database** refuses the thing (docs/DATABASE.md §5.2, ``OPEN-135``):
one request per requester and key, one active deployment per application and environment, a
success that carries health evidence, a build number on a running or succeeded row, a completion
time on a finished one, and a failure reason exactly on a failed one. The state machine is the
independent second guard for the success rule and is tested in ``tests/deployments``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DataError, IntegrityError

from app.deployments import policy, states
from tests.integration.test_alert_schema import a_user
from tests.integration.test_deployment_targets_schema import a_target, an_application
from tests.integration.test_operation_schema import refused

DIGEST = "0" * 64

INSERT = text(
    """
    INSERT INTO deployments (id, application_id, deployment_target_id, environment,
                             pipeline_reference, commit_sha, requested_ref, kind,
                             rollback_of_deployment_id, requested_by, requested_reason,
                             approval_status, execution_status, external_execution_id,
                             queue_item_id, idempotency_key, correlation_id, target_digest,
                             health_check_result, failure_reason, poll_attempts, deadline_at,
                             completed_at)
    VALUES (:id, :application_id, :target_id, :environment, :pipeline_reference, :commit_sha,
            :ref, :kind, :rollback_of, :user, :reason, :approval, :status, :build, :queue_item,
            :key, :correlation, :digest, CAST(:health AS jsonb), :failure, :poll_attempts,
            now() + interval '3600 seconds', CASE WHEN :completed THEN now() END)
    """
)


@contextmanager
def refused_by(connection: Connection, constraint: str) -> Iterator[None]:
    """Expect an IntegrityError **from this constraint**, so a test cannot pass on another one."""
    savepoint = connection.begin_nested()
    try:
        with pytest.raises(IntegrityError) as raised:
            yield
        assert constraint in str(raised.value)
    finally:
        if savepoint.is_active:
            savepoint.rollback()


@contextmanager
def rejected(connection: Connection) -> Iterator[None]:
    """Expect the database to refuse a value: a CHECK, or the column's length (``DataError``)."""
    savepoint = connection.begin_nested()
    try:
        with pytest.raises((IntegrityError, DataError)):
            yield
    finally:
        if savepoint.is_active:
            savepoint.rollback()


class Setup:
    """An application, a registered target and a requester to hang deployments on."""

    def __init__(self, connection: Connection, environment: str = "staging") -> None:
        self.connection = connection
        self.environment = environment
        self.application = an_application(connection)
        self.target = a_target(connection, self.application, environment=environment)
        self.user = a_user(connection)

    def deployment(self, **overrides: Any) -> uuid.UUID:
        row: dict[str, Any] = {
            "id": uuid.uuid4(),
            "application_id": self.application,
            "target_id": self.target,
            "environment": self.environment,
            "pipeline_reference": "team/service-deploy",
            "commit_sha": None,
            "ref": "main",
            "kind": "DEPLOY",
            "rollback_of": None,
            "user": self.user,
            "reason": None,
            "approval": "NOT_REQUIRED",
            "status": "REQUESTED",
            "build": None,
            "queue_item": None,
            "key": uuid.uuid4().hex,
            "correlation": uuid.uuid4(),
            "digest": DIGEST,
            "health": "{}",
            "failure": None,
            "poll_attempts": 0,
            "completed": False,
        }
        row.update(overrides)
        self.connection.execute(INSERT, row)
        return row["id"]  # type: ignore[no-any-return]

    def succeeded(self, **overrides: Any) -> uuid.UUID:
        return self.deployment(
            status="SUCCEEDED",
            build="42",
            health=json.dumps(policy.none_configured()),
            completed=True,
            **overrides,
        )

    def failed(self, reason: str = "PIPELINE_FAILED", **overrides: Any) -> uuid.UUID:
        return self.deployment(status="FAILED", failure=reason, completed=True, **overrides)


@pytest.fixture
def s(connection: Connection) -> Setup:
    return Setup(connection)


def test_a_legitimate_row_in_every_status_is_accepted(s: Setup) -> None:
    """One row per status, each with exactly what its status requires; none is in flight twice."""
    s.succeeded()
    s.failed()
    s.deployment(status="CANCELLED", completed=True)
    s.deployment(status="ROLLED_BACK", build="41", completed=True)
    s.deployment(status="RUNNING", build="43")  # in flight, alone


@pytest.mark.parametrize("status", states.STATUSES)
def test_every_documented_status_can_be_stored(connection: Connection, status: str) -> None:
    s = Setup(connection)
    extra: dict[str, Any] = {}
    if status in states.TERMINAL:
        extra["completed"] = True
    if status in {"RUNNING", "SUCCEEDED"}:
        extra["build"] = "7"
    if status == "SUCCEEDED":
        extra["health"] = json.dumps(policy.none_configured())
    if status == "FAILED":
        extra["failure"] = "PIPELINE_FAILED"
    s.deployment(status=status, **extra)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("environment", "prod"),
        ("environment", "PRODUCTION"),
        ("status", "TIMED_OUT"),  # there is no such status (§21)
        ("status", "VERIFYING"),
        ("status", "running"),
        ("approval", "EXPIRED"),  # an operations value
        ("approval", "approved"),
        ("kind", "REDEPLOY"),
    ],
)
def test_closed_sets_are_constraints(s: Setup, column: str, value: str) -> None:
    with refused(s.connection):
        s.deployment(**{column: value})


@pytest.mark.parametrize(
    "reference", ["", "../escape", "a/../b", ".hidden", "a//b", "a/", "a b", "a?x=1", "a\nb"]
)
def test_the_job_path_snapshot_obeys_the_target_grammar(s: Setup, reference: str) -> None:
    with refused(s.connection):
        s.deployment(pipeline_reference=reference)


@pytest.mark.parametrize("ref", ["", "x" * 201])
def test_the_requested_ref_is_bounded(s: Setup, ref: str) -> None:
    with rejected(s.connection):
        s.deployment(ref=ref)


def test_a_two_hundred_character_ref_is_accepted(s: Setup) -> None:
    s.deployment(ref="r" * 200)


@pytest.mark.parametrize("sha", ["abc123", "ABCDEF1", "g" * 40, "a" * 41, " abcdef1", "abcdef1\n"])
def test_a_commit_sha_has_a_shape(s: Setup, sha: str) -> None:
    with rejected(s.connection):
        s.deployment(commit_sha=sha)


@pytest.mark.parametrize("sha", ["abcdef1", "0123456789abcdef0123456789abcdef01234567"])
def test_a_real_commit_sha_is_accepted(s: Setup, sha: str) -> None:
    s.deployment(commit_sha=sha)


@pytest.mark.parametrize("digest", ["", "0" * 63, "0" * 65, "G" * 64, "A" * 64])
def test_the_target_digest_has_a_shape(s: Setup, digest: str) -> None:
    with rejected(s.connection):
        s.deployment(digest=digest)


def test_a_reason_is_bounded(s: Setup) -> None:
    s.deployment(reason="r" * 500)
    with refused(s.connection):
        s.deployment(reason="r" * 501)


def test_poll_attempts_cannot_be_negative(s: Setup) -> None:
    with refused(s.connection):
        s.deployment(poll_attempts=-1)


# --- mandatory rule 8: a success carries evidence --------------------------------------------


def test_a_success_with_no_health_evidence_is_refused_by_the_schema(s: Setup) -> None:
    """The schema's half of the two guards (the state machine is the other)."""
    with refused_by(s.connection, "ck_deployments_success_evidence"):
        s.deployment(status="SUCCEEDED", build="42", health="{}", completed=True)


def test_a_success_with_the_explicit_none_configured_result_is_accepted(s: Setup) -> None:
    s.succeeded()


def test_the_schema_checks_presence_not_truth_which_is_the_machines_job() -> None:
    """Stated, not hidden: DATABASE §5.2's accepted limitation (a failing result passes)."""
    failing = {"policy": "all_must_pass", "configured": True, "checks": [], "passed": False}
    assert failing != {}
    assert policy.success("SUCCESS", failing) is False  # the machine refuses what the CHECK allows


@pytest.mark.parametrize("status", ["RUNNING", "SUCCEEDED"])
def test_a_running_or_succeeded_row_has_its_build_number(s: Setup, status: str) -> None:
    extra: dict[str, Any] = {"completed": status == "SUCCEEDED"}
    if status == "SUCCEEDED":
        extra["health"] = json.dumps(policy.none_configured())
    with refused(s.connection):
        s.deployment(status=status, build=None, **extra)


def test_a_queued_row_may_have_no_build_number_yet(s: Setup) -> None:
    s.deployment(status="QUEUED", build=None, queue_item=None)  # the trigger may have been sent


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "CANCELLED", "ROLLED_BACK"])
def test_a_finished_row_has_a_completion_time(s: Setup, status: str) -> None:
    extra: dict[str, Any] = {}
    if status in {"SUCCEEDED", "ROLLED_BACK"}:
        extra["build"] = "9"
    if status == "SUCCEEDED":
        extra["health"] = json.dumps(policy.none_configured())
    if status == "FAILED":
        extra["failure"] = "PIPELINE_FAILED"
    with refused(s.connection):
        s.deployment(status=status, completed=False, **extra)


def test_a_row_is_failed_exactly_when_it_has_a_failure_reason(s: Setup) -> None:
    with refused(s.connection):
        s.deployment(status="FAILED", failure=None, completed=True)
    with refused(s.connection):
        s.deployment(status="REQUESTED", failure="PIPELINE_FAILED")
    with refused(s.connection):
        s.deployment(status="CANCELLED", failure="PIPELINE_FAILED", completed=True)


def test_a_rollback_names_what_it_rolls_back_and_nothing_else_does(s: Setup) -> None:
    original = s.succeeded()
    s.deployment(kind="ROLLBACK", rollback_of=original, ref="main")
    with refused(s.connection):
        s.deployment(kind="ROLLBACK", rollback_of=None)
    with refused(s.connection):
        s.deployment(kind="DEPLOY", rollback_of=original)


def test_a_rollback_target_must_exist(s: Setup) -> None:
    with refused(s.connection):
        s.deployment(kind="ROLLBACK", rollback_of=uuid.uuid4())


# --- the storage guarantees -------------------------------------------------------------------


def test_one_requester_and_key_is_one_deployment(s: Setup) -> None:
    s.failed(key="same-key")
    with refused_by(s.connection, "uq_deployments_idempotency"):
        s.failed(key="same-key")


def test_a_key_is_scoped_to_its_requester(s: Setup) -> None:
    other = a_user(s.connection)
    s.failed(key="same-key")
    s.failed(key="same-key", user=other)


@pytest.mark.parametrize("first", states.IN_FLIGHT)
@pytest.mark.parametrize("second", states.IN_FLIGHT)
def test_only_one_deployment_per_application_and_environment_is_active(
    connection: Connection, first: str, second: str
) -> None:
    """The milestone's storage guarantee: no pair of in-flight statuses can coexist."""
    s = Setup(connection)
    s.deployment(status=first, build="1" if first == "RUNNING" else None)
    with refused_by(connection, "uq_deployments_single_inflight"):
        s.deployment(status=second, build="2" if second == "RUNNING" else None)


def test_a_finished_deployment_frees_the_slot(s: Setup) -> None:
    first = s.deployment(status="QUEUED")
    s.connection.execute(
        text(
            "UPDATE deployments SET execution_status = 'FAILED', "
            "failure_reason = 'PIPELINE_FAILED', completed_at = now() WHERE id = :id"
        ),
        {"id": first},
    )
    s.deployment(status="REQUESTED")


def test_any_number_of_finished_deployments_may_coexist(s: Setup) -> None:
    for _ in range(3):
        s.succeeded()
        s.failed()
        s.deployment(status="CANCELLED", completed=True)


def test_a_different_environment_or_application_has_its_own_slot(connection: Connection) -> None:
    staging = Setup(connection, "staging")
    staging.deployment(status="REQUESTED")
    # Same application, another environment: its own target, its own slot.
    other_target = a_target(connection, staging.application, environment="development")
    staging.deployment(status="REQUESTED", environment="development", target_id=other_target)
    # Another application altogether.
    Setup(connection, "staging").deployment(status="REQUESTED")


def test_a_second_request_cannot_be_started_by_updating_a_finished_row_back_to_flight(
    s: Setup,
) -> None:
    s.deployment(status="RUNNING", build="1")
    done = s.failed()
    with refused(s.connection):
        s.connection.execute(
            text(
                "UPDATE deployments SET execution_status = 'REQUESTED', failure_reason = NULL "
                "WHERE id = :id"
            ),
            {"id": done},
        )


# --- history is never deleted -----------------------------------------------------------------


def test_a_deployment_blocks_deleting_its_target_and_requester(s: Setup) -> None:
    s.failed()
    for table, key in (("deployment_targets", s.target), ("users", s.user)):
        with refused_by(s.connection, "fk_deployments_"):
            s.connection.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": key})  # noqa: S608


def test_an_application_with_deployments_cannot_be_deleted(s: Setup) -> None:
    """Refused already by its target's key; stated because history hangs on the application."""
    s.failed()
    with refused(s.connection):
        s.connection.execute(text("DELETE FROM applications WHERE id = :id"), {"id": s.application})


def test_a_deployment_cannot_claim_another_applications_target(s: Setup) -> None:
    """The composite key: the application is the target's, not merely claimed."""
    other_application = an_application(s.connection)
    with refused_by(s.connection, "fk_deployments_deployment_target_id_deployment_targets"):
        s.deployment(application_id=other_application)


@pytest.mark.parametrize("environment", ["production", "development", "other"])
def test_a_deployment_cannot_store_an_environment_its_target_does_not_have(
    s: Setup, environment: str
) -> None:
    """The stored environment is the authorisation basis for reading it; it must be the target's."""
    with refused_by(s.connection, "fk_deployments_deployment_target_id_deployment_targets"):
        s.deployment(environment=environment)


def test_a_deployment_against_a_target_that_does_not_exist_is_refused(s: Setup) -> None:
    with refused_by(s.connection, "fk_deployments_deployment_target_id_deployment_targets"):
        s.deployment(target_id=uuid.uuid4())


def test_a_rollback_blocks_deleting_what_it_rolled_back(s: Setup) -> None:
    original = s.succeeded()
    s.deployment(kind="ROLLBACK", rollback_of=original)
    with refused_by(s.connection, "fk_deployments_rollback_of_deployment_id_deployments"):
        s.connection.execute(text("DELETE FROM deployments WHERE id = :id"), {"id": original})
