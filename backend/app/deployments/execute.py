"""Running a deployment's first half: claim, re-authorise, trigger (``OPEN-134`` – ``OPEN-137``).

Synchronous, because workers are. The Celery task is a thin wrapper
(``app/workers/tasks/deployments.py``); everything that matters is here so a test can drive it with
a real database and an in-process Jenkins.

The order, and why it is this order:

1. **Claim with a guarded ``UPDATE``, committed before anything is sent.** The claim *is* the move
   to ``QUEUED``. A second delivery, or a second worker, finds the row no longer
   ``REQUESTED`` / ``APPROVED`` and exits having done nothing -- so the trigger below is reached
   once per deployment, however many times this function runs.
2. **Re-authorise after the claim**, so a refusal is recorded against a row that really started and
   ``QUEUED -> FAILED`` is a legal edge. The requester, their permission, the application, the
   Jenkins configuration, the target, **its digest**, the ref and the approval are read again; the
   request's own checks are not trusted (SECURITY.md §22). A refusal sends nothing to Jenkins.
3. **Trigger once; never retry.** A failure that definitely did not queue anything is a terminal
   ``FAILED``. One that *may* have is left ``QUEUED`` with no queue id, and the poller looks for
   the build by DCP's own ``deployment_id``; it does not trigger again (CLAUDE.md §5).
4. **Persist the queue id before anything polls** (AD-08), in its own committed statement.

Nothing here builds a URL or a command: the client takes a registered job path and the rendered
parameters (:func:`app.deployments.template.render_parameters`), and nothing else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any, Final

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.authz.matcher import Target, permits
from app.authz.service import resolve_grants_sync
from app.core.config import get_settings
from app.core.logging import get_logger
from app.deployments import codes, schedule, states
from app.deployments.approval import approval_required
from app.deployments.digest import target_digest
from app.deployments.jenkins import JenkinsClient, JenkinsError
from app.deployments.models import Deployment, DeploymentTarget
from app.deployments.paths import InvalidJobPathError
from app.deployments.refs import ref_permitted
from app.deployments.store import poll_statement, transition_statement
from app.deployments.template import render_parameters
from app.deployments.worker_common import Dep, finish, now_utc, record, to_dep
from app.inventory.models import Application

logger = get_logger(__name__)

#: What ``sanitized_result.trigger`` says about the call to Jenkins (closed values).
ATTEMPTED: Final = "attempted"
SENT: Final = "sent"
NOT_SENT: Final = "not_sent"
NOT_ATTEMPTED: Final = "not_attempted"
OUTCOME_UNKNOWN: Final = "outcome_unknown"


@dataclass(frozen=True)
class _Ready:
    pipeline: str
    template: dict[str, Any]


def _claim(engine: Engine, deployment_id: uuid.UUID) -> tuple[Dep | None, str]:
    """Load, check the deadline, claim. ``(dep, "")`` on success, ``(None, outcome)`` otherwise."""
    with Session(engine) as session:
        row = session.get(Deployment, deployment_id)
        if row is None or row.execution_status not in states.CLAIMABLE:
            return None, "not_claimable"
        dep = to_dep(row)
    if now_utc() >= dep.deadline_at:
        return None, finish(
            engine,
            dep,
            current=dep.status,
            new=states.FAILED,
            failure_reason=codes.EXPIRED_BEFORE_START,
            result={"trigger": NOT_ATTEMPTED},
        )
    claimed_at = now_utc()
    with Session(engine) as session:
        won = session.execute(
            transition_statement(
                dep.id,
                dep.status,
                states.QUEUED,
                result={"trigger": ATTEMPTED, "claimed_at": claimed_at.isoformat()},
                poll_after_seconds=schedule.RECONCILE_AFTER_SECONDS,
            )
        ).first()
        if won is None:
            session.rollback()
            return None, "lost_claim"
        record(session, dep, "DEPLOYMENT_STARTED", "SUCCESS", {"from": dep.status})
        session.commit()
    return replace(dep, status=states.QUEUED, claimed_at=claimed_at), ""


def _recheck(engine: Engine, dep: Dep) -> _Ready | str:
    """Everything that must still be true. Any doubt is a failure reason (fail closed)."""
    with Session(engine) as session:
        user = session.get(User, dep.requested_by)
        if user is None or user.status != "ACTIVE":
            return codes.PERMISSION_REVOKED
        allowed = permits(
            resolve_grants_sync(session, user.id),
            "deployment",
            "create",
            Target(environment=dep.environment, application=str(dep.application_id)),
        )
        if not allowed.allowed:
            return codes.PERMISSION_REVOKED
        application = session.get(Application, dep.application_id)
        if application is None or application.deleted_at is not None:
            return codes.TARGET_VALIDATION_FAILED
        settings = get_settings()
        if settings.demo_mode or settings.jenkins_url is None:
            return codes.NOT_CONFIGURED
        target = session.get(DeploymentTarget, dep.target_id)
        if (
            target is None
            or target.deleted_at is not None
            or target.status != "ACTIVE"
            or target.application_id != dep.application_id
            or target.environment != dep.environment
        ):
            return codes.TARGET_VALIDATION_FAILED
        template = dict(target.parameter_template)
        # An edit between the request and now: Jenkins would run something its requester never saw.
        if target_digest(target.pipeline_reference, template) != dep.target_digest:
            return codes.TARGET_CHANGED
        if not ref_permitted(list(target.allowed_ref_patterns), dep.requested_ref):
            return codes.REF_NOT_PERMITTED
        # No approval can be given before M5.4, so a row that needs one is refused whatever its
        # ``approval_status`` says: a flag nothing legitimate can set is not evidence. M5.4 replaces
        # this with a check of the approvals row and its decider's standing (mandatory rule 3).
        if approval_required(
            environment=dep.environment, policy_linked=target.approval_policy_id is not None
        ):
            return codes.APPROVAL_REQUIRED
        return _Ready(target.pipeline_reference, template)


def _remember_unknown(engine: Engine, dep: Dep) -> None:
    """The trigger may have been sent: say so, and leave the looking to the poller."""
    with Session(engine) as session:
        session.execute(
            poll_statement(
                dep.id,
                states.QUEUED,
                delay_seconds=schedule.RECONCILE_AFTER_SECONDS,
                result={"trigger": OUTCOME_UNKNOWN},
            )
        )
        session.commit()


def run(engine: Engine, deployment_id: uuid.UUID, *, client: JenkinsClient | None = None) -> str:
    """Claim a deployment, re-check it and trigger its pipeline **once**. Returns the outcome."""
    dep, outcome = _claim(engine, deployment_id)
    if dep is None:
        return outcome

    ready = _recheck(engine, dep)
    if isinstance(ready, str):
        return finish(
            engine,
            dep,
            current=states.QUEUED,
            new=states.FAILED,
            failure_reason=ready,
            result={"trigger": NOT_ATTEMPTED},
            refused=True,
        )

    try:
        parameters = render_parameters(
            ready.template,
            ref=dep.requested_ref,
            deployment_id=dep.id,
            environment=dep.environment,
            requested_by=dep.requested_by,
        )
    except ValueError:
        return finish(
            engine,
            dep,
            current=states.QUEUED,
            new=states.FAILED,
            failure_reason=codes.TARGET_VALIDATION_FAILED,
            result={"trigger": NOT_ATTEMPTED},
            refused=True,
        )

    jenkins = client or JenkinsClient()
    try:
        queued = jenkins.trigger(ready.pipeline, parameters)
    except InvalidJobPathError:
        return finish(
            engine,
            dep,
            current=states.QUEUED,
            new=states.FAILED,
            failure_reason=codes.TARGET_VALIDATION_FAILED,
            result={"trigger": NOT_ATTEMPTED},
            refused=True,
        )
    except JenkinsError as error:
        failure = codes.trigger_failure(error.reason, may_have_acted=error.may_have_acted)
        if failure is not None:
            return finish(
                engine,
                dep,
                current=states.QUEUED,
                new=states.FAILED,
                failure_reason=failure,
                result={"trigger": NOT_SENT},
            )
        _remember_unknown(engine, dep)
        return "reconcile"
    except Exception:
        # An unexpected failure around a state-changing call is ambiguous: the safe side is to
        # look for the build, never to send it again.
        logger.exception("deployment_trigger_unexpected_error", deployment_id=str(dep.id))
        _remember_unknown(engine, dep)
        return "reconcile"

    with Session(engine) as session:
        recorded = session.execute(
            poll_statement(
                dep.id,
                states.QUEUED,
                delay_seconds=schedule.FIRST_POLL_SECONDS,
                queue_item_id=queued.queue_id,
                result={"trigger": SENT},
            )
        ).first()
        session.commit()
    if recorded is None:
        # The reaper (or a reconciler) moved the row while the call was in flight. The build exists.
        logger.warning("deployment_queue_item_not_recorded", deployment_id=str(dep.id))
        return "superseded"
    return "QUEUED:sent"
