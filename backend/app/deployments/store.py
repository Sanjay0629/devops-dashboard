"""The one place a deployment's status changes (``OPEN-134``).

``transition_statement`` builds the guarded ``UPDATE`` for the async API (cancel) and, in commit 5,
the synchronous worker (claim, run, finish, reap); it executes nothing, so neither session type is
assumed. The statement carries ``WHERE execution_status = <expected>``: two actors racing one row
cannot both advance it, the loser's ``RETURNING`` is empty, and the API answers ``409`` while a
worker exits quietly.

It validates the *move* and the *evidence* before it builds anything -- a ``SUCCEEDED`` needs a
pipeline ``SUCCESS`` and a passing health result (:mod:`app.deployments.policy`), a ``FAILED`` a
closed reason, a ``RUNNING`` its build number -- and the schema's CHECKs are the second guard
behind each of them.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Final

from sqlalchemy import Update, func, literal, update
from sqlalchemy.dialects.postgresql import JSONB

from app.deployments import states
from app.deployments.codes import FAILURE_REASONS, needs_reconciliation, terminal_status_for
from app.deployments.models import Deployment

#: The columns a transition may set besides the status and the timestamps it owns.
_SETTABLE: Final[frozenset[str]] = frozenset(
    {"external_execution_id", "queue_item_id", "commit_sha", "failure_reason", "approval_status"}
)


def transition_statement(
    deployment_id: uuid.UUID,
    current: str,
    new: str,
    *,
    pipeline_result: str | None = None,
    health_check_result: Mapping[str, Any] | None = None,
    result: Mapping[str, Any] | None = None,
    poll_after_seconds: int | None = None,
    **fields: Any,
) -> Update:
    """``UPDATE deployments … WHERE id = :id AND execution_status = <current> RETURNING id``.

    * ``new = SUCCEEDED`` needs ``pipeline_result`` and ``health_check_result`` that satisfy
      :func:`app.deployments.policy.success`, otherwise
      :class:`~app.deployments.states.SuccessEvidenceError`; the health result is stored with it.
    * ``new = FAILED`` needs a ``failure_reason`` from the closed set; ``reconciliation_required``
      is then set from the reason, so a caller cannot forget it.
    * ``new = RUNNING`` needs the build number as ``external_execution_id`` and sets ``started_at``.
    * ``result`` is merged into ``sanitized_result`` (closed fields only).
    * A finished row stops being polled: ``next_poll_at`` is cleared and ``completed_at`` set.
    * ``poll_after_seconds`` is for a move that keeps the row in flight: when the poller may next
      look, and a clean slate for the failure counters (the move is itself good news).
    """
    states.require_transition(current, new)
    unknown = set(fields) - _SETTABLE
    if unknown:
        raise TypeError(f"not a settable deployment field: {sorted(unknown)}")
    states.require_success(new, pipeline_result, health_check_result)

    values: dict[str, Any] = dict(fields)
    merged: dict[str, Any] = dict(result or {})
    if pipeline_result is not None:
        merged["pipeline_result"] = pipeline_result
    if new == states.FAILED:
        reason = fields.get("failure_reason")
        if reason is None or reason not in FAILURE_REASONS or terminal_status_for(reason) != new:
            raise ValueError("a FAILED transition needs a failure_reason from the closed set")
        merged["reconciliation_required"] = needs_reconciliation(reason)
    elif "failure_reason" in fields:
        raise ValueError("failure_reason belongs to FAILED only")
    if new == states.RUNNING:
        if not fields.get("external_execution_id"):
            raise ValueError("a RUNNING transition needs the build number")
        values["started_at"] = func.now()
    if new == states.SUCCEEDED:
        values["health_check_result"] = dict(health_check_result or {})

    values["execution_status"] = new
    if new in states.TERMINAL:
        if poll_after_seconds is not None:
            raise ValueError("a finished deployment is not polled")
        values["completed_at"] = func.now()
        values["next_poll_at"] = None
    elif poll_after_seconds is not None:
        values["next_poll_at"] = func.now() + timedelta(seconds=poll_after_seconds)
        values["poll_attempts"] = 0
        values["last_poll_error"] = None
        values["last_polled_at"] = func.now()
    if merged:
        values["sanitized_result"] = Deployment.sanitized_result.op("||", return_type=JSONB)(
            literal(merged, type_=JSONB)
        )

    return (
        update(Deployment)
        .where(Deployment.id == deployment_id, Deployment.execution_status == current)
        .values(**values)
        .returning(Deployment.id)
    )


_REASON: Final = re.compile(r"[a-z][a-z_]{0,63}")


def poll_statement(
    deployment_id: uuid.UUID,
    current: str,
    *,
    delay_seconds: int,
    error: str | None = None,
    failed: bool = False,
    queue_item_id: int | None = None,
    result: Mapping[str, Any] | None = None,
) -> Update:
    """A move that **keeps** the status: when to look next, and what the last look found.

    Guarded by ``execution_status = current`` (a row the reaper finished meanwhile is not touched).
    ``failed`` counts a failed read toward the backoff; a good one resets the count. ``error`` is a
    closed Jenkins reason, never a sentence. ``queue_item_id`` is the recorded answer to a trigger,
    written only while none is recorded.
    """
    if current not in states.POLLED:
        raise ValueError("only a queued or running deployment is polled")
    if error is not None and _REASON.fullmatch(error) is None:
        raise ValueError("error must be a closed reason code")
    if failed and error is None:
        raise ValueError("a failed poll names its reason")
    values: dict[str, Any] = {
        "next_poll_at": func.now() + timedelta(seconds=delay_seconds),
        "last_polled_at": func.now(),
        "last_poll_error": error,
        "poll_attempts": Deployment.poll_attempts + 1 if failed else 0,
    }
    conditions = [Deployment.id == deployment_id, Deployment.execution_status == current]
    if queue_item_id is not None:
        if current != states.QUEUED:
            raise ValueError("a queue item belongs to a queued deployment")
        values["queue_item_id"] = queue_item_id
        conditions.append(Deployment.queue_item_id.is_(None))
    if result:
        values["sanitized_result"] = Deployment.sanitized_result.op("||", return_type=JSONB)(
            literal(dict(result), type_=JSONB)
        )
    return update(Deployment).where(*conditions).values(**values).returning(Deployment.id)
