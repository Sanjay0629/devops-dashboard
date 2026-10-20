"""Closed code sets for operations (``OPEN-71``, ``OPEN-73``, ``OPEN-75``).

``failure_reason`` is a closed set in code and free text in the column, so a client tolerates a
code added later. Nothing here is ever built from provider text.
"""

from __future__ import annotations

from typing import Final

from app.operations import states

#: The actions §17.1 accepts. ``status`` and ``logs_tail`` stay in the table's CHECK but are
#: served by §16.4 and §16.7 (``OPEN-67``).
REQUESTABLE_ACTIONS: Final[tuple[str, ...]] = ("start", "stop", "restart")
#: The table's CHECK: the contract's full enum.
ACTION_TYPES: Final[tuple[str, ...]] = ("start", "stop", "restart", "status", "logs_tail")
APPROVAL_STATUSES: Final[tuple[str, ...]] = (
    "NOT_REQUIRED",
    "PENDING",
    "APPROVED",
    "REJECTED",
    "EXPIRED",
)

# --- failure reasons (the row's `failure_reason`) ---------------------------------------------
# refused by a re-check, before anything was sent
PERMISSION_REVOKED: Final = "PERMISSION_REVOKED"
APPROVAL_REQUIRED: Final = "APPROVAL_REQUIRED"
ACCOUNT_DISABLED: Final = "ACCOUNT_DISABLED"
ACTION_NOT_ALLOWED: Final = "ACTION_NOT_ALLOWED"
TARGET_VALIDATION_FAILED: Final = "TARGET_VALIDATION_FAILED"
# the provider refused before acting
NOT_CONFIGURED: Final = "NOT_CONFIGURED"
UNSUPPORTED_OPERATION: Final = "UNSUPPORTED_OPERATION"
PROVIDER_AUTHENTICATION: Final = "PROVIDER_AUTHENTICATION"
PROVIDER_AUTHORIZATION: Final = "PROVIDER_AUTHORIZATION"
PROVIDER_NOT_FOUND: Final = "PROVIDER_NOT_FOUND"
PROVIDER_THROTTLED: Final = "PROVIDER_THROTTLED"
# the provider may have acted
PROVIDER_TIMEOUT: Final = "PROVIDER_TIMEOUT"
PROVIDER_UNAVAILABLE: Final = "PROVIDER_UNAVAILABLE"
PROVIDER_PARTIAL_FAILURE: Final = "PROVIDER_PARTIAL_FAILURE"
PROVIDER_REPORTED_FAILURE: Final = "PROVIDER_REPORTED_FAILURE"
# verification
VERIFICATION_MISMATCH: Final = "VERIFICATION_MISMATCH"
VERIFICATION_UNAVAILABLE: Final = "VERIFICATION_UNAVAILABLE"
# a resumer refused to continue an operation whose command is already running (M4.3)
RESUME_REFUSED: Final = "RESUME_REFUSED"
# time
DEADLINE_EXCEEDED: Final = "DEADLINE_EXCEEDED"
EXPIRED_BEFORE_START: Final = "EXPIRED_BEFORE_START"

FAILURE_REASONS: Final[tuple[str, ...]] = (
    PERMISSION_REVOKED,
    APPROVAL_REQUIRED,
    ACCOUNT_DISABLED,
    ACTION_NOT_ALLOWED,
    TARGET_VALIDATION_FAILED,
    NOT_CONFIGURED,
    UNSUPPORTED_OPERATION,
    PROVIDER_AUTHENTICATION,
    PROVIDER_AUTHORIZATION,
    PROVIDER_NOT_FOUND,
    PROVIDER_THROTTLED,
    PROVIDER_TIMEOUT,
    PROVIDER_UNAVAILABLE,
    PROVIDER_PARTIAL_FAILURE,
    PROVIDER_REPORTED_FAILURE,
    VERIFICATION_MISMATCH,
    VERIFICATION_UNAVAILABLE,
    RESUME_REFUSED,
    DEADLINE_EXCEEDED,
    EXPIRED_BEFORE_START,
)

#: Outcomes where the provider may have acted and DCP does not know (``OPEN-71``). Such a row
#: is flagged for reconciliation and is **never** re-invoked.
RECONCILIATION_REASONS: Final[frozenset[str]] = frozenset(
    {
        PROVIDER_TIMEOUT,
        PROVIDER_UNAVAILABLE,
        PROVIDER_PARTIAL_FAILURE,
        VERIFICATION_UNAVAILABLE,
        RESUME_REFUSED,
        DEADLINE_EXCEEDED,
    }
)
#: The ends that mean "we do not know" or "it never began" rather than "it failed".
TIMED_OUT_REASONS: Final[frozenset[str]] = frozenset(
    {PROVIDER_TIMEOUT, DEADLINE_EXCEEDED, EXPIRED_BEFORE_START}
)

# --- reasons on a request refused with 503 (`details.reason`) ----------------------------------
APPROVAL_POLICY_UNSUPPORTED: Final = "approval_policy_unsupported"
DEMONSTRATION_MODE: Final = "demonstration_mode"
NO_ADAPTER_FOR_PROVIDER: Final = "no_adapter_for_provider"


def terminal_status_for(reason: str) -> str:
    """The terminal status a failure reason ends a claimed row in."""
    if reason not in FAILURE_REASONS:
        raise ValueError("unknown failure reason")
    return states.TIMED_OUT if reason in TIMED_OUT_REASONS else states.FAILED


def needs_reconciliation(reason: str) -> bool:
    return reason in RECONCILIATION_REASONS
