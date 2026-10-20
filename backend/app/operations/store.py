"""The one place a status changes (``OPEN-69``).

``transition_statement`` builds the guarded ``UPDATE`` for both the async API (cancel) and the
synchronous worker (claim, finalise, reap); it executes nothing, so neither session type is
assumed. The statement carries ``WHERE execution_status = <expected>``, so two actors racing one
row cannot both advance it: the loser's ``RETURNING`` is empty and the caller treats that as
an ``InvalidTransitionError``-shaped loss (the API answers ``409``, a worker exits quietly).

Nothing here inspects a provider or a request: it validates the *move* and the *evidence*, and
the schema's CHECKs are the second guard behind it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import Update, func, literal, update
from sqlalchemy.dialects.postgresql import JSONB

from app.operations import states
from app.operations.codes import FAILURE_REASONS, needs_reconciliation, terminal_status_for
from app.operations.models import Operation

#: The columns a transition may set besides the status and the two timestamps it owns.
_SETTABLE: Final[frozenset[str]] = frozenset(
    {
        "external_execution_id",
        "observed_state_before",
        "observed_state_after",
        "failure_reason",
    }
)


def transition_statement(
    operation_id: uuid.UUID,
    current: str,
    new: str,
    *,
    action: str | None = None,
    result: dict[str, Any] | None = None,
    approval_status: str | None = None,
    deadline_after_seconds: int | None = None,
    **fields: Any,
) -> Update:
    """``UPDATE operations … WHERE id = :id AND execution_status = <stored current> RETURNING id``.

    ``current`` / ``new`` may be the internal ``VERIFYING``: it is stored as ``RUNNING`` and a
    move *into* it writes nothing, so asking for one is a programming error.

    * ``new = SUCCEEDED`` needs ``action`` and an ``observed_state_after`` that is the state the
      action was meant to bring about -- otherwise ``VerificationRequiredError``.
    * ``new`` in ``FAILED`` / ``TIMED_OUT`` needs a ``failure_reason`` that belongs to that status;
      ``reconciliation_required`` is then set from the reason, so a caller cannot forget it.
    * ``result`` is merged into ``sanitized_result`` (closed fields only; see ``OPEN-74``).
    * ``approval_status`` is set in the same statement (the approval workflow's moves, M4.5);
      ``deadline_after_seconds`` restarts the deadline at the database's ``now()`` -- an approved
      operation gets its full timeout from the moment it was approved (``OPEN-96``).
    """
    if new == states.VERIFYING:
        raise ValueError("VERIFYING is never written")
    states.require_transition(current, new)
    unknown = set(fields) - _SETTABLE
    if unknown:
        raise TypeError(f"not a settable operation field: {sorted(unknown)}")
    states.require_verified_success(new, action, fields.get("observed_state_after"))

    values: dict[str, Any] = dict(fields)
    merged = dict(result or {})
    if new in (states.FAILED, states.TIMED_OUT):
        reason = fields.get("failure_reason")
        if reason is None or reason not in FAILURE_REASONS or terminal_status_for(reason) != new:
            raise ValueError("a FAILED or TIMED_OUT transition needs a failure_reason that fits it")
        merged["reconciliation_required"] = needs_reconciliation(reason)
    elif "failure_reason" in fields:
        raise ValueError("failure_reason belongs to FAILED and TIMED_OUT only")

    values["execution_status"] = new
    if approval_status is not None:
        values["approval_status"] = approval_status
    if deadline_after_seconds is not None:
        values["deadline_at"] = func.now() + timedelta(seconds=deadline_after_seconds)
    if new == states.RUNNING:
        values["started_at"] = func.now()
    if new in states.TERMINAL:
        values["completed_at"] = func.now()
    if merged:
        values["sanitized_result"] = Operation.sanitized_result.op("||", return_type=JSONB)(
            literal(merged, type_=JSONB)
        )

    return (
        update(Operation)
        .where(
            Operation.id == operation_id,
            Operation.execution_status == states.stored_status(current),
        )
        .values(**values)
        .returning(Operation.id)
    )


def record_handle_statement(
    operation_id: uuid.UUID,
    external_execution_id: str,
    *,
    observed_state_before: str | None,
    steps: list[str],
) -> Update:
    """Persist the provider's id **before** polling begins (AD-08, ``OPEN-70``).

    Guarded on ``RUNNING``: a row the reaper has already timed out is not written to, and the
    caller learns that from the empty ``RETURNING``. Not a transition, so it lives beside the
    transition builder rather than in it.
    """
    return (
        update(Operation)
        .where(Operation.id == operation_id, Operation.execution_status == states.RUNNING)
        .values(
            external_execution_id=external_execution_id,
            heartbeat_at=func.clock_timestamp(),
            observed_state_before=observed_state_before,
            sanitized_result=Operation.sanitized_result.op("||", return_type=JSONB)(
                literal({"steps": steps}, type_=JSONB)
            ),
        )
        .returning(Operation.id)
    )


def heartbeat_statement(operation_id: uuid.UUID) -> Update:
    """Say "still polling" (``OPEN-84``). Guarded on ``RUNNING``: never touches a finished row."""
    return (
        update(Operation)
        .where(Operation.id == operation_id, Operation.execution_status == states.RUNNING)
        .values(heartbeat_at=func.clock_timestamp())
        .returning(Operation.id)
    )


def resume_claim_statement(
    operation_id: uuid.UUID,
    seen_heartbeat: datetime | None,
    *,
    stale_after_seconds: int,
    steps: list[str],
) -> Update:
    """Take over a ``RUNNING`` row whose worker has gone quiet (``OPEN-84``).

    Two guards in one statement: the row must still be ``RUNNING`` with a recorded handle and
    a heartbeat (or, failing that, a start) older than ``stale_after_seconds`` **by the
    database's clock**, and the heartbeat must still be the value the caller saw -- so of two
    resumers racing one row, exactly one gets a ``RETURNING`` row. It never changes the status:
    resuming is polling, not a transition.
    """
    return (
        update(Operation)
        .where(
            Operation.id == operation_id,
            Operation.execution_status == states.RUNNING,
            Operation.external_execution_id.is_not(None),
            Operation.heartbeat_at.is_not_distinct_from(seen_heartbeat),
            func.coalesce(Operation.heartbeat_at, Operation.started_at)
            < func.now() - timedelta(seconds=stale_after_seconds),
        )
        .values(
            heartbeat_at=func.clock_timestamp(),
            sanitized_result=Operation.sanitized_result.op("||", return_type=JSONB)(
                literal({"steps": steps}, type_=JSONB)
            ),
        )
        .returning(Operation.id)
    )
