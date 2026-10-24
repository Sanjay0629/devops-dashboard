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

import uuid
from collections.abc import Mapping
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
        values["completed_at"] = func.now()
        values["next_poll_at"] = None
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
