"""The approval evaluator and the closed code sets, pure (``OPEN-68``, ``OPEN-71``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.operations import codes, evaluator, states
from app.operations.evaluator import ApprovalPolicyView, approval_required

ENVIRONMENTS = ("development", "staging", "production", "other")
REQUESTABLE = codes.REQUESTABLE_ACTIONS


def _policy(env: str = "production", applies_to: str = "SERVICE_OPERATION") -> ApprovalPolicyView:
    return ApprovalPolicyView(applies_to=applies_to, environment=env)


@pytest.mark.parametrize("environment", ENVIRONMENTS)
@pytest.mark.parametrize("action", REQUESTABLE)
def test_no_linked_policy_means_no_approval_except_a_production_stop(
    environment: str, action: str
) -> None:
    decision = approval_required(
        environment=environment, action=action, policy_linked=False, policy=None
    )
    if (environment, action) == ("production", "stop"):
        assert decision == evaluator.Decision(True, evaluator.PRODUCTION_STOP)
    else:
        assert decision == evaluator.Decision(False, evaluator.NOT_REQUIRED)


@pytest.mark.parametrize("action", REQUESTABLE)
def test_a_production_policy_requires_approval_for_production_only(action: str) -> None:
    for environment in ENVIRONMENTS:
        decision = approval_required(
            environment=environment, action=action, policy_linked=True, policy=_policy()
        )
        expected = environment == "production"
        assert decision.required is expected, (environment, action)


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_a_wildcard_policy_covers_every_environment(environment: str) -> None:
    decision = approval_required(
        environment=environment, action="restart", policy_linked=True, policy=_policy("*")
    )
    assert decision == evaluator.Decision(True, evaluator.BY_POLICY)


def test_a_linked_policy_that_cannot_be_read_is_required() -> None:
    decision = approval_required(
        environment="staging", action="restart", policy_linked=True, policy=None
    )
    assert decision == evaluator.Decision(True, evaluator.POLICY_UNREADABLE)


def test_a_deployment_policy_linked_to_a_service_is_a_misconfiguration_and_required() -> None:
    decision = approval_required(
        environment="staging",
        action="restart",
        policy_linked=True,
        policy=_policy("staging", applies_to="DEPLOYMENT"),
    )
    assert decision == evaluator.Decision(True, evaluator.POLICY_UNREADABLE)


@pytest.mark.parametrize("environment", ["", "prod", "PRODUCTION", "*", "unknown"])
def test_an_environment_the_code_does_not_know_is_required(environment: str) -> None:
    decision = approval_required(
        environment=environment, action="start", policy_linked=False, policy=None
    )
    assert decision == evaluator.Decision(True, evaluator.UNKNOWN_ENVIRONMENT)


def test_a_production_stop_needs_approval_even_when_the_policy_says_otherwise() -> None:
    decision = approval_required(
        environment="production",
        action="stop",
        policy_linked=True,
        policy=_policy("staging"),  # a policy for another environment
    )
    assert decision.required and decision.basis == evaluator.PRODUCTION_STOP


@pytest.mark.parametrize(
    ("environment", "needed", "expected"),
    [
        ("production", False, True),
        ("staging", True, True),
        ("staging", False, False),
        ("development", False, False),
    ],
)
def test_a_reason_is_required_for_production_and_wherever_approval_is(
    environment: str, needed: bool, expected: bool
) -> None:
    assert evaluator.reason_required(environment=environment, approval_needed=needed) is expected


# --- codes ------------------------------------------------------------------------------------


def test_only_start_stop_restart_are_requestable_and_status_logs_stay_in_the_enum() -> None:
    assert codes.REQUESTABLE_ACTIONS == ("start", "stop", "restart")
    assert set(codes.REQUESTABLE_ACTIONS) < set(codes.ACTION_TYPES)
    assert set(codes.ACTION_TYPES) == {"start", "stop", "restart", "status", "logs_tail"}


def test_every_failure_reason_ends_in_one_terminal_status() -> None:
    assert len(set(codes.FAILURE_REASONS)) == len(codes.FAILURE_REASONS)
    for reason in codes.FAILURE_REASONS:
        assert codes.terminal_status_for(reason) in {states.FAILED, states.TIMED_OUT}
    assert {r for r in codes.FAILURE_REASONS if codes.terminal_status_for(r) == "TIMED_OUT"} == {
        "PROVIDER_TIMEOUT",
        "DEADLINE_EXCEEDED",
        "EXPIRED_BEFORE_START",
    }


def test_an_unknown_reason_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown failure reason"):
        codes.terminal_status_for("SOMETHING_THE_PROVIDER_SAID")


def test_reconciliation_marks_exactly_the_outcomes_where_the_provider_may_have_acted() -> None:
    assert set(codes.FAILURE_REASONS) > codes.RECONCILIATION_REASONS
    assert {r for r in codes.FAILURE_REASONS if codes.needs_reconciliation(r)} == {
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_PARTIAL_FAILURE",
        "VERIFICATION_UNAVAILABLE",
        "RESUME_REFUSED",
        "DEADLINE_EXCEEDED",
    }
    # Nothing was sent, so there is nothing to reconcile.
    for reason in (
        codes.PERMISSION_REVOKED,
        codes.APPROVAL_REQUIRED,
        codes.EXPIRED_BEFORE_START,
        codes.PROVIDER_THROTTLED,
        codes.VERIFICATION_MISMATCH,
    ):
        assert not codes.needs_reconciliation(reason)


def test_the_contract_documents_every_failure_reason() -> None:
    contract = (Path(__file__).parents[3] / "docs" / "API_CONTRACTS.md").read_text(encoding="utf-8")
    missing = [reason for reason in codes.FAILURE_REASONS if f"`{reason}`" not in contract]
    assert not missing, missing
