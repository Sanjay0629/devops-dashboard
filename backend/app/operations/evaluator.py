"""Does this operation need an approval? (``OPEN-68``; docs/SECURITY.md §16, mandatory rule 3.)

One pure function, called at request time **and** by the worker, so the two cannot disagree.
Every doubt is an answer of *required*: an unreadable or mismatched policy, an environment the
code does not know, and a ``stop`` on production (SECURITY §16: "defaults to requiring
approval", whatever the rows say).

Until the approval workflow exists (M4.5) the request path turns *required* into a refusal and
the worker turns it into ``APPROVAL_REQUIRED`` unless the row is already ``APPROVED``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.approvals.models import ENVIRONMENTS

NOT_REQUIRED: Final = "none"
BY_POLICY: Final = "policy"
PRODUCTION_STOP: Final = "production_stop"
POLICY_UNREADABLE: Final = "policy_unreadable"
UNKNOWN_ENVIRONMENT: Final = "unknown_environment"


@dataclass(frozen=True)
class ApprovalPolicyView:
    """The two columns of ``approval_policies`` the decision reads."""

    applies_to: str
    environment: str


@dataclass(frozen=True)
class Decision:
    required: bool
    #: One of the module constants; a log and audit value, never shown as free text.
    basis: str


def approval_required(
    *,
    environment: str,
    action: str,
    policy_linked: bool,
    policy: ApprovalPolicyView | None,
) -> Decision:
    """``policy_linked`` is ``service_action_policies.approval_policy_id IS NOT NULL``;
    ``policy`` is the row it points at, or ``None`` when it could not be read.
    """
    if environment not in ENVIRONMENTS:
        return Decision(True, UNKNOWN_ENVIRONMENT)
    if action == "stop" and environment == "production":
        return Decision(True, PRODUCTION_STOP)
    if not policy_linked:
        return Decision(False, NOT_REQUIRED)
    if policy is None or policy.applies_to != "SERVICE_OPERATION":
        return Decision(True, POLICY_UNREADABLE)
    if policy.environment in (environment, "*"):
        return Decision(True, BY_POLICY)
    return Decision(False, NOT_REQUIRED)


def reason_required(*, environment: str, approval_needed: bool) -> bool:
    """A reason is required for ``production`` and wherever approval is required."""
    return approval_needed or environment == "production"
