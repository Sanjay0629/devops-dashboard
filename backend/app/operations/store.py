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
    **fields: str,
) -> Update:
    """``UPDATE operations … WHERE id = :id AND execution_status = <stored current> RETURNING id``.

    ``current`` / ``new`` may be the internal ``VERIFYING``: it is stored as ``RUNNING`` and a
    move *into* it writes nothing, so asking for one is a programming error.

    * ``new = SUCCEEDED`` needs ``action`` and an ``observed_state_after`` that is the state the
      action was meant to bring about -- otherwise ``VerificationRequiredError``.
    * ``new`` in ``FAILED`` / ``TIMED_OUT`` needs a ``failure_reason`` that belongs to that status;
      ``reconciliation_required`` is then set from the reason, so a caller cannot forget it.
    * ``result`` is merged into ``sanitized_result`` (closed fields only; see ``OPEN-74``).
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
