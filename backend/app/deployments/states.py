"""The deployment state machine, as data (``OPEN-134``; docs/ARCHITECTURE.md §8.1).

Every status change is one of the pairs in ``_LEGAL``; there is no per-call special casing, so a
new path has to be added here on purpose and shows up in a reviewable diff. Terminal states have no
outgoing pair. The statuses are exactly PRD §4.10's nine: there is **no** ``TIMED_OUT`` (a deadline
is ``FAILED`` with ``DEPLOYMENT_TIMED_OUT``, API_CONTRACTS §21) and no ``VERIFYING``.

**The claim is the move to ``QUEUED``.** A worker claims ``REQUESTED`` or ``APPROVED`` by moving
it to ``QUEUED`` and committing *before* it calls Jenkins, so two workers cannot both trigger and
a ``QUEUED`` row with no queue id means "the trigger may have been sent" (``OPEN-137``).

Three edges are added to the diagram of §8.1 and recorded in ``OPEN-134``: ``REQUESTED -> FAILED``
and ``APPROVED -> FAILED`` (the reaper only: a row nobody claimed before its deadline) and
``QUEUED -> FAILED``. ``QUEUED -> CANCELLED`` is *Jenkins* reporting the queue item cancelled; a
request cannot ask for it (``CANCELLABLE``), because DCP does not abort a Jenkins build.

There is no edge into ``AWAITING_APPROVAL`` (nothing in M5.2 creates one) and none into
``ROLLED_BACK`` (M5.5 adds ``SUCCEEDED -> ROLLED_BACK`` together with the thing that uses it).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Final

from app.deployments import policy

REQUESTED: Final = "REQUESTED"
AWAITING_APPROVAL: Final = "AWAITING_APPROVAL"
APPROVED: Final = "APPROVED"
QUEUED: Final = "QUEUED"
RUNNING: Final = "RUNNING"
SUCCEEDED: Final = "SUCCEEDED"
FAILED: Final = "FAILED"
CANCELLED: Final = "CANCELLED"
ROLLED_BACK: Final = "ROLLED_BACK"

#: Exactly PRD §4.10's list, and the ``execution_status`` CHECK.
STATUSES: Final[tuple[str, ...]] = (
    REQUESTED,
    AWAITING_APPROVAL,
    APPROVED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    FAILED,
    CANCELLED,
    ROLLED_BACK,
)
#: No outgoing edge. ``SUCCEEDED`` is here because M5.2 has none out of it; M5.5 adds one to
#: ``ROLLED_BACK`` and the in-flight rule below does not change, since a ``SUCCEEDED`` row has
#: already released the application's slot.
TERMINAL: Final[frozenset[str]] = frozenset({SUCCEEDED, FAILED, CANCELLED, ROLLED_BACK})
#: Holds the application and environment's one slot (``uq_deployments_single_inflight``).
IN_FLIGHT: Final[tuple[str, ...]] = (REQUESTED, AWAITING_APPROVAL, APPROVED, QUEUED, RUNNING)
#: Waiting for a worker: what the healer re-enqueues and the reaper may expire unclaimed.
CLAIMABLE: Final[tuple[str, ...]] = (REQUESTED, APPROVED)
#: A requester (or an approver) may cancel only before a worker has claimed the row.
CANCELLABLE: Final[tuple[str, ...]] = (REQUESTED, AWAITING_APPROVAL)
#: Rows Jenkins may hold: what the poller leases.
POLLED: Final[tuple[str, ...]] = (QUEUED, RUNNING)

#: ``(from, to)`` pairs that exist. Everything else is illegal.
_LEGAL: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        (AWAITING_APPROVAL, APPROVED),  # an approver other than the requester (M5.4)
        (AWAITING_APPROVAL, CANCELLED),  # rejected, cancelled, or the window expired (M5.4)
        (REQUESTED, QUEUED),  # a worker claims the row, before it calls Jenkins
        (APPROVED, QUEUED),
        (REQUESTED, CANCELLED),  # the requester cancels before any claim
        (REQUESTED, FAILED),  # deadline passed unclaimed -- reaper only
        (APPROVED, FAILED),  # likewise
        (QUEUED, RUNNING),  # Jenkins assigned a build number
        (QUEUED, CANCELLED),  # Jenkins reports the queue item cancelled
        (QUEUED, FAILED),  # re-check refused, trigger refused or lost, deadline
        (RUNNING, SUCCEEDED),  # pipeline success AND the health result passes
        (RUNNING, FAILED),  # pipeline did not succeed, or the deadline passed
    }
)

#: ``DCP_DEPLOYMENT_DEADLINE_SECONDS``: the default and the bounds it is validated against.
DEFAULT_DEADLINE_SECONDS: Final = 3_600
MIN_DEADLINE_SECONDS: Final = 60
MAX_DEADLINE_SECONDS: Final = 21_600


class InvalidTransitionError(Exception):
    """A move that is not in the table. The API maps it to ``409 invalid_state_transition``."""

    def __init__(self, current: str, new: str) -> None:
        super().__init__(f"{current} -> {new} is not a legal deployment transition")
        self.current = current
        self.new = new


class SuccessEvidenceError(Exception):
    """``SUCCEEDED`` without a pipeline success and a passing health result (rule 8).

    The machine's half of two independent guards; the schema's ``CHECK`` is the other, and the
    tests show each alone refuses.
    """


def can_transition(current: str, new: str) -> bool:
    return (current, new) in _LEGAL


def require_transition(current: str, new: str) -> None:
    if not can_transition(current, new):
        raise InvalidTransitionError(current, new)


def is_terminal(status: str) -> bool:
    return status in TERMINAL


def require_success(
    new: str, pipeline_result: str | None, health_check_result: Mapping[str, Any] | None
) -> None:
    """Refuse ``SUCCEEDED`` unless :func:`app.deployments.policy.success` holds; others pass."""
    if new == SUCCEEDED and not policy.success(pipeline_result, health_check_result):
        raise SuccessEvidenceError


def deadline_at(created_at: datetime, timeout_seconds: int) -> datetime:
    """The request time plus the setting: queue and approval wait count against the deadline."""
    if created_at.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    if isinstance(timeout_seconds, bool) or not (
        MIN_DEADLINE_SECONDS <= timeout_seconds <= MAX_DEADLINE_SECONDS
    ):
        raise ValueError("timeout_seconds is outside the deployment deadline range")
    return created_at + timedelta(seconds=timeout_seconds)
