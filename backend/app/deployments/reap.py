"""The deadline, and the healer (``OPEN-136``).

Runs on ``maintenance`` every 30 s, so a saturated ``deployments`` queue cannot stop the thing that
frees a stuck application's one slot. **It never calls Jenkins**:

* a ``QUEUED`` or ``RUNNING`` deployment past its deadline ends ``FAILED / DEPLOYMENT_TIMED_OUT``
  with ``reconciliation_required`` and ``build_may_still_be_running`` -- DCP cannot stop a Jenkins
  build and says so, rather than implying it did;
* a ``REQUESTED`` or ``APPROVED`` one nobody claimed before its deadline ends
  ``FAILED / EXPIRED_BEFORE_START`` and nothing was sent;
* an unclaimed one still inside its deadline, older than the healer age, is handed to the queue
  again (the enqueue after the request's commit is best effort). Safe to repeat: the claim is
  guarded, so a second delivery exits having done nothing.

``AWAITING_APPROVAL`` is not touched: its window is M5.4's.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.deployments import codes, schedule, states
from app.deployments.models import Deployment
from app.deployments.worker_common import Dep, finish, now_utc, to_dep


def reap(
    engine: Engine,
    *,
    enqueue: Callable[[uuid.UUID], None],
    healer_age_seconds: int = schedule.HEALER_AGE_SECONDS,
) -> dict[str, int]:
    timed_out = expired = requeued = 0
    with Session(engine) as session:
        overdue: list[Dep] = [
            to_dep(row)
            for row in session.execute(
                select(Deployment).where(
                    Deployment.execution_status.in_((*states.POLLED, *states.CLAIMABLE)),
                    Deployment.deadline_at < func.now(),
                )
            ).scalars()
        ]
        waiting = list(
            session.execute(
                select(Deployment.id).where(
                    Deployment.execution_status.in_(states.CLAIMABLE),
                    Deployment.deadline_at >= func.now(),
                    Deployment.created_at < now_utc() - timedelta(seconds=healer_age_seconds),
                )
            ).scalars()
        )
    for dep in overdue:
        if dep.status in states.POLLED:
            outcome = finish(
                engine,
                dep,
                current=dep.status,
                new=states.FAILED,
                failure_reason=codes.DEPLOYMENT_TIMED_OUT,
                result={"build_may_still_be_running": True},
                phase="reaper",
            )
            timed_out += outcome.startswith("FAILED")
        else:
            outcome = finish(
                engine,
                dep,
                current=dep.status,
                new=states.FAILED,
                failure_reason=codes.EXPIRED_BEFORE_START,
                result={"trigger": "not_attempted"},
                phase="reaper",
            )
            expired += outcome.startswith("FAILED")
    for deployment_id in waiting:
        enqueue(deployment_id)
        requeued += 1
    return {"timed_out": timed_out, "expired_before_start": expired, "requeued": requeued}
