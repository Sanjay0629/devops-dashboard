"""Following a deployment: lease the due rows, ask Jenkins, move the row (``OPEN-136``/``137``).

**Polling is a table, not a task per deployment.** :func:`lease_due` takes the rows whose
``next_poll_at`` has come, with ``FOR UPDATE SKIP LOCKED``, and pushes their ``next_poll_at`` a
lease ahead in the same transaction. A worker killed mid-poll therefore loses nothing: its lease
expires and the row is polled again. **Nothing here can trigger a build** -- the trigger is
reached only through the claim in :mod:`app.deployments.execute` -- so resuming never duplicates
one.

One step per call, by what Jenkins says:

* ``QUEUED`` with a queue id: waiting stays; **started** records the build number and moves to
  ``RUNNING``; **cancelled** (somebody cancelled it *in Jenkins*) ends ``CANCELLED``; **gone**
  (Jenkins forgot the item) is looked for by ``deployment_id`` and otherwise ends
  ``QUEUE_ITEM_LOST``.
* ``QUEUED`` with **no** queue id: the trigger may have been sent. Wait for a live worker to record
  its answer, then look by ``deployment_id``; never trigger again.
* ``RUNNING``: building stays; a result ends the row. **Only a pipeline ``SUCCESS`` succeeds**
  (:func:`app.deployments.policy.success`), and a build that is neither building nor carrying a
  result is an anomaly counted as a failed poll, not a verdict.

A failed read changes nothing but the schedule and the *closed* reason of the last failure, so an
outage shows on the page and never reads as a result. The deadline is the reaper's; a row past it is
left alone here.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import Engine, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.deployments import codes, policy, schedule, states
from app.deployments.jenkins import BuildState, JenkinsClient, JenkinsError, Located
from app.deployments.models import Deployment, DeploymentTarget
from app.deployments.store import poll_statement, transition_statement
from app.deployments.worker_common import Dep, finish, now_utc, record, to_dep

logger = get_logger(__name__)


def lease_due(
    engine: Engine,
    *,
    batch: int = schedule.BATCH,
    lease_seconds: int = schedule.LEASE_SECONDS,
) -> list[uuid.UUID]:
    """Take up to ``batch`` rows that are due and hold them for ``lease_seconds``.

    A row in flight with no ``next_poll_at`` at all is due: nothing is allowed to be forgotten
    because a column is empty.
    """
    with Session(engine) as session:
        ids = list(
            session.execute(
                select(Deployment.id)
                .where(
                    Deployment.execution_status.in_(states.POLLED),
                    or_(Deployment.next_poll_at.is_(None), Deployment.next_poll_at <= func.now()),
                )
                .order_by(Deployment.next_poll_at.asc().nulls_first(), Deployment.id)
                .limit(batch)
                .with_for_update(skip_locked=True)
            ).scalars()
        )
        if ids:
            session.execute(
                update(Deployment)
                .where(Deployment.id.in_(ids))
                .values(next_poll_at=func.now() + timedelta(seconds=lease_seconds))
            )
        session.commit()
    return ids


# --- the writes ------------------------------------------------------------------------------


def _reschedule(engine: Engine, dep: Dep, delay: int) -> str:
    """A good look that changes nothing: look again in ``delay`` seconds, with a clean count."""
    with Session(engine) as session:
        session.execute(poll_statement(dep.id, dep.status, delay_seconds=delay))
        session.commit()
    return f"{dep.status}:waiting"


def _read_failed(engine: Engine, dep: Dep, reason: str) -> str:
    """A read failed. Back off, remember the closed reason, change nothing else."""
    with Session(engine) as session:
        session.execute(
            poll_statement(
                dep.id,
                dep.status,
                delay_seconds=schedule.poll_delay(dep.poll_attempts + 1),
                error=reason,
                failed=True,
            )
        )
        session.commit()
    logger.warning("deployment_poll_failed", deployment_id=str(dep.id), reason=reason)
    return f"{dep.status}:poll_failed"


def _to_running(engine: Engine, dep: Dep, number: int) -> str:
    """Jenkins assigned a build number. Persisted with the move, before anything polls the build."""
    with Session(engine) as session:
        won = session.execute(
            transition_statement(
                dep.id,
                states.QUEUED,
                states.RUNNING,
                external_execution_id=str(number),
                result={"trigger": "sent"},
                poll_after_seconds=schedule.FIRST_POLL_SECONDS,
            )
        ).first()
        if won is None:
            session.rollback()
            return "superseded"
        record(session, dep, "DEPLOYMENT_RUNNING", "SUCCESS", {"build": str(number)})
        session.commit()
    return "RUNNING"


def _complete(engine: Engine, dep: Dep, build: BuildState, result: str) -> str:
    """The build has a result. Success is the policy's call, not Jenkins' word alone."""
    if result == "SUCCESS":
        health = policy.none_configured()
        if policy.success(result, health):
            return finish(
                engine,
                dep,
                current=states.RUNNING,
                new=states.SUCCEEDED,
                pipeline_result=result,
                health_check_result=health,
                commit_sha=build.commit_sha,
            )
    reason = codes.failure_for_result(result) or codes.PIPELINE_FAILED
    return finish(
        engine,
        dep,
        current=states.RUNNING,
        new=states.FAILED,
        failure_reason=reason,
        pipeline_result=result,
        commit_sha=build.commit_sha,
    )


# --- reconciling an unknown trigger outcome --------------------------------------------------


def _slot_name(engine: Engine, dep: Dep) -> str | None:
    """The parameter the target fills from the deployment id, if its template has one."""
    with Session(engine) as session:
        target = session.get(DeploymentTarget, dep.target_id)
        if target is None:
            return None
        slots = (target.parameter_template or {}).get("slots", {})
    return next((n for n, source in slots.items() if source == "deployment_id"), None)


def _give_up(engine: Engine, dep: Dep, *, lost: bool) -> str:
    return finish(
        engine,
        dep,
        current=states.QUEUED,
        new=states.FAILED,
        failure_reason=codes.QUEUE_ITEM_LOST if lost else codes.TRIGGER_OUTCOME_UNKNOWN,
    )


def _reconcile(
    engine: Engine, client: JenkinsClient, dep: Dep, *, lost: bool, now: datetime
) -> str:
    """Find the build the trigger may have started. **Never** triggers: it only reads."""
    claimed = dep.claimed_at or dep.created_at
    age = (now - claimed).total_seconds()
    slot = _slot_name(engine, dep)
    action = schedule.reconcile_action(age_seconds=age, lost=lost, has_slot=slot is not None)
    if action == "wait":
        return _reschedule(engine, dep, max(1, math.ceil(schedule.RECONCILE_AFTER_SECONDS - age)))
    if action == "give_up" or slot is None:
        return _give_up(engine, dep, lost=lost)
    try:
        found: Located | None = client.find_by_parameter(dep.pipeline_reference, slot, str(dep.id))
    except JenkinsError as error:
        return _read_failed(engine, dep, error.reason)
    if found is None:
        if schedule.empty_look_is_final(age_seconds=age, lost=lost):
            return _give_up(engine, dep, lost=lost)
        return _reschedule(engine, dep, schedule.RECONCILE_RETRY_SECONDS)
    if found.kind == "build":
        return _to_running(engine, dep, found.number)
    if dep.queue_item_id is not None:
        return _reschedule(engine, dep, schedule.FIRST_POLL_SECONDS)  # already tracked
    with Session(engine) as session:
        recorded = session.execute(
            poll_statement(
                dep.id,
                states.QUEUED,
                delay_seconds=schedule.FIRST_POLL_SECONDS,
                queue_item_id=found.number,
                result={"trigger": "sent"},
            )
        ).first()
        session.commit()
    return "QUEUED:reconciled" if recorded is not None else "superseded"


# --- one look at one deployment --------------------------------------------------------------


def poll_one(
    engine: Engine,
    client: JenkinsClient,
    deployment_id: uuid.UUID,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> str:
    """Ask Jenkins about one deployment and move it by the answer. Reads only."""
    with Session(engine) as session:
        row = session.get(Deployment, deployment_id)
        if row is None or row.execution_status not in states.POLLED:
            return "not_polled"
        dep = to_dep(row)
    now = clock()
    if now >= dep.deadline_at:
        return "overdue"  # the reaper ends it; a late answer here would only contradict it

    try:
        if dep.status == states.QUEUED:
            if dep.queue_item_id is None:
                return _reconcile(engine, client, dep, lost=False, now=now)
            queue = client.queue_item(dep.queue_item_id)
            if queue.kind == "waiting":
                return _reschedule(engine, dep, schedule.poll_delay(0))
            if queue.kind == "started" and queue.build_number is not None:
                return _to_running(engine, dep, queue.build_number)
            if queue.kind == "cancelled":
                return finish(engine, dep, current=states.QUEUED, new=states.CANCELLED)
            return _reconcile(engine, client, dep, lost=True, now=now)  # gone

        number = dep.build_number
        if number is None:
            return _read_failed(engine, dep, "provider_error")
        build = client.build(dep.pipeline_reference, number)
        if build.building:
            return _reschedule(engine, dep, schedule.poll_delay(0))
        if build.result is None:
            # Not building and no result: Jenkins did not say. That is not a verdict.
            return _read_failed(engine, dep, "provider_error")
        return _complete(engine, dep, build, build.result)
    except JenkinsError as error:
        return _read_failed(engine, dep, error.reason)
