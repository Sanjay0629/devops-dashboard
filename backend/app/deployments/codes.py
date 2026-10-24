"""The closed sets of the deployment lifecycle (``OPEN-137``; API_CONTRACTS §21.3).

``failure_reason`` is a stable machine code, never a sentence from Jenkins: every function here
goes from a *closed* input (a Jenkins reason, a pipeline result) to a *closed* output. Nothing
Jenkins says reaches a column or a page (CLAUDE.md §4 rule 10).

``HEALTH_CHECKS_FAILED`` is not here: M5.5 adds it with the health-check runner.
"""

from __future__ import annotations

from typing import Final

KINDS: Final[tuple[str, ...]] = ("DEPLOY", "ROLLBACK")
#: A deployment's approval column (DATABASE §5.2). No ``EXPIRED``: that is an operations value.
APPROVAL_STATUSES: Final[tuple[str, ...]] = ("NOT_REQUIRED", "PENDING", "APPROVED", "REJECTED")

PERMISSION_REVOKED: Final = "PERMISSION_REVOKED"
APPROVAL_REQUIRED: Final = "APPROVAL_REQUIRED"
TARGET_VALIDATION_FAILED: Final = "TARGET_VALIDATION_FAILED"
TARGET_CHANGED: Final = "TARGET_CHANGED"
REF_NOT_PERMITTED: Final = "REF_NOT_PERMITTED"
NOT_CONFIGURED: Final = "NOT_CONFIGURED"
JENKINS_UNAVAILABLE: Final = "JENKINS_UNAVAILABLE"
JENKINS_AUTHENTICATION: Final = "JENKINS_AUTHENTICATION"
JOB_NOT_FOUND: Final = "JOB_NOT_FOUND"
TRIGGER_OUTCOME_UNKNOWN: Final = "TRIGGER_OUTCOME_UNKNOWN"
QUEUE_ITEM_LOST: Final = "QUEUE_ITEM_LOST"
PIPELINE_FAILED: Final = "PIPELINE_FAILED"
PIPELINE_UNSTABLE: Final = "PIPELINE_UNSTABLE"
PIPELINE_ABORTED: Final = "PIPELINE_ABORTED"
PIPELINE_NOT_BUILT: Final = "PIPELINE_NOT_BUILT"
DEPLOYMENT_TIMED_OUT: Final = "DEPLOYMENT_TIMED_OUT"
EXPIRED_BEFORE_START: Final = "EXPIRED_BEFORE_START"

FAILURE_REASONS: Final[tuple[str, ...]] = (
    PERMISSION_REVOKED,
    APPROVAL_REQUIRED,
    TARGET_VALIDATION_FAILED,
    TARGET_CHANGED,
    REF_NOT_PERMITTED,
    NOT_CONFIGURED,
    JENKINS_UNAVAILABLE,
    JENKINS_AUTHENTICATION,
    JOB_NOT_FOUND,
    TRIGGER_OUTCOME_UNKNOWN,
    QUEUE_ITEM_LOST,
    PIPELINE_FAILED,
    PIPELINE_UNSTABLE,
    PIPELINE_ABORTED,
    PIPELINE_NOT_BUILT,
    DEPLOYMENT_TIMED_OUT,
    EXPIRED_BEFORE_START,
)

#: A build may exist that DCP cannot see or stop: an operator must look, and DCP never re-triggers.
RECONCILIATION_REASONS: Final[frozenset[str]] = frozenset(
    {TRIGGER_OUTCOME_UNKNOWN, QUEUE_ITEM_LOST, DEPLOYMENT_TIMED_OUT}
)

#: What Jenkins can report a finished build as (``app.deployments.jenkins``).
PIPELINE_RESULTS: Final[tuple[str, ...]] = (
    "SUCCESS",
    "FAILURE",
    "UNSTABLE",
    "ABORTED",
    "NOT_BUILT",
)
_RESULT_FAILURE: Final[dict[str, str]] = {
    "FAILURE": PIPELINE_FAILED,
    "UNSTABLE": PIPELINE_UNSTABLE,
    "ABORTED": PIPELINE_ABORTED,
    "NOT_BUILT": PIPELINE_NOT_BUILT,
}

#: A failed trigger that definitely did not queue anything (``JenkinsError.may_have_acted`` false)
#: is terminal, and is **not retried**: the user requests again (``OPEN-137``).
TRIGGER_FAILURE: Final[dict[str, str]] = {
    "not_configured": NOT_CONFIGURED,
    "provider_unreachable": JENKINS_UNAVAILABLE,
    "provider_timeout": JENKINS_UNAVAILABLE,
    "provider_error": JENKINS_UNAVAILABLE,
    "circuit_open": JENKINS_UNAVAILABLE,
    "destination_refused": JENKINS_UNAVAILABLE,
    "auth_failed": JENKINS_AUTHENTICATION,
    "crumb_rejected": JENKINS_AUTHENTICATION,
    "job_not_found": JOB_NOT_FOUND,
}
#: Always reconciled, whatever ``may_have_acted`` says: the request went out and no answer came.
TRIGGER_RECONCILE_ONLY: Final[tuple[str, ...]] = ("trigger_outcome_unknown",)


def is_failure_reason(value: object) -> bool:
    return isinstance(value, str) and value in FAILURE_REASONS


def failure_for_result(result: str) -> str | None:
    """The failure reason for a finished pipeline: ``None`` for ``SUCCESS``, else a closed code.

    A result Jenkins does not define is refused rather than guessed at: a success must never be
    inferred from text that merely looks like one.
    """
    if result not in PIPELINE_RESULTS:
        raise ValueError("not a Jenkins pipeline result")
    return _RESULT_FAILURE.get(result)


def trigger_failure(reason: str, *, may_have_acted: bool) -> str | None:
    """The terminal failure for a failed trigger, or ``None`` when it must be reconciled.

    An answer that may have queued a build is **never** terminal: ending the row would free the
    application's slot while a build might be running, and a second request would then trigger
    twice. ``None`` is the safe side.
    """
    if reason in TRIGGER_RECONCILE_ONLY:
        return None
    if reason not in TRIGGER_FAILURE:
        raise ValueError("not a Jenkins reason")
    return None if may_have_acted else TRIGGER_FAILURE[reason]


def needs_reconciliation(reason: str) -> bool:
    return reason in RECONCILIATION_REASONS


def terminal_status_for(reason: str) -> str:
    """Every failure ends in ``FAILED``: the public enum has no ``TIMED_OUT`` (§21)."""
    if reason not in FAILURE_REASONS:
        raise ValueError("unknown failure reason")
    return "FAILED"
