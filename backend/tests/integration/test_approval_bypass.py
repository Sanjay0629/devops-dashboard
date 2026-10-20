"""The approval bypass suite (M4.5, ``OPEN-99``; mandatory rule 3, docs/TESTING.md §7).

The adversarial question: with the workflow built, can an operation that needs an approval still
run without a real one? Every case here builds a row the way an attacker (or a bug, or a stale
queue) could -- directly in SQL, bypassing the API -- and drives the worker. Each is refused with
``failure_reason = APPROVAL_REQUIRED``, a closed ``reason_code``, **zero provider calls**, and an
audited denial; a positive control proves the same setup runs when the evidence is real.

The scripted provider is a fake, so this proves what DCP decides, not that any cloud works.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import text

from app.operations import execute
from tests.integration.inventory_helpers import STAGING_SCOPE, grant, revoke_all
from tests.integration.test_operations_worker import (  # noqa: F401 -- fixture used by name
    World,
    assert_nothing_was_sent,
    no_sleep,
    world,
)
from tests.operations.fakes import Scripted

GATED = "restart-only"  # the seeded production approval policy
POLICY = "production-service-operations"


def gated_row(world: World, *, status: str = "APPROVED", approval: str = "APPROVED") -> uuid.UUID:  # noqa: F811
    """A production restart under the seeded approval policy, claimable by the worker."""
    target = world.add(environment="production", policy=GATED)
    return world.operation(target, status=status, approval=approval)


def refused_for(world: World, operation: uuid.UUID, code: str) -> None:  # noqa: F811
    assert world.run(operation) == "FAILED:APPROVAL_REQUIRED"
    row = world.row(operation)
    assert row["execution_status"] == "FAILED" and row["failure_reason"] == "APPROVAL_REQUIRED"
    assert row["sanitized_result"]["reason_code"] == code
    assert row["external_execution_id"] is None
    assert row["sanitized_result"]["reconciliation_required"] is False
    assert_nothing_was_sent(world)  # THE assertion: no provider call of any kind to change state
    assert world.adapter.info_calls == world.adapter.list_calls == 0  # not even a pre-flight read
    [denial] = world.audit(operation, "SERVICE_OPERATION_DENIED")
    assert denial["outcome"] == "DENIED"
    assert denial["sanitized_metadata"]["reason"] == "APPROVAL_REQUIRED"
    assert denial["sanitized_metadata"]["reason_code"] == code
    assert denial["sanitized_metadata"]["phase"] == "worker"


@contextmanager
def policy_changed(world: World, **columns: Any) -> Iterator[None]:  # noqa: F811
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    with world.engine.begin() as connection:
        original = connection.execute(
            text(f"SELECT {', '.join(columns)} FROM approval_policies WHERE name = :n"),  # noqa: S608
            {"n": POLICY},
        ).one()
        connection.execute(
            text(f"UPDATE approval_policies SET {assignments} WHERE name = :n"),  # noqa: S608
            {"n": POLICY, **columns},
        )
    try:
        yield
    finally:
        with world.engine.begin() as connection:
            connection.execute(
                text(f"UPDATE approval_policies SET {assignments} WHERE name = :n"),  # noqa: S608
                {"n": POLICY, **dict(zip(columns, original, strict=True))},
            )


# --- the positive control ---------------------------------------------------------------------


def test_the_same_setup_with_a_real_approval_runs_exactly_once(world: World) -> None:  # noqa: F811
    operation = gated_row(world)
    world.approve(operation)
    assert world.run(operation) == "SUCCEEDED"
    assert len(world.adapter.submitted) == 1
    assert world.row(operation)["execution_status"] == "SUCCEEDED"


# --- evidence that is not an approval ---------------------------------------------------------


def test_a_row_set_to_approved_in_sql_with_no_approvals_row_is_refused(world: World) -> None:  # noqa: F811
    """The flag alone proves nothing: this is the test the old flag check would have failed."""
    refused_for(world, gated_row(world), "approval_missing")


def test_a_production_stop_marked_approved_in_sql_is_refused(world: World) -> None:  # noqa: F811
    operation = world.operation(
        world.add(environment="production", policy="test-ops-all"),
        action="stop",
        status="APPROVED",
        approval="APPROVED",
    )
    refused_for(world, operation, "approval_missing")


def test_a_pending_row_that_skipped_the_queue_is_refused(world: World) -> None:  # noqa: F811
    refused_for(
        world, gated_row(world, status="PENDING", approval="NOT_REQUIRED"), "approval_missing"
    )


@pytest.mark.parametrize(
    ("decision", "code"),
    [
        ("REJECTED", "approval_not_approved"),
        ("PENDING", "approval_not_approved"),
        ("EXPIRED", "approval_expired"),
    ],
)
def test_an_approvals_row_that_is_not_an_approval_is_refused(
    world: World,  # noqa: F811
    decision: str,
    code: str,
) -> None:
    operation = gated_row(world)
    world.approve(operation, decision=decision)
    refused_for(world, operation, code)


def test_an_approval_that_belongs_to_another_operation_is_refused(world: World) -> None:  # noqa: F811
    operation = gated_row(world)
    elsewhere = gated_row(world)
    world.approve(operation, for_operation=elsewhere)
    refused_for(world, operation, "approval_missing")


def test_an_approval_recorded_for_another_requester_is_refused(world: World) -> None:  # noqa: F811
    operation = gated_row(world)
    world.approve(operation, requested_by=world.approve(gated_row(world)))
    refused_for(world, operation, "approval_missing")


def test_a_decision_made_after_the_window_closed_is_refused(world: World) -> None:  # noqa: F811
    operation = gated_row(world)
    world.approve(operation, window=600, decided_offset=601)
    refused_for(world, operation, "approval_expired")


def test_a_decision_at_the_last_instant_of_the_window_is_accepted(world: World) -> None:  # noqa: F811
    operation = gated_row(world)
    world.approve(operation, window=600, decided_offset=600)
    assert world.run(operation) == "SUCCEEDED"


# --- separation of duties ---------------------------------------------------------------------


def test_a_row_approved_by_its_own_requester_is_refused_under_a_no_self_approval_policy(
    world: World,  # noqa: F811
) -> None:
    operation = gated_row(world)
    grant(world.engine, world.user, "APPROVER")  # the requester holds both roles
    world.approve(operation, approver=world.user)
    refused_for(world, operation, "self_approval")


def test_the_same_row_runs_where_the_policy_explicitly_allows_self_approval(
    world: World,  # noqa: F811
) -> None:
    operation = gated_row(world)
    grant(world.engine, world.user, "APPROVER")
    world.approve(operation, approver=world.user)
    with policy_changed(world, allow_self_approval=True):
        assert world.run(operation) == "SUCCEEDED"


# --- an approver who lost standing after deciding ---------------------------------------------


def test_an_approver_whose_role_was_revoked_after_deciding_is_refused(world: World) -> None:  # noqa: F811
    operation = gated_row(world)
    approver = world.approve(operation)
    revoke_all(world.engine, approver)
    refused_for(world, operation, "approver_revoked")


def test_an_approver_whose_scope_no_longer_reaches_production_is_refused(world: World) -> None:  # noqa: F811
    operation = gated_row(world)
    approver = world.approve(operation)
    revoke_all(world.engine, approver)
    grant(world.engine, approver, "APPROVER", STAGING_SCOPE)  # still an approver, not here
    refused_for(world, operation, "approver_revoked")


def test_an_approver_who_no_longer_holds_the_policys_role_is_refused(world: World) -> None:  # noqa: F811
    operation = gated_row(world)
    world.approve(operation)
    with policy_changed(world, required_role="DEVOPS_ENGINEER"):
        refused_for(world, operation, "approver_revoked")


def test_a_disabled_approver_is_refused(world: World) -> None:  # noqa: F811
    operation = gated_row(world)
    approver = world.approve(operation)
    world.set("UPDATE users SET status = 'DISABLED' WHERE id = :u", u=approver)
    refused_for(world, operation, "approver_revoked")


def test_an_approver_whose_role_assignment_expired_is_refused(world: World) -> None:  # noqa: F811
    operation = gated_row(world)
    approver = world.approve(operation)
    world.set(
        "UPDATE user_roles SET expires_at = now() - interval '1 second' WHERE user_id = :u",
        u=approver,
    )
    refused_for(world, operation, "approver_revoked")


# --- the policy moved while the request waited ------------------------------------------------


def test_a_row_that_needed_no_approval_when_requested_but_is_now_governed_is_refused(
    world: World,  # noqa: F811
) -> None:
    """Requested while nothing gated it; a policy was linked before a worker claimed it."""
    target = world.add(environment="production", policy="test-ops-all")  # no approval policy
    operation = world.operation(target)  # PENDING, NOT_REQUIRED
    world.set(
        "UPDATE service_action_policies SET approval_policy_id ="
        " (SELECT id FROM approval_policies WHERE name = :p) WHERE name = 'test-ops-all'",
        p=POLICY,
    )
    try:
        refused_for(world, operation, "approval_missing")
    finally:
        world.set(
            "UPDATE service_action_policies SET approval_policy_id = NULL"
            " WHERE name = 'test-ops-all'"
        )


def test_a_valid_approval_still_runs_after_the_policy_stopped_applying(world: World) -> None:  # noqa: F811
    operation = gated_row(world)
    world.approve(operation)
    with policy_changed(world, environment="staging"):  # no longer governs production
        assert world.run(operation) == "SUCCEEDED"


def test_the_flag_alone_is_refused_even_when_the_policy_stopped_applying(world: World) -> None:  # noqa: F811
    """A row that *claims* an approval is checked whether or not a policy still demands one."""
    operation = gated_row(world)
    with policy_changed(world, environment="staging"):
        refused_for(world, operation, "approval_missing")


def test_a_policy_that_now_needs_more_than_one_approval_is_refused(world: World) -> None:  # noqa: F811
    operation = gated_row(world)
    world.approve(operation)
    with policy_changed(world, required_approvals=2):
        refused_for(world, operation, "approval_policy_unsupported")


def test_an_unreadable_policy_falls_back_to_the_strict_defaults_a_real_approval_satisfies(
    world: World,  # noqa: F811
) -> None:
    operation = gated_row(world)
    world.approve(operation)
    world.set("UPDATE approval_policies SET applies_to = 'DEPLOYMENT' WHERE name = :n", n=POLICY)
    try:
        # applies_to is not SERVICE_OPERATION: the evaluator answers "required" on the doubt and
        # the strict defaults apply -- and a real approval by a real approver still satisfies them.
        assert world.run(operation) == "SUCCEEDED"
    finally:
        world.set(
            "UPDATE approval_policies SET applies_to = 'SERVICE_OPERATION' WHERE name = :n",
            n=POLICY,
        )


def test_an_unreadable_policy_with_no_approval_is_refused(world: World) -> None:  # noqa: F811
    operation = gated_row(world)
    world.set("UPDATE approval_policies SET applies_to = 'DEPLOYMENT' WHERE name = :n", n=POLICY)
    try:
        refused_for(world, operation, "approval_missing")
    finally:
        world.set(
            "UPDATE approval_policies SET applies_to = 'SERVICE_OPERATION' WHERE name = :n",
            n=POLICY,
        )


# --- the resumer runs the same check ----------------------------------------------------------


def _running_gated(world: World, *, evidence: bool) -> uuid.UUID:  # noqa: F811
    world.adapter = Scripted(before="ACTIVE", after=("ACTIVE",), statuses=("COMPLETED",))
    target = world.add(environment="production", policy=GATED)
    operation = world.operation(target, status="RUNNING", approval="APPROVED", handle="cmd-1")
    if evidence:
        world.approve(operation)
    world.set(
        "UPDATE operations SET started_at = now() - interval '10 minutes',"
        " heartbeat_at = now() - interval '10 minutes', observed_state_before = 'INACTIVE'"
        " WHERE id = :i",
        i=operation,
    )
    return operation


def test_a_resumer_refuses_to_follow_a_command_whose_approval_cannot_be_shown(world: World) -> None:  # noqa: F811
    operation = _running_gated(world, evidence=False)
    assert execute.resume(world.engine, operation, sleep=no_sleep) == "FAILED:RESUME_REFUSED"
    row = world.row(operation)
    assert row["failure_reason"] == "RESUME_REFUSED"
    assert row["sanitized_result"]["reason_code"] == "approval_missing"
    assert row["sanitized_result"]["reconciliation_required"] is True  # a command may be running
    assert world.adapter.submit_attempts == 0  # and it still never sends


def test_a_resumer_with_a_real_approval_polls_and_verifies(world: World) -> None:  # noqa: F811
    operation = _running_gated(world, evidence=True)
    assert execute.resume(world.engine, operation, sleep=no_sleep) == "SUCCEEDED"
    assert world.adapter.submit_attempts == 0


def test_a_resumer_refuses_when_the_approver_lost_standing(world: World) -> None:  # noqa: F811
    operation = _running_gated(world, evidence=False)
    approver = world.approve(operation)
    revoke_all(world.engine, approver)
    assert execute.resume(world.engine, operation, sleep=no_sleep) == "FAILED:RESUME_REFUSED"
    assert world.row(operation)["sanitized_result"]["reason_code"] == "approver_revoked"
