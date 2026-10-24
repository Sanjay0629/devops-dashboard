"""Does a deployment need an approval? (``OPEN-133``). Pure, and it fails closed.

The same function answers the request and the worker's re-check, so these cases are both. Until
M5.4 "required" means "refused": nothing can decide an approval.
"""

from __future__ import annotations

import pytest

from app.deployments.approval import APPROVAL_WORKFLOW_UNAVAILABLE, approval_required


@pytest.mark.parametrize("environment", ["development", "staging", "other"])
def test_a_non_production_target_with_no_policy_needs_none(environment: str) -> None:
    assert approval_required(environment=environment, policy_linked=False) is False


@pytest.mark.parametrize("environment", ["development", "staging", "other", "production"])
def test_a_linked_policy_always_requires_one(environment: str) -> None:
    """Whatever the policy row now says: it is not read here, so a missing one is also required."""
    assert approval_required(environment=environment, policy_linked=True) is True


def test_production_requires_one_even_with_no_policy_linked() -> None:
    """The API refuses to register such a target; a direct ``UPDATE`` could still make one."""
    assert approval_required(environment="production", policy_linked=False) is True


@pytest.mark.parametrize("environment", ["", "prod", "PRODUCTION", "*", "unknown", "Staging"])
def test_an_environment_that_is_not_a_known_non_production_name_requires_one(
    environment: str,
) -> None:
    assert approval_required(environment=environment, policy_linked=False) is True


def test_the_reason_is_a_closed_code() -> None:
    assert APPROVAL_WORKFLOW_UNAVAILABLE == "approval_workflow_unavailable"
