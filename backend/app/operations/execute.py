"""Running an operation: claim, re-authorise, invoke, poll, verify (``OPEN-70`` - ``OPEN-72``).

Synchronous, because workers and ``boto3`` are. The Celery task is a thin wrapper
(``app/workers/tasks/operations.py``); everything that matters is here so a test can drive it with
a real database, a scripted adapter and a clock it controls.

The order, and why it is this order:

1. **Load by id and claim with a guarded ``UPDATE``.** A second delivery, or a second worker, finds
   the row no longer ``PENDING`` / ``APPROVED`` and exits having done nothing.
2. **Re-authorise after the claim**, so a refusal is recorded against a row that really started and
   ``RUNNING -> FAILED`` is a legal edge. Permission, approval, target and account are read again;
   nothing from the request is trusted (SECURITY.md §14, §22). A refusal makes **zero** provider
   calls to change anything.
3. **Pre-flight reads** confirm the provider still knows the host and the unit, and give
   ``observed_state_before``.
4. **Invoke**, then **persist the provider's id in its own committed transaction before polling**
   (AD-08). A state-changing call is never retried; anything ambiguous is flagged
   ``reconciliation_required`` and left to a person (CLAUDE.md §5).
5. **Poll, then verify with a read.** ``SUCCEEDED`` is written only when the observed state is the
   one the action was meant to produce (CLAUDE.md §6; mandatory rule 8).

Nothing in this module builds a command: the adapter takes a ``ServerRef``, a ``ServiceRef`` and the
closed action enum, and nothing else.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.approvals.models import ApprovalPolicy
from app.audit import service as audit
from app.auth.models import User
from app.authz.matcher import Target, permits
from app.authz.service import resolve_grants_sync
from app.core.logging import get_logger
from app.inventory.models import CloudAccount, Server, Service, ServiceActionPolicy
from app.operations import alerting, codes, states
from app.operations.evaluator import ApprovalPolicyView, approval_required
from app.operations.models import Operation
from app.operations.store import (
    heartbeat_statement,
    record_handle_statement,
    resume_claim_statement,
    transition_statement,
)
from app.providers import registry
from app.providers.base import ProviderAdapter
from app.providers.capabilities import ensure_supported
from app.providers.errors import (
    AuthenticationError,
    AuthorizationError,
    NotConfigured,
    NotFound,
    PartialFailure,
    ProviderError,
    ProviderUnavailable,
    Throttled,
    Timeout,
    UnsupportedOperation,
)
from app.providers.types import (
    ExecutionHandle,
    ExecutionStatus,
    ServerRef,
    ServiceAction,
    ServiceRef,
)

logger = get_logger(__name__)

ACTOR_LABEL: Final = "system:operations-worker"
#: Working defaults, not measurements (``OPEN-70``).
POLL_DELAYS_SECONDS: Final[tuple[float, ...]] = (1.0, 2.0, 4.0, 5.0)
VERIFY_READS: Final = 3
VERIFY_INTERVAL_SECONDS: Final = 2.0
#: Consecutive failed *reads* while polling before the outcome is called unknown.
MAX_POLL_ERRORS: Final = 3
#: An unclaimed row older than this is handed to the queue again (``OPEN-70``).
HEALER_AGE_SECONDS: Final = 30
#: A `RUNNING` row with a handle whose worker has been quiet this long is resumed
#: (`OPEN-84`). Longer than a poll, because one provider read may itself take close to a
#: minute (botocore retries a read up to five times); a working default, not a measurement.
RESUME_STALE_SECONDS: Final = 120

_CODE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SETTLING: Final = frozenset({"ACTIVATING", "DEACTIVATING"})

#: What a provider error means for a state-changing call (``OPEN-71``).
_SUBMIT_REASON: Final[dict[type[ProviderError], str]] = {
    NotConfigured: codes.NOT_CONFIGURED,
    UnsupportedOperation: codes.UNSUPPORTED_OPERATION,
    AuthenticationError: codes.PROVIDER_AUTHENTICATION,
    AuthorizationError: codes.PROVIDER_AUTHORIZATION,
    NotFound: codes.PROVIDER_NOT_FOUND,
    Throttled: codes.PROVIDER_THROTTLED,
    Timeout: codes.PROVIDER_TIMEOUT,
    ProviderUnavailable: codes.PROVIDER_UNAVAILABLE,
    PartialFailure: codes.PROVIDER_PARTIAL_FAILURE,
}


@dataclass(frozen=True)
class _Op:
    """The operation as plain values: no ORM object lives across a provider call."""

    id: uuid.UUID
    action: str
    environment: str
    requested_by: uuid.UUID
    approval_status: str
    deadline_at: datetime
    provider: str
    correlation_id: uuid.UUID
    server_id: uuid.UUID
    service_id: uuid.UUID | None
    status: str
    handle: str | None = None
    before: str | None = None
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Ready:
    adapter: ProviderAdapter
    ref: ServerRef
    service: ServiceRef
    before: str | None


@dataclass(frozen=True)
class _Refusal:
    reason: str
    detail: str | None = None


def _op(row: Operation) -> _Op:
    return _Op(
        row.id,
        row.action_type,
        row.environment,
        row.requested_by,
        row.approval_status,
        row.deadline_at,
        row.provider,
        row.correlation_id,
        row.target_server_id,
        row.target_service_id,
        row.execution_status,
        row.external_execution_id,
        row.observed_state_before,
        row.started_at,
        row.heartbeat_at,
        tuple((row.sanitized_result or {}).get("steps", ())),
    )


def _audit(
    session: Session, op: _Op, action_type: str, outcome: str, metadata: dict[str, Any]
) -> None:
    """Attributed to the requester who started it, by the named system actor that carried it out."""
    audit.record(
        session,
        action_type=action_type,
        outcome=outcome,
        actor_id=op.requested_by,
        actor_label=ACTOR_LABEL,
        target_type="operation",
        target_id=op.id,
        environment=op.environment,
        correlation_id=op.correlation_id,
        metadata={"on_behalf_of": str(op.requested_by), "action": op.action, **metadata},
    )


def _closed(code: str | None) -> str | None:
    """A reason code from an adapter is kept only if it is a closed code, never free text."""
    return code if code is not None and _CODE.fullmatch(code) else None


def _alert(session: Session, op: _Op, new: str, reason: str | None) -> None:
    """The failure alert follows the terminal state, in the same transaction (``OPEN-89``)."""
    if new in (states.FAILED, states.TIMED_OUT):
        alerting.raise_failure(
            session,
            operation_id=op.id,
            server_id=op.server_id,
            service_id=op.service_id,
            environment=op.environment,
            action=op.action,
            status=new,
            reason=reason or "UNKNOWN",
        )
    elif new == states.SUCCEEDED:
        alerting.clear_failure(
            session, operation_id=op.id, server_id=op.server_id, service_id=op.service_id
        )


def _finish(
    engine: Engine,
    op: _Op,
    *,
    current: str,
    new: str,
    steps: list[str] | None,
    failure_reason: str | None = None,
    before: str | None = None,
    after: str | None = None,
    detail: str | None = None,
    refused: bool = False,
) -> str:
    """Write the terminal state and its audit rows together. ``superseded`` if the row moved on."""
    fields: dict[str, str] = {}
    if failure_reason is not None:
        fields["failure_reason"] = failure_reason
    if before is not None:
        fields["observed_state_before"] = before
    if after is not None:
        fields["observed_state_after"] = after
    result: dict[str, Any] = {} if steps is None else {"steps": steps}
    if (code := _closed(detail)) is not None:
        result["reason_code"] = code
    with Session(engine) as session:
        won = session.execute(
            transition_statement(op.id, current, new, action=op.action, result=result, **fields)
        ).first()
        if won is None:
            # The reaper (or another worker) already ended it. Our write must not resurrect it.
            session.rollback()
            logger.warning("operation_finish_superseded", operation_id=str(op.id))
            return "superseded"
        _alert(session, op, new, failure_reason)
        if refused:
            _audit(
                session,
                op,
                "SERVICE_OPERATION_DENIED",
                "DENIED",
                {"reason": failure_reason, "phase": "worker"},
            )
        _audit(
            session,
            op,
            "SERVICE_OPERATION_COMPLETED",
            "SUCCESS" if new == states.SUCCEEDED else "FAILURE",
            {"status": new, "failure_reason": failure_reason},
        )
        session.commit()
    logger.info("operation_finished", operation_id=str(op.id), status=new, reason=failure_reason)
    return new if failure_reason is None else f"{new}:{failure_reason}"


def _fail(
    engine: Engine,
    op: _Op,
    reason: str,
    steps: list[str],
    *,
    detail: str | None = None,
    before: str | None = None,
    refused: bool = False,
) -> str:
    return _finish(
        engine,
        op,
        current=states.RUNNING,
        new=codes.terminal_status_for(reason),
        steps=steps,
        failure_reason=reason,
        before=before,
        detail=detail,
        refused=refused,
    )


# --- 1. claim -------------------------------------------------------------------------------


def _claim(engine: Engine, operation_id: uuid.UUID) -> tuple[_Op | None, str]:
    """Load, check the deadline, claim. ``(op, "")`` on success, ``(None, outcome)`` otherwise."""
    with Session(engine) as session:
        row = session.get(Operation, operation_id)
        if row is None or row.execution_status not in states.CLAIMABLE:
            return None, "not_claimable"
        op = _op(row)
        if datetime.now(UTC) >= op.deadline_at:
            return None, _expire_unclaimed(session, op)
        if session.execute(transition_statement(op.id, op.status, states.RUNNING)).first() is None:
            session.rollback()
            return None, "lost_claim"
        _audit(session, op, "SERVICE_OPERATION_STARTED", "SUCCESS", {"provider": op.provider})
        session.commit()
    return op, ""


def _expire_unclaimed(session: Session, op: _Op) -> str:
    """``PENDING|APPROVED -> TIMED_OUT``: the deadline passed before anyone claimed it."""
    won = session.execute(
        transition_statement(
            op.id,
            op.status,
            states.TIMED_OUT,
            failure_reason=codes.EXPIRED_BEFORE_START,
            result={"steps": []},
        )
    ).first()
    if won is None:
        session.rollback()
        return "lost_claim"
    _alert(session, op, states.TIMED_OUT, codes.EXPIRED_BEFORE_START)
    _audit(
        session,
        op,
        "SERVICE_OPERATION_COMPLETED",
        "FAILURE",
        {"status": states.TIMED_OUT, "failure_reason": codes.EXPIRED_BEFORE_START},
    )
    session.commit()
    return f"TIMED_OUT:{codes.EXPIRED_BEFORE_START}"


# --- 2/3. re-authorise, validate, pre-flight reads ---------------------------------------------


def _prepare(engine: Engine, op: _Op, *, resume: bool = False) -> _Ready | _Refusal:
    """Everything that must still be true. Any doubt is a refusal (fail closed).

    A resumer runs every check except the pre-flight reads: the command is already running,
    ``observed_state_before`` is already stored, and nothing is about to be sent.
    """
    with Session(engine) as session:
        user = session.get(User, op.requested_by)
        server = session.get(Server, op.server_id)
        service = None if op.service_id is None else session.get(Service, op.service_id)
        account = None if server is None else session.get(CloudAccount, server.cloud_account_id)
        if user is None or user.status != "ACTIVE":
            return _Refusal(codes.PERMISSION_REVOKED)
        if (
            server is None
            or server.deleted_at is not None
            or server.status != "MANAGED"
            or server.environment != op.environment  # re-labelled since the request, either way
            or service is None
            or service.deleted_at is not None
            or service.server_id != server.id
            or account is None
            or account.deleted_at is not None
        ):
            return _Refusal(codes.TARGET_VALIDATION_FAILED)
        # The account's regional boundary (PRD §4.11). Provider-neutral: an account that
        # configures no `allowed_regions` has no regional boundary; one that does is enforced.
        allowed = (account.region_configuration or {}).get("allowed_regions")
        if allowed is not None and (not isinstance(allowed, list) or server.region not in allowed):
            return _Refusal(codes.TARGET_VALIDATION_FAILED, "region_not_allowed")

        target = Target(
            environment=op.environment,
            cloud_account=str(server.cloud_account_id),
            server=str(server.id),
        )
        if not permits(resolve_grants_sync(session, user.id), "service", op.action, target).allowed:
            return _Refusal(codes.PERMISSION_REVOKED)

        policy = (
            None
            if service.action_policy_id is None
            else session.get(ServiceActionPolicy, service.action_policy_id)
        )
        approval_policy = None
        if policy is not None and policy.approval_policy_id is not None:
            found = session.get(ApprovalPolicy, policy.approval_policy_id)
            if found is not None:
                approval_policy = ApprovalPolicyView(found.applies_to, found.environment)
        if (
            approval_required(
                environment=op.environment,
                action=op.action,
                policy_linked=policy is not None and policy.approval_policy_id is not None,
                policy=approval_policy,
            ).required
            and op.approval_status != "APPROVED"
        ):
            return _Refusal(codes.APPROVAL_REQUIRED)

        if account.status != "ACTIVE":
            return _Refusal(codes.ACCOUNT_DISABLED)
        if policy is None or op.action not in policy.allowed_actions:
            return _Refusal(codes.ACTION_NOT_ALLOWED)

        try:
            ref = ServerRef(
                provider_resource_id=server.provider_resource_id,
                account_reference=account.account_reference,
                management_method=server.management_method,
                region=server.region,
                credential_reference=account.credential_reference,
            )
            service_ref = ServiceRef(service.id, service.service_name)
        except ValueError:
            return _Refusal(codes.TARGET_VALIDATION_FAILED, "malformed_reference")
        provider = account.provider

    try:
        adapter = registry.resolve_adapter(provider)
        if adapter.demo:
            raise NotConfigured(adapter_id=adapter.adapter_id, detail_code="demonstration_mode")
        if adapter.adapter_id != op.provider:
            return _Refusal(codes.TARGET_VALIDATION_FAILED, "adapter_changed")
        ensure_supported(
            adapter.capabilities(), ServiceAction(op.action), adapter_id=adapter.adapter_id
        )
    except ProviderError as error:
        return _Refusal(
            _SUBMIT_REASON.get(type(error), codes.NOT_CONFIGURED), error.provider_error_class
        )

    if resume:
        return _Ready(adapter, ref, service_ref, op.before)

    # Provider-side identity (SECURITY §14.5): inventory can be stale, and acting on a recycled
    # identifier is how one restarts the wrong host. Reads only; nothing here changes anything.
    try:
        info = adapter.get_server_info(ref)
        units = adapter.list_managed_services(ref, service_ref)
    except ProviderError as error:
        return _Refusal(codes.TARGET_VALIDATION_FAILED, error.provider_error_class)
    if info.data_state != "ok" or info.value is None:
        return _Refusal(codes.TARGET_VALIDATION_FAILED, "identity_unavailable")
    if info.value.provider_resource_id != ref.provider_resource_id:
        return _Refusal(codes.TARGET_VALIDATION_FAILED, "identity_mismatch")
    if units.data_state != "ok" or units.value is None:
        return _Refusal(codes.TARGET_VALIDATION_FAILED, "state_unavailable")
    known = {unit.unit_name: unit.state for unit in units.value}
    if service_ref.unit_name not in known:
        return _Refusal(codes.TARGET_VALIDATION_FAILED, "unit_not_on_host")
    return _Ready(adapter, ref, service_ref, known[service_ref.unit_name])


# --- 4/5. invoke, poll, verify -----------------------------------------------------------------


def _submit(engine: Engine, op: _Op, ready: _Ready, steps: list[str]) -> ExecutionHandle | str:
    """Invoke once. Returns the handle, or the outcome after ending the row."""
    try:
        handle = ready.adapter.execute_operation(
            ready.ref, ready.service, ServiceAction(op.action), operation_id=op.id
        )
    except ProviderError as error:
        return _fail(
            engine,
            op,
            _SUBMIT_REASON.get(type(error), codes.PROVIDER_UNAVAILABLE),
            steps,
            detail=error.provider_error_class,
            before=ready.before,
        )
    except Exception as error:
        # A state-changing call that raised something we do not know: it may have acted.
        logger.error(
            "operation_submit_unexpected", operation_id=str(op.id), error=type(error).__name__
        )
        return _fail(
            engine,
            op,
            codes.PROVIDER_UNAVAILABLE,
            steps,
            detail="unexpected_error",
            before=ready.before,
        )
    steps.append("submitted")
    with Session(engine) as session:
        recorded = session.execute(
            record_handle_statement(
                op.id,
                handle.external_execution_id,
                observed_state_before=ready.before,
                steps=steps,
            )
        ).first()
        if recorded is None:
            session.rollback()
            logger.error("operation_handle_not_recorded", operation_id=str(op.id))
            return "superseded"
        session.commit()
    return handle


def _beat(engine: Engine, op: _Op) -> None:
    """Say "still here" (`OPEN-84`). A failure to write it is logged and never stops the work."""
    try:
        with Session(engine) as session:
            session.execute(heartbeat_statement(op.id))
            session.commit()
    except Exception as error:
        logger.warning(
            "operation_heartbeat_failed", operation_id=str(op.id), error=type(error).__name__
        )


def _status(
    adapter: ProviderAdapter, ref: ServerRef, handle: ExecutionHandle
) -> tuple[ExecutionStatus | None, str]:
    """``(status, "")``, or ``(None, why)`` when the read failed. A failed read is not a result."""
    try:
        result = adapter.get_operation_status(ref, handle)
    except ProviderError as error:
        return None, error.provider_error_class
    if result.data_state != "ok" or result.value is None:
        return None, "status_unavailable"
    return result.value, ""


def _poll(
    engine: Engine,
    op: _Op,
    ready: _Ready,
    handle: ExecutionHandle,
    steps: list[str],
    sleep: Callable[[float], None],
) -> str | None:
    """Wait for the provider to finish. ``None`` means it reports ``COMPLETED``; otherwise the
    row has been ended and this is the outcome."""
    errors = 0
    attempt = 0
    while True:
        _beat(engine, op)
        if datetime.now(UTC) >= op.deadline_at:
            return _fail(engine, op, codes.DEADLINE_EXCEEDED, steps, before=ready.before)
        status, why = _status(ready.adapter, ready.ref, handle)
        if status is None:
            errors += 1
            if errors >= MAX_POLL_ERRORS:
                # Reads may be repeated; after the cap the outcome is unknown, so it is flagged.
                reason = codes.PROVIDER_TIMEOUT if why == "timeout" else codes.PROVIDER_UNAVAILABLE
                return _fail(engine, op, reason, steps, detail=why, before=ready.before)
        else:
            errors = 0
            if status.state == "COMPLETED":
                steps.append("completed")
                return None
            if status.state in ("FAILED", "CANCELLED"):
                return _fail(
                    engine,
                    op,
                    codes.PROVIDER_REPORTED_FAILURE,
                    steps,
                    detail=status.reason_code,
                    before=ready.before,
                )
        sleep(POLL_DELAYS_SECONDS[min(attempt, len(POLL_DELAYS_SECONDS) - 1)])
        attempt += 1


def _verify(
    engine: Engine, op: _Op, ready: _Ready, steps: list[str], sleep: Callable[[float], None]
) -> str:
    """Read the state back. Only a match is ``SUCCEEDED``; the schema's CHECK is the second guard.

    A few reads, a couple of seconds apart, let ``ACTIVATING`` / ``DEACTIVATING`` settle. A read
    that is not ``ok`` -- including ``stale`` -- cannot prove an end state.
    """
    observed: str | None = None
    for attempt in range(VERIFY_READS):
        _beat(engine, op)
        if datetime.now(UTC) >= op.deadline_at:
            return _fail(engine, op, codes.DEADLINE_EXCEEDED, steps, before=ready.before)
        try:
            snapshot = ready.adapter.list_managed_services(ready.ref, ready.service)
        except ProviderError:
            snapshot = None
        if snapshot is not None and snapshot.data_state == "ok" and snapshot.value is not None:
            observed = {u.unit_name: u.state for u in snapshot.value}.get(ready.service.unit_name)
            if observed is not None and states.verifies(op.action, observed):
                steps.append("verified")
                return _finish(
                    engine,
                    op,
                    current=states.VERIFYING,
                    new=states.SUCCEEDED,
                    steps=steps,
                    before=ready.before,
                    after=observed,
                )
        if attempt < VERIFY_READS - 1:
            sleep(VERIFY_INTERVAL_SECONDS)
    if observed is None:
        return _fail(engine, op, codes.VERIFICATION_UNAVAILABLE, steps, before=ready.before)
    return _finish(
        engine,
        op,
        current=states.RUNNING,
        new=states.FAILED,
        steps=steps,
        failure_reason=codes.VERIFICATION_MISMATCH,
        before=ready.before,
        after=observed,
    )


def run(
    engine: Engine, operation_id: uuid.UUID, *, sleep: Callable[[float], None] = time.sleep
) -> str:
    """Carry out one operation. Returns a short outcome label for the task result and the tests."""
    op, outcome = _claim(engine, operation_id)
    if op is None:
        return outcome
    steps = ["claimed"]

    prepared = _prepare(engine, op)
    if isinstance(prepared, _Refusal):
        return _fail(engine, op, prepared.reason, steps, detail=prepared.detail, refused=True)
    steps.append("validated")

    handle = _submit(engine, op, prepared, steps)
    if isinstance(handle, str):
        return handle

    ended = _poll(engine, op, prepared, handle, steps, sleep)
    if ended is not None:
        return ended
    return _verify(engine, op, prepared, steps, sleep)


# --- resume ---------------------------------------------------------------------------------


def _claim_resume(engine: Engine, operation_id: uuid.UUID) -> tuple[_Op | None, str]:
    """Take over a quiet ``RUNNING`` row, or say why not. One statement decides (``OPEN-84``)."""
    with Session(engine) as session:
        row = session.get(Operation, operation_id)
        if (
            row is None
            or row.execution_status != states.RUNNING
            or row.external_execution_id is None
        ):
            return None, "not_resumable"
        op = _op(row)
        if datetime.now(UTC) >= op.deadline_at:
            return None, "past_deadline"  # the reaper ends it; a resumer must not start polling
        steps = [*op.steps, "resumed"]
        won = session.execute(
            resume_claim_statement(
                op.id, op.heartbeat_at, stale_after_seconds=RESUME_STALE_SECONDS, steps=steps
            )
        ).first()
        if won is None:
            session.rollback()
            return None, "lost_claim"
        _audit(session, op, "SERVICE_OPERATION_RESUMED", "SUCCESS", {"provider": op.provider})
        session.commit()
    return op, ""


def resume(
    engine: Engine, operation_id: uuid.UUID, *, sleep: Callable[[float], None] = time.sleep
) -> str:
    """Continue an operation whose worker died after the provider accepted the command.

    Never calls ``execute_operation``: the command is already running and sending it again is
    exactly the duplicate this design exists to prevent. It re-checks everything a worker
    checks (the permission may have been revoked meanwhile), then polls from the persisted
    handle and verifies. A re-check that refuses ends the row ``FAILED`` / ``RESUME_REFUSED``
    **flagged for reconciliation**, because a command may still be running that nobody follows.
    """
    op, outcome = _claim_resume(engine, operation_id)
    if op is None:
        return outcome
    if op.handle is None:  # the claim statement requires one; narrowing for the type checker
        return "not_resumable"
    steps = [*op.steps, "resumed"]

    prepared = _prepare(engine, op, resume=True)
    if isinstance(prepared, _Refusal):
        return _fail(
            engine,
            op,
            codes.RESUME_REFUSED,
            steps,
            detail=prepared.reason.lower(),
            before=op.before,
            refused=True,
        )
    handle = ExecutionHandle(
        prepared.adapter.adapter_id, op.handle, op.started_at or datetime.now(UTC)
    )
    ended = _poll(engine, op, prepared, handle, steps, sleep)
    if ended is not None:
        return ended
    return _verify(engine, op, prepared, steps, sleep)


# --- the reaper and the healer ------------------------------------------------------------------


def reap(
    engine: Engine,
    *,
    enqueue: Callable[[uuid.UUID], None],
    enqueue_resume: Callable[[uuid.UUID], None] | None = None,
    healer_age_seconds: int = HEALER_AGE_SECONDS,
    resume_after_seconds: int = RESUME_STALE_SECONDS,
) -> dict[str, int]:
    """Time out what ran out of time, and hand unclaimed rows to the queue again (``OPEN-70``).

    Never calls a provider: a row whose worker died is ended as ``TIMED_OUT`` with
    ``reconciliation_required``, and the handle it recorded is kept for a person. The enqueue is
    safe to repeat because the claim is guarded.
    """
    timed_out = expired = requeued = 0
    with Session(engine) as session:
        overdue = [
            _op(row)
            for row in session.execute(
                select(Operation).where(
                    Operation.execution_status.in_((states.RUNNING, *states.CLAIMABLE)),
                    Operation.deadline_at < func.now(),
                )
            ).scalars()
        ]
        waiting = [
            row.id
            for row in session.execute(
                select(Operation).where(
                    Operation.execution_status.in_(states.CLAIMABLE),
                    Operation.deadline_at >= func.now(),
                    Operation.created_at
                    < datetime.now(UTC) - timedelta(seconds=healer_age_seconds),
                )
            ).scalars()
        ]
    stalled: list[uuid.UUID] = []
    if enqueue_resume is not None:
        with Session(engine) as session:
            stalled = list(
                session.execute(
                    select(Operation.id).where(
                        Operation.execution_status == states.RUNNING,
                        Operation.external_execution_id.is_not(None),
                        Operation.deadline_at >= func.now(),
                        func.coalesce(Operation.heartbeat_at, Operation.started_at)
                        < func.now() - timedelta(seconds=resume_after_seconds),
                    )
                ).scalars()
            )
    for op in overdue:
        if op.status == states.RUNNING:
            outcome = _finish(
                engine,
                op,
                current=states.RUNNING,
                new=states.TIMED_OUT,
                steps=None,
                failure_reason=codes.DEADLINE_EXCEEDED,
            )
            timed_out += outcome.startswith("TIMED_OUT")
        else:
            with Session(engine) as session:
                result = _expire_unclaimed(session, op)
            expired += result.startswith("TIMED_OUT")
    for operation_id in waiting:
        enqueue(operation_id)
        requeued += 1
    if enqueue_resume is not None:
        for operation_id in stalled:
            enqueue_resume(operation_id)
    return {
        "timed_out": timed_out,
        "expired_before_start": expired,
        "requeued": requeued,
        "resume_requeued": len(stalled),
    }
