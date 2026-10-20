"""The three pure approval decisions as tables (``OPEN-95``, ``OPEN-98``, ``OPEN-99``).

Every cell is an explicit expectation, not the function's own logic restated: the production-stop
rule, the "a doubt is *required*" rule and the "doubt falls back to the strict defaults" rule are
written out by hand in :func:`_expected_required`.
"""

from __future__ import annotations

import inspect
import itertools
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.approvals import rules
from app.approvals.rules import (
    DEFAULT_TERMS,
    Decider,
    Evidence,
    PolicyRow,
    PolicyTerms,
    approval_valid_for_execution,
    may_decide,
    resolve_policy,
)
from app.authz.matcher import Grant, Target

ENVIRONMENTS = ("development", "staging", "production", "other")
ACTIONS = ("start", "stop", "restart")

REQUESTER = uuid.uuid4()
APPROVER = uuid.uuid4()
OPERATION = uuid.uuid4()
NOW = datetime(2026, 1, 15, 9, 30, tzinfo=UTC)


def _row(
    *,
    environment: str = "production",
    applies_to: str = "SERVICE_OPERATION",
    required_approvals: int = 1,
    allow_self: bool = False,
    role: str = "APPROVER",
    window: int = 600,
) -> PolicyRow:
    return PolicyRow(
        uuid.uuid4(), applies_to, environment, required_approvals, allow_self, role, window
    )


# --- resolve_policy ------------------------------------------------------------------------

#: name -> (policy_linked, row factory)
POLICIES: dict[str, tuple[bool, Callable[[], PolicyRow | None]]] = {
    "none": (False, lambda: None),
    "dangling": (True, lambda: None),
    "production": (True, lambda: _row(environment="production")),
    "staging": (True, lambda: _row(environment="staging")),
    "wildcard": (True, lambda: _row(environment="*", window=900, allow_self=True, role="OPS")),
    "deployment": (True, lambda: _row(applies_to="DEPLOYMENT", environment="*")),
    "two": (True, lambda: _row(environment="*", required_approvals=2)),
}


def _expected_required(environment: str, action: str, policy: str) -> bool:
    if action == "stop" and environment == "production":
        return True
    linked, factory = POLICIES[policy]
    if not linked:
        return False
    row = factory()
    if row is None or row.applies_to != "SERVICE_OPERATION":
        return True  # a linked policy we cannot use is a doubt, and a doubt is *required*
    return row.environment in (environment, "*")


@pytest.mark.parametrize(
    ("environment", "action", "policy"),
    list(itertools.product(ENVIRONMENTS, ACTIONS, POLICIES)),
)
def test_resolution_matrix(environment: str, action: str, policy: str) -> None:
    linked, factory = POLICIES[policy]
    row = factory()
    result = resolve_policy(
        environment=environment, action=action, policy_linked=linked, policy=row
    )
    assert result.required is _expected_required(environment, action, policy)
    if not result.required:
        assert result.terms is None
        assert not result.unsupported
        return
    applies = (
        row is not None
        and row.applies_to == "SERVICE_OPERATION"
        and row.environment in (environment, "*")
    )
    if row is not None and applies:
        assert result.terms == PolicyTerms(
            row.policy_id,
            row.required_approvals,
            row.allow_self_approval,
            row.required_role,
            row.approval_window_seconds,
        )
        assert result.unsupported is (row.required_approvals != 1)
    else:
        # A production stop with no usable policy, a dangling link, a policy for something
        # else: the strict built-in defaults, and never "unsupported".
        assert result.terms == DEFAULT_TERMS
        assert result.unsupported is False


def test_the_built_in_defaults_are_the_strict_ones() -> None:
    assert DEFAULT_TERMS.policy_id is None
    assert DEFAULT_TERMS.required_approvals == 1
    assert DEFAULT_TERMS.allow_self_approval is False
    assert DEFAULT_TERMS.required_role == "APPROVER"
    assert DEFAULT_TERMS.approval_window_seconds == 3_600


def test_an_unknown_environment_is_required_on_default_terms() -> None:
    result = resolve_policy(
        environment="qa", action="restart", policy_linked=True, policy=_row(environment="*")
    )
    assert result.required
    assert result.terms == DEFAULT_TERMS
    assert not result.unsupported


def test_a_policy_for_more_than_one_approval_is_flagged_never_clamped() -> None:
    result = resolve_policy(
        environment="production",
        action="restart",
        policy_linked=True,
        policy=_row(required_approvals=3),
    )
    assert result.required
    assert result.unsupported
    assert result.terms is not None
    assert result.terms.required_approvals == 3


def test_a_production_stop_under_a_matching_policy_uses_that_policys_terms() -> None:
    row = _row(window=120, allow_self=True)
    result = resolve_policy(environment="production", action="stop", policy_linked=True, policy=row)
    assert result.basis == "production_stop"
    assert result.terms is not None
    assert result.terms.approval_window_seconds == 120


# --- may_decide ----------------------------------------------------------------------------


def _grant(environment: str, *, effect: str = "ALLOW") -> Grant:
    return Grant("operation", "approve", effect, {"environment": [environment], "server": ["*"]})


PRODUCTION = Target(environment="production", cloud_account="acct", server="srv")
STAGING = Target(environment="staging", cloud_account="acct", server="srv")
STRICT = DEFAULT_TERMS
SELF_OK = replace(DEFAULT_TERMS, allow_self_approval=True)


@pytest.mark.parametrize("same_person", [False, True])
@pytest.mark.parametrize("policy_allows_self", [False, True])
@pytest.mark.parametrize("grants", ["production", "staging_only", "none", "denied"])
@pytest.mark.parametrize(
    "roles", [frozenset({"APPROVER"}), frozenset({"DEVOPS_ENGINEER"}), frozenset[str]()]
)
def test_may_decide_matrix(
    same_person: bool, policy_allows_self: bool, grants: str, roles: frozenset[str]
) -> None:
    held = {
        "production": [_grant("production")],
        "staging_only": [_grant("staging")],
        "none": [],
        "denied": [_grant("production"), _grant("production", effect="DENY")],
    }[grants]
    decider = REQUESTER if same_person else APPROVER
    verdict = may_decide(
        requester_id=REQUESTER,
        decider_id=decider,
        policy=SELF_OK if policy_allows_self else STRICT,
        grants=held,
        target=PRODUCTION,
        decider_roles=roles,
    )
    if grants in ("staging_only", "none", "denied"):
        assert verdict == rules.Verdict(False, rules.OUT_OF_SCOPE)  # scope is judged first
    elif same_person and not policy_allows_self:
        assert verdict == rules.Verdict(False, rules.SELF_APPROVAL_NOT_PERMITTED)
    elif "APPROVER" not in roles:
        assert verdict == rules.Verdict(False, rules.APPROVER_ROLE_REQUIRED)
    else:
        assert verdict == rules.Verdict(True, None)


def test_a_wildcard_environment_grant_never_reaches_production() -> None:
    verdict = may_decide(
        requester_id=REQUESTER,
        decider_id=APPROVER,
        policy=STRICT,
        grants=[Grant("operation", "approve", "ALLOW", {"environment": ["*"]})],
        target=PRODUCTION,
        decider_roles={"APPROVER"},
    )
    assert verdict == rules.Verdict(False, rules.OUT_OF_SCOPE)


def test_the_required_role_is_the_policys_not_always_approver() -> None:
    policy = replace(DEFAULT_TERMS, required_role="DEVOPS_ENGINEER")

    def decide(roles: set[str]) -> rules.Verdict:
        return may_decide(
            requester_id=REQUESTER,
            decider_id=APPROVER,
            policy=policy,
            grants=[_grant("production")],
            target=PRODUCTION,
            decider_roles=roles,
        )

    assert decide({"APPROVER"}).reason == rules.APPROVER_ROLE_REQUIRED
    assert decide({"DEVOPS_ENGINEER"}).allowed


# --- approval_valid_for_execution ----------------------------------------------------------


def _evidence(**overrides: object) -> Evidence:
    base = Evidence(
        operation_type="SERVICE_OPERATION",
        operation_id=OPERATION,
        requested_by=REQUESTER,
        decision="APPROVED",
        approved_by=APPROVER,
        decided_at=NOW,
        expires_at=NOW + timedelta(seconds=600),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _decider(**overrides: object) -> Decider:
    base = Decider(active=True, grants=[_grant("production")], roles={"APPROVER"})
    return replace(base, **overrides)  # type: ignore[arg-type]


def _check(
    evidence: Evidence | None,
    decider: Decider | None,
    policy: PolicyTerms = STRICT,
) -> str | None:
    return approval_valid_for_execution(
        operation_id=OPERATION,
        requested_by=REQUESTER,
        target=PRODUCTION,
        policy=policy,
        evidence=evidence,
        decider=decider,
    )


def test_a_real_approval_is_valid() -> None:
    assert _check(_evidence(), _decider()) is None


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (None, rules.APPROVAL_MISSING),
        (_evidence(operation_type="DEPLOYMENT"), rules.APPROVAL_MISSING),
        (_evidence(operation_id=uuid.uuid4()), rules.APPROVAL_MISSING),
        (_evidence(requested_by=uuid.uuid4()), rules.APPROVAL_MISSING),
        (
            _evidence(decision="PENDING", approved_by=None, decided_at=None),
            rules.APPROVAL_NOT_APPROVED,
        ),
        (_evidence(decision="REJECTED"), rules.APPROVAL_NOT_APPROVED),
        (_evidence(decision="EXPIRED", approved_by=None), rules.APPROVAL_EXPIRED),
        (_evidence(approved_by=None), rules.APPROVAL_NOT_APPROVED),
        (_evidence(decided_at=None), rules.APPROVAL_NOT_APPROVED),
        (_evidence(decided_at=NOW + timedelta(seconds=601)), rules.APPROVAL_EXPIRED),
    ],
)
def test_evidence_that_is_not_an_approval(evidence: Evidence | None, expected: str) -> None:
    assert _check(evidence, _decider()) == expected


def test_a_decision_at_the_last_instant_of_the_window_still_counts() -> None:
    evidence = _evidence(decided_at=NOW + timedelta(seconds=600))
    assert _check(evidence, _decider()) is None


@pytest.mark.parametrize(
    "decider",
    [
        None,
        _decider(active=False),
        _decider(grants=[]),
        _decider(grants=[_grant("staging")]),
        _decider(grants=[_grant("production"), _grant("production", effect="DENY")]),
        _decider(roles=set()),
        _decider(roles={"DEVOPS_ENGINEER"}),
    ],
)
def test_an_approver_who_lost_standing_is_revoked(decider: Decider | None) -> None:
    assert _check(_evidence(), decider) == rules.APPROVER_REVOKED


def test_a_requester_who_approved_their_own_request_is_refused_unless_the_policy_allows() -> None:
    own = _evidence(approved_by=REQUESTER)
    decider = _decider()
    assert _check(own, decider) == rules.SELF_APPROVAL
    assert _check(own, decider, SELF_OK) is None


def test_the_flag_on_the_operation_row_is_not_an_input() -> None:
    assert "approval_status" not in inspect.signature(approval_valid_for_execution).parameters


def test_every_refusal_is_in_the_closed_set() -> None:
    seen = {
        _check(None, _decider()),
        _check(_evidence(decision="REJECTED"), _decider()),
        _check(_evidence(decision="EXPIRED", approved_by=None), _decider()),
        _check(_evidence(), _decider(active=False)),
        _check(_evidence(approved_by=REQUESTER), _decider()),
    }
    assert seen <= rules.EXECUTION_REFUSALS
