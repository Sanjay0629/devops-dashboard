"""The operation worker against real PostgreSQL and a scripted provider (M4.2, ``OPEN-70``-``72``).

``execute.run`` is driven directly with a clock-free ``sleep``; the Celery wrappers are tested for
their shape only. There is no real provider: these tests prove the state machine's lifecycle
against a fake that can be told to fail in every way the worker has a rule for. Passing them is
not evidence that any cloud works.
"""

from __future__ import annotations

import inspect
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.operations import execute
from app.providers import registry
from app.providers.capabilities import CapabilitySet, Support
from app.providers.errors import (
    AuthenticationError,
    NotFound,
    PartialFailure,
    ProviderUnavailable,
    Throttled,
    Timeout,
)
from app.providers.types import ServiceAction
from tests.integration.inventory_helpers import (
    FULL_SCOPE,
    account_id,
    grant,
    retire_servers,
    revoke_all,
)
from tests.integration.test_alert_schema import a_user
from tests.integration.test_auth_flow import redis_url  # noqa: F401 -- fixture used by name
from tests.integration.test_constraints import _server
from tests.operations.fakes import Scripted

ALL = "test-ops-all"
UNIT = "web.service"
NOW = "now()"


def no_sleep(_: float) -> None:
    return None


@dataclass
class Target:
    server: uuid.UUID
    service: uuid.UUID


@dataclass
class World:
    engine: Engine
    user: uuid.UUID
    account: uuid.UUID
    monkeypatch: pytest.MonkeyPatch
    adapter: Scripted = field(default_factory=Scripted)
    servers: list[str] = field(default_factory=list)

    def add(
        self, *, environment: str = "staging", policy: str | None = ALL, unit: str = UNIT
    ) -> Target:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO service_action_policies (id, name, allowed_actions,"
                    " approval_policy_id, max_concurrent_operations, operation_timeout_seconds,"
                    " created_at, updated_at) VALUES (gen_random_uuid(), :n,"
                    " ARRAY['start','stop','restart']::varchar[], NULL, 1, 300, now(), now())"
                    " ON CONFLICT (name) DO NOTHING"
                ),
                {"n": ALL},
            )
            server = _server(
                connection,
                self.account,
                resource_id=f"i-{uuid.uuid4().hex[:12]}",
                hostname=f"h{uuid.uuid4().hex[:10]}.internal.example",
                environment=environment,
            )
            connection.execute(
                text("UPDATE servers SET status = 'MANAGED' WHERE id = :i"), {"i": server}
            )
            service = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO services (id, server_id, service_name, monitoring_enabled,"
                    " last_observed_state, action_policy_id, created_at, updated_at)"
                    " VALUES (:i, :s, :n, true, 'UNKNOWN',"
                    " (SELECT id FROM service_action_policies WHERE name = :p), now(), now())"
                ),
                {"i": service, "s": server, "n": unit, "p": policy},
            )
        self.servers.append(str(server))
        return Target(server, service)

    def operation(
        self,
        target: Target,
        *,
        status: str = "PENDING",
        action: str = "restart",
        environment: str | None = None,
        approval: str = "NOT_REQUIRED",
        deadline_in: float = 300.0,
        handle: str | None = None,
        provider: str = "scripted",
    ) -> uuid.UUID:
        operation = uuid.uuid4()
        terminal = status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "REJECTED")
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO operations (id, target_server_id, target_service_id,"
                    " environment, action_type, requested_by, approval_status, execution_status,"
                    " idempotency_key, correlation_id, provider, external_execution_id,"
                    " observed_state_after, deadline_at, started_at, completed_at, created_at)"
                    " SELECT :o, :sv, :sc, COALESCE(CAST(:env AS varchar), v.environment), :a, :u,"
                    " :ap, :st, :k, :c, :pr, :h, CASE WHEN :ok THEN 'ACTIVE' END,"
                    " now() + make_interval(secs => CAST(:d AS float8)),"
                    " CASE WHEN :running THEN now() END, CASE WHEN :done THEN now() END,"
                    " now() - interval '1 minute'"
                    " FROM servers v WHERE v.id = :sv"
                ),
                {
                    "o": operation,
                    "sv": target.server,
                    "sc": target.service,
                    "env": environment,
                    "a": action,
                    "u": self.user,
                    "ap": approval,
                    "st": status,
                    "k": str(uuid.uuid4()),
                    "c": uuid.uuid4(),
                    "pr": provider,
                    "h": handle,
                    "d": deadline_in,
                    "ok": status == "SUCCEEDED",
                    "running": status == "RUNNING",
                    "done": terminal,
                },
            )
        return operation

    def row(self, operation: uuid.UUID) -> dict[str, Any]:
        with self.engine.connect() as connection:
            result = connection.execute(
                text("SELECT * FROM operations WHERE id = :i"), {"i": operation}
            )
            return dict(result.mappings().one())

    def audit(self, operation: uuid.UUID, action: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            result = connection.execute(
                text(
                    "SELECT actor_id, actor_label, outcome, correlation_id, sanitized_metadata"
                    " FROM audit_events WHERE action_type = :a AND target_id = :t"
                ),
                {"a": action, "t": operation},
            )
            return [dict(r) for r in result.mappings()]

    def run(self, operation: uuid.UUID, sleep: Callable[[float], None] = no_sleep) -> str:
        return execute.run(self.engine, operation, sleep=sleep)

    def set(self, sql: str, **params: Any) -> None:
        with self.engine.begin() as connection:
            connection.execute(text(sql), params)


@pytest.fixture
def world(migrated: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setenv("DCP_DEMO_MODE", "false")
    get_settings.cache_clear()
    user = a_user_committed(migrated)
    grant(migrated, user, "DEVOPS_ENGINEER", FULL_SCOPE)
    built = World(migrated, user, account_id(migrated), monkeypatch)
    monkeypatch.setitem(registry._FACTORIES, "ON_PREM", lambda: built.adapter)
    yield built
    revoke_all(migrated, user)
    retire_servers(migrated, built.servers)
    get_settings.cache_clear()


def a_user_committed(engine: Engine) -> uuid.UUID:
    with engine.begin() as connection:
        return a_user(connection)


def assert_nothing_was_sent(world: World) -> None:
    assert world.adapter.submit_attempts == 0 and world.adapter.submitted == []


# --- the lifecycle ---------------------------------------------------------------------------


def test_a_restart_is_claimed_invoked_polled_verified_and_recorded(world: World) -> None:
    target = world.add()
    operation = world.operation(target)
    delays: list[float] = []

    assert world.run(operation, sleep=delays.append) == "SUCCEEDED"

    row = world.row(operation)
    assert row["execution_status"] == "SUCCEEDED"
    assert (row["observed_state_before"], row["observed_state_after"]) == ("INACTIVE", "ACTIVE")
    assert row["external_execution_id"] == "scripted-1" and row["failure_reason"] is None
    assert row["started_at"] is not None and row["completed_at"] >= row["started_at"]
    assert row["sanitized_result"]["steps"] == [
        "claimed",
        "validated",
        "submitted",
        "completed",
        "verified",
    ]
    assert "reconciliation_required" not in row["sanitized_result"]  # only a failure sets it
    assert world.adapter.submitted == [(ServiceAction.RESTART, operation)]
    assert delays == [1.0]  # one poll wait, then COMPLETED

    [started] = world.audit(operation, "SERVICE_OPERATION_STARTED")
    [completed] = world.audit(operation, "SERVICE_OPERATION_COMPLETED")
    assert started["actor_id"] == world.user == completed["actor_id"]
    assert started["actor_label"] == "system:operations-worker"
    assert started["correlation_id"] == row["correlation_id"] == completed["correlation_id"]
    assert completed["outcome"] == "SUCCESS"
    assert completed["sanitized_metadata"]["on_behalf_of"] == str(world.user)


def test_a_stop_is_verified_as_inactive(world: World) -> None:
    world.adapter = Scripted(before="ACTIVE", after=("INACTIVE",))
    operation = world.operation(world.add(), action="stop")
    assert world.run(operation) == "SUCCEEDED"
    assert world.row(operation)["observed_state_after"] == "INACTIVE"


def test_an_approved_row_is_claimed_like_a_pending_one(world: World) -> None:
    operation = world.operation(world.add(), status="APPROVED", approval="APPROVED")
    assert world.run(operation) == "SUCCEEDED"


def test_a_state_that_is_still_settling_is_read_again_before_it_is_judged(world: World) -> None:
    world.adapter = Scripted(after=("ACTIVATING", "ACTIVATING", "ACTIVE"))
    operation = world.operation(world.add())
    delays: list[float] = []
    assert world.run(operation, sleep=delays.append) == "SUCCEEDED"
    assert execute.VERIFY_INTERVAL_SECONDS in delays and world.adapter.list_calls == 4


def test_the_handle_is_persisted_before_the_first_poll(world: World) -> None:
    """AD-08: the provider's id is committed before polling, so a crash can still find it."""
    seen: list[str | None] = []
    operation = world.operation(world.add())

    def peek() -> None:
        seen.append(world.row(operation)["external_execution_id"])

    world.adapter = Scripted(on_status=peek)
    assert world.run(operation) == "SUCCEEDED"
    assert seen and all(handle == "scripted-1" for handle in seen)


# --- verification: the worst failure mode is a wrongly reported success ----------------------


@pytest.mark.parametrize(
    ("action", "before", "after"),
    [
        ("restart", "INACTIVE", ("INACTIVE",)),  # the provider said done; the unit is down
        ("restart", "FAILED", ("FAILED",)),
        ("start", "INACTIVE", ("ACTIVATING",)),  # never settled
        ("stop", "ACTIVE", ("ACTIVE",)),  # the stop did nothing
    ],
)
def test_a_provider_that_says_done_while_the_unit_is_wrong_is_failed_never_succeeded(
    world: World, action: str, before: str, after: tuple[str, ...]
) -> None:
    world.adapter = Scripted(before=before, after=after)
    operation = world.operation(world.add(), action=action)

    outcome = world.run(operation)

    assert outcome == "FAILED:VERIFICATION_MISMATCH"
    row = world.row(operation)
    assert row["execution_status"] == "FAILED" and row["observed_state_after"] == after[-1]
    assert row["sanitized_result"]["reconciliation_required"] is False
    assert world.audit(operation, "SERVICE_OPERATION_COMPLETED")[0]["outcome"] == "FAILURE"


@pytest.mark.parametrize("read_state", ["stale", "unavailable", "not_configured"])
def test_a_verification_read_that_is_not_ok_cannot_prove_success(
    world: World, read_state: str
) -> None:
    world.adapter = Scripted(read_state=read_state)
    operation = world.operation(world.add())
    assert world.run(operation) == "FAILED:VERIFICATION_UNAVAILABLE"
    row = world.row(operation)
    assert row["observed_state_after"] is None
    assert row["sanitized_result"]["reconciliation_required"] is True


def test_the_unit_disappearing_from_the_host_is_unverifiable_not_a_success(world: World) -> None:
    world.adapter = Scripted(after_unit="other.service")
    operation = world.operation(world.add())
    assert world.run(operation) == "FAILED:VERIFICATION_UNAVAILABLE"


def test_a_provider_that_reports_failure_is_failed_with_its_closed_reason_kept(
    world: World,
) -> None:
    world.adapter = Scripted(statuses=("IN_PROGRESS", "FAILED"), provider_reason="exit_code_1")
    operation = world.operation(world.add())
    assert world.run(operation) == "FAILED:PROVIDER_REPORTED_FAILURE"
    row = world.row(operation)
    assert row["sanitized_result"]["reason_code"] == "exit_code_1"
    assert row["sanitized_result"]["reconciliation_required"] is False


def test_free_text_from_a_provider_is_never_stored_as_a_reason(world: World) -> None:
    world.adapter = Scripted(
        statuses=("FAILED",), provider_reason="Permission denied for AKIAEXAMPLEKEY123"
    )
    operation = world.operation(world.add())
    world.run(operation)
    assert "reason_code" not in world.row(operation)["sanitized_result"]


# --- provider errors on the state-changing call: never retried -------------------------------


@pytest.mark.parametrize(
    ("error", "outcome", "flagged"),
    [
        (AuthenticationError(adapter_id="scripted"), "FAILED:PROVIDER_AUTHENTICATION", False),
        (NotFound(adapter_id="scripted"), "FAILED:PROVIDER_NOT_FOUND", False),
        (Throttled(retry_after=1, adapter_id="scripted"), "FAILED:PROVIDER_THROTTLED", False),
        (Timeout(adapter_id="scripted"), "TIMED_OUT:PROVIDER_TIMEOUT", True),
        (ProviderUnavailable(adapter_id="scripted"), "FAILED:PROVIDER_UNAVAILABLE", True),
        (PartialFailure(adapter_id="scripted"), "FAILED:PROVIDER_PARTIAL_FAILURE", True),
        (RuntimeError("secret AKIA… in a message"), "FAILED:PROVIDER_UNAVAILABLE", True),
    ],
)
def test_a_failed_invoke_ends_the_row_is_never_retried_and_is_flagged_only_when_ambiguous(
    world: World, error: BaseException, outcome: str, flagged: bool
) -> None:
    world.adapter = Scripted(submit_error=error)
    operation = world.operation(world.add())

    assert world.run(operation) == outcome

    assert (
        world.adapter.submit_attempts == 1
    )  # CLAUDE.md §5: a state-changing call is never retried
    row = world.row(operation)
    assert row["sanitized_result"]["reconciliation_required"] is flagged
    assert row["external_execution_id"] is None
    assert "AKIA" not in str(row["sanitized_result"]) and "secret" not in str(
        row["sanitized_result"]
    )


def test_a_provider_timeout_is_timed_out_with_the_handle_unknown(world: World) -> None:
    world.adapter = Scripted(submit_error=Timeout(adapter_id="scripted"))
    operation = world.operation(world.add())
    world.run(operation)
    row = world.row(operation)
    assert row["execution_status"] == "TIMED_OUT" and row["failure_reason"] == "PROVIDER_TIMEOUT"
    assert row["completed_at"] is not None


# --- polling ---------------------------------------------------------------------------------


def test_a_few_failed_status_reads_are_tolerated_then_the_run_continues(world: World) -> None:
    world.adapter = Scripted(status_errors_first=2, statuses=("COMPLETED",))
    operation = world.operation(world.add())
    assert world.run(operation) == "SUCCEEDED"
    assert world.adapter.submit_attempts == 1


def test_a_status_that_keeps_timing_out_leaves_the_outcome_unknown_and_flagged(
    world: World,
) -> None:
    world.adapter = Scripted(status_error=Timeout(adapter_id="scripted"))
    operation = world.operation(world.add())
    assert world.run(operation) == "TIMED_OUT:PROVIDER_TIMEOUT"
    row = world.row(operation)
    assert row["external_execution_id"] == "scripted-1"  # the handle survives for reconciliation
    assert row["sanitized_result"]["reconciliation_required"] is True
    assert world.adapter.submit_attempts == 1 and world.adapter.status_calls == 3


def test_the_deadline_passing_while_the_provider_is_still_working_is_timed_out(
    world: World,
) -> None:
    world.adapter = Scripted(statuses=("IN_PROGRESS",))
    operation = world.operation(world.add(), deadline_in=2.5)

    outcome = world.run(operation, sleep=lambda _: time.sleep(1.2))

    assert outcome == "TIMED_OUT:DEADLINE_EXCEEDED"
    row = world.row(operation)
    assert row["sanitized_result"]["reconciliation_required"] is True
    assert row["external_execution_id"] == "scripted-1" and world.adapter.submit_attempts == 1


def test_a_row_whose_deadline_passed_before_anyone_claimed_it_never_starts(world: World) -> None:
    operation = world.operation(world.add(), deadline_in=-5)

    assert world.run(operation) == "TIMED_OUT:EXPIRED_BEFORE_START"

    row = world.row(operation)
    assert row["started_at"] is None and row["completed_at"] is not None
    assert row["sanitized_result"]["reconciliation_required"] is False
    assert world.audit(operation, "SERVICE_OPERATION_STARTED") == []  # it never started
    assert world.adapter.info_calls == world.adapter.list_calls == 0
    assert_nothing_was_sent(world)


# --- duplicate delivery and races ------------------------------------------------------------


def test_a_second_delivery_after_completion_does_nothing(world: World) -> None:
    operation = world.operation(world.add())
    assert world.run(operation) == "SUCCEEDED"
    assert world.run(operation) == "not_claimable"
    assert len(world.adapter.submitted) == 1


@pytest.mark.parametrize("status", ["RUNNING", "AWAITING_APPROVAL", "CANCELLED", "REJECTED"])
def test_only_pending_and_approved_rows_can_be_claimed(world: World, status: str) -> None:
    operation = world.operation(world.add(), status=status)
    assert world.run(operation) == "not_claimable"
    assert world.row(operation)["execution_status"] == status
    assert_nothing_was_sent(world)


def test_a_cancelled_operation_is_never_executed(world: World) -> None:
    operation = world.operation(world.add(), status="CANCELLED")
    assert world.run(operation) == "not_claimable"
    assert_nothing_was_sent(world)


def test_two_workers_racing_one_row_produce_one_claim_and_one_invocation(world: World) -> None:
    operation = world.operation(world.add())
    workers = 8
    barrier = threading.Barrier(workers)
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def work() -> None:
        try:
            barrier.wait(timeout=10)
            outcomes.append(world.run(operation))
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=work) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, errors
    assert outcomes.count("SUCCEEDED") == 1, outcomes
    assert set(outcomes) <= {"SUCCEEDED", "lost_claim", "not_claimable"}
    assert world.adapter.submit_attempts == 1 and len(world.adapter.submitted) == 1


def test_a_worker_that_lost_the_row_to_the_reaper_does_not_resurrect_it(world: World) -> None:
    operation = world.operation(world.add())

    def reaper_wins() -> None:
        world.set(
            "UPDATE operations SET deadline_at = now() - interval '1 minute' WHERE id = :i",
            i=operation,
        )
        execute.reap(world.engine, enqueue=lambda _: None)

    world.adapter = Scripted(statuses=("COMPLETED",), on_status=reaper_wins)
    assert world.run(operation) == "superseded"
    row = world.row(operation)
    assert row["execution_status"] == "TIMED_OUT" and row["failure_reason"] == "DEADLINE_EXCEEDED"
    assert row["observed_state_after"] is None  # no success was written over it


# --- re-authorisation and re-validation: every refusal sends nothing -------------------------


def refused(world: World, operation: uuid.UUID, reason: str) -> None:
    assert world.run(operation) == f"FAILED:{reason}"
    row = world.row(operation)
    assert row["execution_status"] == "FAILED" and row["failure_reason"] == reason
    assert row["started_at"] is not None and row["external_execution_id"] is None
    assert row["sanitized_result"]["reconciliation_required"] is False
    assert_nothing_was_sent(world)
    [denial] = world.audit(operation, "SERVICE_OPERATION_DENIED")
    assert denial["outcome"] == "DENIED" and denial["sanitized_metadata"]["reason"] == reason
    assert denial["sanitized_metadata"]["phase"] == "worker"
    assert len(world.audit(operation, "SERVICE_OPERATION_COMPLETED")) == 1


def test_permission_revoked_while_the_operation_waited_is_refused(world: World) -> None:
    operation = world.operation(world.add())
    revoke_all(world.engine, world.user)  # the queue held it; the grant did not survive
    refused(world, operation, "PERMISSION_REVOKED")


def test_a_grant_narrowed_away_from_the_environment_is_refused(world: World) -> None:
    operation = world.operation(world.add(environment="production"))
    revoke_all(world.engine, world.user)
    grant(
        world.engine,
        world.user,
        "DEVOPS_ENGINEER",
        '{"environment":["staging"],"cloud_account":["*"],"server":["*"]}',
    )
    refused(world, operation, "PERMISSION_REVOKED")


def test_a_role_without_the_action_permission_is_refused(world: World) -> None:
    operation = world.operation(world.add())
    revoke_all(world.engine, world.user)
    grant(world.engine, world.user, "VIEWER", FULL_SCOPE)
    refused(world, operation, "PERMISSION_REVOKED")


def test_a_disabled_requester_is_refused(world: World) -> None:
    operation = world.operation(world.add())
    world.set("UPDATE users SET status = 'DISABLED' WHERE id = :i", i=world.user)
    try:
        refused(world, operation, "PERMISSION_REVOKED")
    finally:
        world.set("UPDATE users SET status = 'ACTIVE' WHERE id = :i", i=world.user)


def test_a_disabled_cloud_account_is_refused(world: World) -> None:
    operation = world.operation(world.add())
    world.set("UPDATE cloud_accounts SET status = 'DISABLED' WHERE id = :i", i=world.account)
    try:
        refused(world, operation, "ACCOUNT_DISABLED")
    finally:
        world.set("UPDATE cloud_accounts SET status = 'ACTIVE' WHERE id = :i", i=world.account)


@pytest.mark.parametrize(
    ("snapshot", "now"),
    [("staging", "production"), ("production", "staging")],
)
def test_a_server_relabelled_since_the_request_is_refused_in_either_direction(
    world: World, snapshot: str, now: str
) -> None:
    target = world.add(environment=snapshot)
    operation = world.operation(target)
    world.set("UPDATE servers SET environment = :e WHERE id = :i", e=now, i=target.server)
    refused(world, operation, "TARGET_VALIDATION_FAILED")


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE servers SET status = 'UNREACHABLE' WHERE id = :s",
        "UPDATE servers SET status = 'RETIRED', deleted_at = now() WHERE id = :s",
        "UPDATE services SET deleted_at = now() WHERE id = :v",
    ],
)
def test_a_target_that_is_gone_or_not_managed_is_refused(world: World, change: str) -> None:
    target = world.add()
    operation = world.operation(target)
    world.set(change, s=target.server, v=target.service)
    refused(world, operation, "TARGET_VALIDATION_FAILED")


def test_a_service_moved_to_another_server_is_refused(world: World) -> None:
    target, other = world.add(), world.add(unit="other.service")
    operation = world.operation(target)
    world.set("UPDATE services SET server_id = :o WHERE id = :v", o=other.server, v=target.service)
    refused(world, operation, "TARGET_VALIDATION_FAILED")


def test_a_policy_that_no_longer_allows_the_action_is_refused(world: World) -> None:
    target = world.add()
    operation = world.operation(target)
    world.set("UPDATE services SET action_policy_id = NULL WHERE id = :v", v=target.service)
    refused(world, operation, "ACTION_NOT_ALLOWED")


@pytest.mark.parametrize(
    ("environment", "action", "policy"),
    [
        ("production", "stop", ALL),  # a production stop needs approval whatever the rows say
        ("production", "restart", "restart-only"),  # the seeded production approval policy
    ],
)
def test_an_operation_that_needs_approval_and_has_none_is_refused_by_the_worker(
    world: World, environment: str, action: str, policy: str
) -> None:
    """Mandatory rule 3: the worker re-checks, so a row made directly cannot bypass the gate."""
    operation = world.operation(world.add(environment=environment, policy=policy), action=action)
    refused(world, operation, "APPROVAL_REQUIRED")


def test_the_same_row_with_a_recorded_approval_proceeds(world: World) -> None:
    world.adapter = Scripted(before="ACTIVE", after=("INACTIVE",))
    target = world.add(environment="production")
    operation = world.operation(target, action="stop", status="APPROVED", approval="APPROVED")
    assert world.run(operation) == "SUCCEEDED"


def test_no_adapter_for_the_provider_is_refused(world: World) -> None:
    operation = world.operation(world.add())
    world.monkeypatch.delitem(registry._FACTORIES, "ON_PREM")
    refused(world, operation, "NOT_CONFIGURED")


def test_demonstration_mode_is_refused(world: World) -> None:
    operation = world.operation(world.add())
    world.monkeypatch.setenv("DCP_DEMO_MODE", "true")
    get_settings.cache_clear()
    refused(world, operation, "NOT_CONFIGURED")


def test_a_different_adapter_than_the_one_that_accepted_the_request_is_refused(
    world: World,
) -> None:
    operation = world.operation(world.add(), provider="somewhere_else")
    refused(world, operation, "TARGET_VALIDATION_FAILED")


class NoRestart(Scripted):
    """This provider can start and stop, and says plainly that it cannot restart."""

    def capabilities(self) -> CapabilitySet:
        base = super().capabilities()
        actions = dict(base.actions)
        actions[ServiceAction.RESTART] = Support.UNSUPPORTED
        return CapabilitySet(
            base.inventory_sync,
            base.connectivity_check,
            actions,
            base.async_handle,
            base.concurrency,
            base.latency,
        )


def test_an_action_the_provider_cannot_do_is_refused_as_unsupported(world: World) -> None:
    world.adapter = NoRestart()
    operation = world.operation(world.add())
    assert world.run(operation) == "FAILED:UNSUPPORTED_OPERATION"
    assert_nothing_was_sent(world)


def test_a_host_the_provider_reports_as_someone_else_is_refused(world: World) -> None:
    world.adapter = Scripted(info_resource="i-someoneelse")
    operation = world.operation(world.add())
    refused_reason = "TARGET_VALIDATION_FAILED"
    assert world.run(operation) == f"FAILED:{refused_reason}"
    assert_nothing_was_sent(world)
    assert world.row(operation)["sanitized_result"]["reason_code"] == "identity_mismatch"


def test_a_unit_that_is_not_on_the_host_is_refused(world: World) -> None:
    world.adapter = Scripted(unit="other.service")
    operation = world.operation(world.add())
    assert world.run(operation) == "FAILED:TARGET_VALIDATION_FAILED"
    assert world.row(operation)["sanitized_result"]["reason_code"] == "unit_not_on_host"
    assert_nothing_was_sent(world)


def test_a_provider_that_cannot_answer_the_pre_flight_read_is_refused_not_retried(
    world: World,
) -> None:
    world.adapter = Scripted(list_error=Timeout(adapter_id="scripted"))
    operation = world.operation(world.add())
    assert world.run(operation) == "FAILED:TARGET_VALIDATION_FAILED"
    row = world.row(operation)
    assert row["sanitized_result"]["reason_code"] == "timeout"
    assert row["sanitized_result"]["reconciliation_required"] is False  # nothing was sent
    assert_nothing_was_sent(world)


# --- the reaper and the healer ---------------------------------------------------------------


def test_a_worker_killed_mid_operation_is_timed_out_flagged_and_never_re_invoked(
    world: World,
) -> None:
    operation = world.operation(world.add(), status="RUNNING", handle="scripted-9", deadline_in=-5)

    summary = execute.reap(world.engine, enqueue=lambda _: None)

    assert summary["timed_out"] >= 1
    row = world.row(operation)
    assert row["execution_status"] == "TIMED_OUT" and row["failure_reason"] == "DEADLINE_EXCEEDED"
    assert row["sanitized_result"]["reconciliation_required"] is True
    assert row["external_execution_id"] == "scripted-9"  # kept for a person
    assert row["completed_at"] is not None
    # and a late redelivery of the task loses the claim: the provider is not touched.
    assert world.run(operation) == "not_claimable"
    assert_nothing_was_sent(world)
    assert world.adapter.info_calls == world.adapter.list_calls == 0


@pytest.mark.parametrize("status", ["PENDING", "APPROVED"])
def test_an_unclaimed_row_past_its_deadline_is_expired_without_a_provider(
    world: World, status: str
) -> None:
    operation = world.operation(world.add(), status=status, deadline_in=-5)
    execute.reap(world.engine, enqueue=lambda _: None)
    row = world.row(operation)
    assert row["execution_status"] == "TIMED_OUT"
    assert row["failure_reason"] == "EXPIRED_BEFORE_START" and row["started_at"] is None
    assert_nothing_was_sent(world)


def test_the_reaper_leaves_live_and_finished_rows_alone(world: World) -> None:
    target = world.add()
    running = world.operation(target, status="RUNNING", deadline_in=300)
    done = world.operation(world.add(), status="SUCCEEDED", deadline_in=-300)
    cancelled = world.operation(world.add(), status="CANCELLED", deadline_in=-300)
    execute.reap(world.engine, enqueue=lambda _: None)
    assert world.row(running)["execution_status"] == "RUNNING"
    assert world.row(done)["execution_status"] == "SUCCEEDED"
    assert world.row(cancelled)["execution_status"] == "CANCELLED"


def test_a_second_reap_finds_nothing_more_to_do(world: World) -> None:
    world.operation(world.add(), status="RUNNING", deadline_in=-5)
    execute.reap(world.engine, enqueue=lambda _: None)
    again = execute.reap(world.engine, enqueue=lambda _: None)
    assert again["timed_out"] == 0 and again["expired_before_start"] == 0


def test_the_healer_requeues_an_unclaimed_row_whose_message_was_lost(world: World) -> None:
    lost = world.operation(world.add(), status="PENDING")  # created a minute ago by the helper
    approved = world.operation(world.add(), status="APPROVED", approval="APPROVED")
    young = world.operation(world.add(), status="PENDING")
    world.set("UPDATE operations SET created_at = now() WHERE id = :i", i=young)
    running = world.operation(world.add(), status="RUNNING")
    sent: list[uuid.UUID] = []

    execute.reap(world.engine, enqueue=sent.append)

    assert lost in sent and approved in sent
    assert young not in sent and running not in sent


def test_a_requeued_row_that_is_delivered_twice_still_runs_once(world: World) -> None:
    operation = world.operation(world.add())
    assert world.run(operation) == "SUCCEEDED"
    assert world.run(operation) == "not_claimable"
    assert len(world.adapter.submitted) == 1


# --- the task wrappers -----------------------------------------------------------------------


def _tasks(redis: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Import the task module with the *test* Redis as the Celery broker (see test_alert_schema)."""
    monkeypatch.setenv("DCP_REDIS_URL", redis)
    get_settings.cache_clear()
    from app.workers.tasks import operations

    return operations


def test_the_executor_takes_an_id_and_nothing_else(
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SECURITY.md §22: no command, URL, credential or domain object crosses the broker."""
    tasks = _tasks(redis_url, monkeypatch)
    parameters = inspect.signature(tasks.execute_operation).parameters
    assert list(parameters) == ["operation_id"]
    assert parameters["operation_id"].annotation in (str, "str")
    assert list(inspect.signature(tasks.reap_operations).parameters) == []


def test_the_wrapper_refuses_a_malformed_id_without_touching_the_database(
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = _tasks(redis_url, monkeypatch)
    for bad in ("not-a-uuid", "", "'; DROP TABLE operations; --", "systemctl restart sshd"):
        assert tasks.execute_operation(bad) == {"outcome": "bad_id"}


def test_the_tasks_are_routed_and_scheduled_where_the_design_says(
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = _tasks(redis_url, monkeypatch)
    app = tasks.celery_app
    assert tasks.execute_operation.queue == "operations"
    assert (
        tasks.reap_operations.queue == "maintenance"
    )  # a saturated operations queue cannot stop it
    assert app.conf.accept_content == ["json"] and app.conf.task_serializer == "json"
    assert app.conf.task_acks_late is True
    assert app.conf.broker_transport_options["visibility_timeout"] > tasks.HARD_LIMIT_SECONDS
    assert tasks.HARD_LIMIT_SECONDS > 3_600  # above the largest policy deadline
    entry = app.conf.beat_schedule["reap-operations"]
    assert entry["task"] == "dcp.operations.reap" and entry["options"]["queue"] == "maintenance"
    assert entry["schedule"] <= 30
    assert "dcp.operations.execute" in app.tasks and "dcp.operations.reap" in app.tasks
