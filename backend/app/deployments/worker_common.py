"""What the deployment worker's three entry points share (``OPEN-136``).

``execute`` (claim and trigger), ``poll`` (follow a build) and ``reap`` (the deadline) all read a
row as **plain values** -- no ORM object lives across a Jenkins call -- and all end a deployment the
same way: one guarded ``UPDATE`` and its audit rows in a single transaction, so a transition that
did not happen has no audit row and an audit row always has its transition.

Every row the worker writes is attributed to the requester who started it, carried out by the named
system actor (mandatory rule 7), under the deployment's own correlation id.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.logging import get_logger
from app.deployments import states
from app.deployments.models import Deployment
from app.deployments.store import transition_statement

logger = get_logger(__name__)

ACTOR_LABEL: Final = "system:deployments-worker"


@dataclass(frozen=True)
class Dep:
    """The deployment as plain values."""

    id: uuid.UUID
    application_id: uuid.UUID
    target_id: uuid.UUID
    environment: str
    requested_by: uuid.UUID
    requested_ref: str
    pipeline_reference: str
    target_digest: str
    approval_status: str
    status: str
    deadline_at: datetime
    created_at: datetime
    correlation_id: uuid.UUID
    queue_item_id: int | None
    build: str | None
    poll_attempts: int
    claimed_at: datetime | None

    @property
    def build_number(self) -> int | None:
        return int(self.build) if self.build is not None and self.build.isdigit() else None


def to_dep(row: Deployment) -> Dep:
    claimed = (row.sanitized_result or {}).get("claimed_at")
    try:
        claimed_at = datetime.fromisoformat(claimed) if isinstance(claimed, str) else None
    except ValueError:
        claimed_at = None
    return Dep(
        row.id,
        row.application_id,
        row.deployment_target_id,
        row.environment,
        row.requested_by,
        row.requested_ref,
        row.pipeline_reference,
        row.target_digest,
        row.approval_status,
        row.execution_status,
        row.deadline_at,
        row.created_at,
        row.correlation_id,
        row.queue_item_id,
        row.external_execution_id,
        row.poll_attempts,
        claimed_at,
    )


def now_utc() -> datetime:
    return datetime.now(UTC)


def record(
    session: Session, dep: Dep, action_type: str, outcome: str, metadata: dict[str, Any]
) -> None:
    """Attributed to the requester who started it, by the named system actor that carried it out."""
    audit.record(
        session,
        action_type=action_type,
        outcome=outcome,
        actor_id=dep.requested_by,
        actor_label=ACTOR_LABEL,
        target_type="deployment",
        target_id=dep.id,
        environment=dep.environment,
        correlation_id=dep.correlation_id,
        metadata={
            "on_behalf_of": str(dep.requested_by),
            "application_id": str(dep.application_id),
            **metadata,
        },
    )


def finish(
    engine: Engine,
    dep: Dep,
    *,
    current: str,
    new: str,
    failure_reason: str | None = None,
    pipeline_result: str | None = None,
    health_check_result: dict[str, Any] | None = None,
    commit_sha: str | None = None,
    result: dict[str, Any] | None = None,
    refused: bool = False,
    phase: str = "worker",
) -> str:
    """Write a terminal state and its audit rows together. ``superseded`` if the row moved on.

    The guarded ``UPDATE`` is what decides a race with the reaper or another worker: the loser's
    write finds the status changed and changes nothing, so a late success cannot resurrect a row
    the deadline already ended.
    """
    fields: dict[str, Any] = {}
    if failure_reason is not None:
        fields["failure_reason"] = failure_reason
    if commit_sha is not None:
        fields["commit_sha"] = commit_sha
    with Session(engine) as session:
        won = session.execute(
            transition_statement(
                dep.id,
                current,
                new,
                pipeline_result=pipeline_result,
                health_check_result=health_check_result,
                result=result,
                **fields,
            )
        ).first()
        if won is None:
            session.rollback()
            logger.warning("deployment_finish_superseded", deployment_id=str(dep.id))
            return "superseded"
        if refused:
            record(
                session,
                dep,
                "DEPLOYMENT_DENIED",
                "DENIED",
                {"reason": failure_reason, "phase": phase},
            )
        record(
            session,
            dep,
            "DEPLOYMENT_COMPLETED",
            "SUCCESS" if new == states.SUCCEEDED else "FAILURE",
            {
                "status": new,
                "failure_reason": failure_reason,
                "pipeline_result": pipeline_result,
                "build": dep.build,
                "commit_sha": commit_sha,
                "phase": phase,
            },
        )
        session.commit()
    logger.info("deployment_finished", deployment_id=str(dep.id), status=new, reason=failure_reason)
    return new if failure_reason is None else f"{new}:{failure_reason}"
