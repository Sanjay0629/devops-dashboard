"""The three pure decisions behind the approval workflow (``OPEN-95``, ``OPEN-98``, ``OPEN-99``).

No database, no request, no clock: values in, a verdict out, so each is exercised by a table of
cases and the API, the sweep and the worker cannot disagree about them.

* :func:`resolve_policy` -- is approval required, and on what terms? Wraps the M4.2 evaluator
  (:func:`app.operations.evaluator.approval_required`, unchanged: it already answers *required* on
  every doubt) and adds the terms, falling back to the built-in defaults where no usable policy
  row exists, and flagging a policy this milestone cannot honour.
* :func:`may_decide` -- may this identity decide this request? Scope, separation of duties and
  role, in that order. The one place a requester is compared with a decider.
* :func:`approval_valid_for_execution` -- the worker's re-check, from *evidence* (an approvals
  row and the decider's present standing), never from the flag on the operation row.

Every function fails closed: anything it does not understand is a refusal.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from app.approvals.models import ENVIRONMENTS
from app.authz.matcher import Grant, Target, permits
from app.operations.evaluator import ApprovalPolicyView, approval_required

SERVICE_OPERATION: Final = "SERVICE_OPERATION"

# --- why a decision was refused (API ``details.reason`` and the audit ``reason``) -----------
OUT_OF_SCOPE: Final = "out_of_scope"
SELF_APPROVAL_NOT_PERMITTED: Final = "self_approval_not_permitted"
APPROVER_ROLE_REQUIRED: Final = "approver_role_required"

# --- why the worker refused an approval (``sanitized_result.reason_code``; closed set) -------
APPROVAL_MISSING: Final = "approval_missing"
APPROVAL_NOT_APPROVED: Final = "approval_not_approved"
APPROVER_REVOKED: Final = "approver_revoked"
SELF_APPROVAL: Final = "self_approval"
APPROVAL_EXPIRED: Final = "approval_expired"
APPROVAL_POLICY_UNSUPPORTED: Final = "approval_policy_unsupported"

EXECUTION_REFUSALS: Final[frozenset[str]] = frozenset(
    {
        APPROVAL_MISSING,
        APPROVAL_NOT_APPROVED,
        APPROVER_REVOKED,
        SELF_APPROVAL,
        APPROVAL_EXPIRED,
        APPROVAL_POLICY_UNSUPPORTED,
    }
)


@dataclass(frozen=True)
class PolicyRow:
    """The columns of ``approval_policies`` the workflow reads."""

    policy_id: uuid.UUID
    applies_to: str
    environment: str
    required_approvals: int
    allow_self_approval: bool
    required_role: str
    approval_window_seconds: int


@dataclass(frozen=True)
class PolicyTerms:
    """What a request is decided under. ``policy_id`` is ``None`` for the built-in defaults."""

    policy_id: uuid.UUID | None
    required_approvals: int
    allow_self_approval: bool
    required_role: str
    approval_window_seconds: int


#: One approval, no self-approval, role ``APPROVER``, an hour: the seeded production values
#: (``OPEN-95``). Used for a production ``stop`` with no policy and for a policy that cannot be
#: read or does not apply -- the safest terms, not the loosest.
DEFAULT_TERMS: Final = PolicyTerms(
    policy_id=None,
    required_approvals=1,
    allow_self_approval=False,
    required_role="APPROVER",
    approval_window_seconds=3_600,
)


@dataclass(frozen=True)
class Resolution:
    required: bool
    #: The evaluator's basis: a log and audit value, never free text.
    basis: str
    #: ``None`` when approval is not required.
    terms: PolicyTerms | None
    #: ``True`` when the governing policy asks for more than one approval, which M4.5 does not
    #: implement: the request is refused rather than treated as if one would do (``OPEN-95``).
    unsupported: bool


def _applies(policy: PolicyRow | None, environment: str) -> PolicyRow | None:
    if policy is None or policy.applies_to != SERVICE_OPERATION:
        return None
    return policy if policy.environment in (environment, "*") else None


def resolve_policy(
    *,
    environment: str,
    action: str,
    policy_linked: bool,
    policy: PolicyRow | None,
) -> Resolution:
    """``policy_linked`` is ``service_action_policies.approval_policy_id IS NOT NULL``; ``policy``
    is the row it points at, or ``None`` when it could not be read."""
    decision = approval_required(
        environment=environment,
        action=action,
        policy_linked=policy_linked,
        policy=None
        if policy is None
        else ApprovalPolicyView(policy.applies_to, policy.environment),
    )
    if not decision.required:
        return Resolution(False, decision.basis, None, False)
    governing = _applies(policy, environment) if environment in ENVIRONMENTS else None
    if governing is None:
        return Resolution(True, decision.basis, DEFAULT_TERMS, False)
    terms = PolicyTerms(
        governing.policy_id,
        governing.required_approvals,
        governing.allow_self_approval,
        governing.required_role,
        governing.approval_window_seconds,
    )
    return Resolution(True, decision.basis, terms, governing.required_approvals != 1)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    #: ``None`` when allowed; otherwise one of the refusal constants above.
    reason: str | None = None


_ALLOWED: Final = Verdict(True)


def may_decide(
    *,
    requester_id: uuid.UUID,
    decider_id: uuid.UUID,
    policy: PolicyTerms,
    grants: Collection[Grant],
    target: Target,
    decider_roles: Collection[str],
) -> Verdict:
    """May ``decider_id`` approve or reject a request made by ``requester_id`` on ``target``?

    In order: ``operation:approve`` over the target's **stored** environment and server (the one
    matcher); the requester is not the decider unless the policy row says so; the decider holds
    the role the policy names. ``decider_roles`` are the *unexpired* role names of the decider.
    """
    if not permits(list(grants), "operation", "approve", target).allowed:
        return Verdict(False, OUT_OF_SCOPE)
    if requester_id == decider_id and not policy.allow_self_approval:
        return Verdict(False, SELF_APPROVAL_NOT_PERMITTED)
    if policy.required_role not in decider_roles:
        return Verdict(False, APPROVER_ROLE_REQUIRED)
    return _ALLOWED


@dataclass(frozen=True)
class Evidence:
    """One ``approvals`` row, as the worker read it."""

    operation_type: str
    operation_id: uuid.UUID
    requested_by: uuid.UUID
    decision: str
    approved_by: uuid.UUID | None
    decided_at: datetime | None
    expires_at: datetime


@dataclass(frozen=True)
class Decider:
    """The approver's standing *now*: read again, not remembered from the decision."""

    active: bool
    grants: Collection[Grant]
    roles: Collection[str]


def approval_valid_for_execution(
    *,
    operation_id: uuid.UUID,
    requested_by: uuid.UUID,
    target: Target,
    policy: PolicyTerms,
    evidence: Evidence | None,
    decider: Decider | None,
) -> str | None:
    """``None`` if the operation may run; otherwise the closed reason it may not.

    ``approval_status = 'APPROVED'`` on the operation row proves nothing and is not an input: a
    row edited in SQL must not pass (``OPEN-99``). ``decider`` is ``None`` when the approver's
    user row could not be read.
    """
    if (
        evidence is None
        or evidence.operation_type != SERVICE_OPERATION
        or evidence.operation_id != operation_id
        or evidence.requested_by != requested_by
    ):
        return APPROVAL_MISSING
    if evidence.decision == "EXPIRED":
        return APPROVAL_EXPIRED
    if (
        evidence.decision != "APPROVED"
        or evidence.approved_by is None
        or evidence.decided_at is None
    ):
        return APPROVAL_NOT_APPROVED
    if evidence.decided_at > evidence.expires_at:
        return APPROVAL_EXPIRED
    if decider is None or not decider.active:
        return APPROVER_REVOKED
    if evidence.approved_by == requested_by and not policy.allow_self_approval:
        return SELF_APPROVAL
    if (
        not permits(list(decider.grants), "operation", "approve", target).allowed
        or policy.required_role not in decider.roles
    ):
        return APPROVER_REVOKED
    return None
