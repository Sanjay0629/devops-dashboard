"""The operation state machine, as data (``OPEN-69``; docs/ARCHITECTURE.md §6.2).

Every status change is one of the pairs in ``_LEGAL``; there is no per-call special casing, so a
new path has to be added here on purpose and shows up in a reviewable diff. Terminal states have
no outgoing pair.

``VERIFYING`` is in the table (the worker passes through it) and in :func:`public_status`, but
**it is never stored**: ``operations.execution_status`` has exactly the PRD's nine values
(DATABASE.md §6), so a row that is submitting, polling or verifying is ``RUNNING``.

``PENDING -> TIMED_OUT`` and ``APPROVED -> TIMED_OUT`` exist for the reaper alone: a row nobody
claimed has no other legal exit, and claiming it just to time it out would put "started" in the
audit trail for work that never began.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

#: Exactly the PRD §4.6 list, and the ``execution_status`` CHECK.
PENDING: Final = "PENDING"
AWAITING_APPROVAL: Final = "AWAITING_APPROVAL"
APPROVED: Final = "APPROVED"
RUNNING: Final = "RUNNING"
SUCCEEDED: Final = "SUCCEEDED"
FAILED: Final = "FAILED"
TIMED_OUT: Final = "TIMED_OUT"
CANCELLED: Final = "CANCELLED"
REJECTED: Final = "REJECTED"
#: Internal to the worker. Never written to the database.
VERIFYING: Final = "VERIFYING"

STATUSES: Final[tuple[str, ...]] = (
    PENDING,
    AWAITING_APPROVAL,
    APPROVED,
    RUNNING,
    SUCCEEDED,
    FAILED,
    TIMED_OUT,
    CANCELLED,
    REJECTED,
)
TERMINAL: Final[frozenset[str]] = frozenset({SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, REJECTED})
#: Holds the target's one in-flight slot (``uq_operations_single_inflight``).
IN_FLIGHT: Final[tuple[str, ...]] = (PENDING, AWAITING_APPROVAL, APPROVED, RUNNING)
#: Waiting for a worker: what the healer re-enqueues and the reaper may expire unclaimed.
CLAIMABLE: Final[tuple[str, ...]] = (PENDING, APPROVED)
#: A requester (or an approver) may cancel only before a worker has claimed the row.
CANCELLABLE: Final[tuple[str, ...]] = (PENDING, AWAITING_APPROVAL)

#: ``(from, to)`` pairs that exist. Everything else is illegal.
_LEGAL: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        (AWAITING_APPROVAL, APPROVED),  # an approver other than the requester (M4.5)
        (AWAITING_APPROVAL, REJECTED),
        (AWAITING_APPROVAL, CANCELLED),  # the requester cancels / the approval window expires
        (PENDING, RUNNING),  # a worker claims the row
        (APPROVED, RUNNING),
        (PENDING, CANCELLED),  # cancelled before any claim
        (PENDING, TIMED_OUT),  # deadline passed unclaimed -- reaper only (OPEN-69)
        (APPROVED, TIMED_OUT),  # likewise
        (RUNNING, VERIFYING),  # the provider reports completion (in the worker only)
        (VERIFYING, SUCCEEDED),  # the observed state matches the intent
        (VERIFYING, FAILED),  # it does not
        (RUNNING, FAILED),  # the provider errored, or a re-check refused the claimed row
        (RUNNING, TIMED_OUT),  # the deadline passed (also while verifying: the row says RUNNING)
    }
)

#: What verification must observe for each requestable action (``OPEN-72``).
EXPECTED_STATE: Final[dict[str, str]] = {"start": "ACTIVE", "restart": "ACTIVE", "stop": "INACTIVE"}

#: ``service_action_policies.operation_timeout_seconds`` CHECK, mirrored so a bad value fails
#: closed here instead of producing a deadline nobody intended.
MIN_TIMEOUT_SECONDS: Final = 10
MAX_TIMEOUT_SECONDS: Final = 3_600


class InvalidTransitionError(Exception):
    """A move that is not in the table. The API maps it to ``409 invalid_state_transition``."""

    def __init__(self, current: str, new: str) -> None:
        super().__init__(f"{current} -> {new} is not a legal operation transition")
        self.current = current
        self.new = new


class VerificationRequiredError(Exception):
    """``SUCCEEDED`` without a verification result (CLAUDE.md §6, mandatory rule 8)."""


def can_transition(current: str, new: str) -> bool:
    return (current, new) in _LEGAL


def require_transition(current: str, new: str) -> None:
    if not can_transition(current, new):
        raise InvalidTransitionError(current, new)


def is_terminal(status: str) -> bool:
    return status in TERMINAL


def public_status(status: str) -> str:
    """What the API reports: the internal ``VERIFYING`` step is ``RUNNING`` (AD-09)."""
    return RUNNING if status == VERIFYING else status


def stored_status(status: str) -> str:
    """The stored value for ``status``: the internal ``VERIFYING`` is ``RUNNING`` in the row."""
    return RUNNING if status == VERIFYING else status


def verifies(action: str, observed_state_after: str | None) -> bool:
    """Did the observed state match what ``action`` was meant to bring about?"""
    expected = EXPECTED_STATE.get(action)
    return expected is not None and observed_state_after == expected


def require_verified_success(
    new: str, action: str | None, observed_state_after: str | None
) -> None:
    """The code half of "a success carries its evidence" (the schema's CHECK is the other).

    Stricter than the CHECK: the evidence must also be the state the action was meant to
    produce, which a column constraint cannot know.
    """
    if new == SUCCEEDED and (action is None or not verifies(action, observed_state_after)):
        raise VerificationRequiredError


def deadline_at(created_at: datetime, timeout_seconds: int) -> datetime:
    """The request time plus the policy timeout: queue wait counts against the deadline."""
    if created_at.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    if not MIN_TIMEOUT_SECONDS <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout_seconds is outside the policy range")
    return created_at + timedelta(seconds=timeout_seconds)
