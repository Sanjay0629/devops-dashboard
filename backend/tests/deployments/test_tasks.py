"""The Celery wrappers' shape (``OPEN-136``): what they take, where they run, when they fire.

What the tasks *do* is ``tests/integration/test_deployments_worker.py``; this is only the wiring,
and the rule that nothing but an id travels through the broker (SECURITY.md §22).
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture
def celery_app(settings: Any) -> Iterator[Any]:
    """Imported inside the test: the Celery app reads its settings when it is first imported."""
    from app.workers.celery_app import celery_app as app

    yield app


@pytest.fixture
def tasks(celery_app: Any) -> Any:
    from app.workers.tasks import deployments

    return deployments


def test_the_three_tasks_are_registered_on_the_right_queues(celery_app: Any, tasks: Any) -> None:
    assert celery_app.tasks["dcp.deployments.execute"].queue == "deployments"
    assert celery_app.tasks["dcp.deployments.poll_due"].queue == "deployments"
    assert celery_app.tasks["dcp.deployments.reap"].queue == "maintenance", (
        "the reaper must not share a queue the deployments can saturate"
    )


def test_execute_takes_an_id_and_nothing_else(tasks: Any) -> None:
    """No command, URL, credential, parameter or domain object travels through the broker."""
    assert list(inspect.signature(tasks.execute_deployment).parameters) == ["deployment_id"]
    assert list(inspect.signature(tasks.poll_due).parameters) == []
    assert list(inspect.signature(tasks.reap_deployments).parameters) == []


@pytest.mark.parametrize("value", ["", "not-a-uuid", "1", "../../etc/passwd", "x" * 500, None, 7])
def test_a_bad_id_is_refused_before_the_database_is_touched(
    value: object, monkeypatch: pytest.MonkeyPatch, tasks: Any
) -> None:
    def forbidden() -> None:
        raise AssertionError("the database must not be opened for a malformed id")

    monkeypatch.setattr(tasks, "get_sync_engine", forbidden)
    assert tasks.execute_deployment(value) == {"outcome": "bad_id"}  # type: ignore[arg-type]


def test_the_poller_and_the_reaper_are_scheduled_and_the_poller_is_the_faster(
    celery_app: Any,
) -> None:
    schedule = celery_app.conf.beat_schedule
    poll = next(e for e in schedule.values() if e["task"] == "dcp.deployments.poll_due")
    reap = next(e for e in schedule.values() if e["task"] == "dcp.deployments.reap")
    assert poll["schedule"] == 10.0 and poll["options"] == {"queue": "deployments"}
    assert reap["schedule"] == 30.0 and reap["options"] == {"queue": "maintenance"}


def test_a_tasks_hard_limit_outlasts_its_soft_limit_and_a_trigger_is_short(celery_app: Any) -> None:
    execute = celery_app.tasks["dcp.deployments.execute"]
    assert execute.soft_time_limit < execute.time_limit <= 120, (
        "execute is one claim and one trigger"
    )
    poll = celery_app.tasks["dcp.deployments.poll_due"]
    assert poll.soft_time_limit < poll.time_limit


def test_every_task_finishes_well_inside_the_brokers_visibility_timeout(celery_app: Any) -> None:
    """A message must not be redelivered while its task still runs (a double execution).

    The deployment *deadline* (up to six hours) is a column the reaper reads, not a task that
    sleeps, so it is not the bound; the longest task is, and it is minutes.
    """
    from app.workers.celery_app import VISIBILITY_TIMEOUT_SECONDS

    for name in ("dcp.deployments.execute", "dcp.deployments.poll_due"):
        assert celery_app.tasks[name].time_limit * 10 < VISIBILITY_TIMEOUT_SECONDS, name
