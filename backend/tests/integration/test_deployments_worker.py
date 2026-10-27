"""The deployment worker against real PostgreSQL and an in-process mock Jenkins (``OPEN-136``).

``execute.run``, ``poll.poll_one``, ``poll.lease_due`` and ``reap.reap`` are driven directly; the
Celery wrappers are tested for their shape only. There is no real Jenkins: these tests prove the
state machine's lifecycle against a stand-in that can be told to fail in every way the worker has a
rule for. Passing them is not evidence that any Jenkins works.

What matters, in this order:

1. **A trigger is sent once.** Two deliveries of one message, two threads, a worker abandoned
   mid-lease, a response lost on the wire: the mock's request log shows one ``POST``.
2. **An unknown outcome is looked for, never repeated.** Found: followed. Not found: ended
   honestly, with ``reconciliation_required``.
3. **A re-check that fails sends nothing to Jenkins at all** -- not even the crumb request.
4. **A Jenkins outage leaves the row as it was**, with the closed reason of the last failure and a
   backing-off schedule, and recovers.
5. **Success needs a pipeline ``SUCCESS``**; every other result is a failure with its own code.
6. **A late answer cannot resurrect what the deadline ended.**
"""

# ruff: noqa: F811 -- fixtures imported by name are redefined as parameters, as pytest intends
from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from app.core.config import Settings, get_settings
from app.deployments import execute, policy, poll, reap
from app.deployments.digest import target_digest
from app.deployments.jenkins import BuildState
from tests.deployments.conftest import harness, mock, settings  # noqa: F401 -- used by name
from tests.deployments.harness import JOB, TOKEN, Harness
from tests.integration.inventory_helpers import FULL_SCOPE, audit_rows, grant, revoke_all
from tests.integration.test_alert_schema import a_user
from tests.integration.test_deployment_targets_schema import a_target, an_application

TEMPLATE = {
    "fixed": {"TARGET": "staging"},
    "slots": {"GIT_REF": "ref", "DCP_DEPLOYMENT_ID": "deployment_id"},
}
NO_SLOT_TEMPLATE = {"fixed": {"TARGET": "staging"}, "slots": {"GIT_REF": "ref"}}
BUILD_PATH = "/job/demo/job/hello-pipeline/1/api/json"


class LostResponse(httpx.BaseTransport):
    """The build is queued, and the answer never arrives."""

    def __init__(self, mock: Any, *, deliver: bool) -> None:
        self._inner = httpx.WSGITransport(app=mock)
        self._deliver = deliver
        self.posts = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/buildWithParameters"):
            self.posts += 1
            if self._deliver:
                self._inner.handle_request(request)
            raise httpx.ReadTimeout("lost", request=request)
        return self._inner.handle_request(request)


class Programmable(httpx.BaseTransport):
    """The mock, except where a test replaces one answer."""

    def __init__(self, mock: Any, override: Callable[[httpx.Request], Any]) -> None:
        self._inner = httpx.WSGITransport(app=mock)
        self._override = override

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        result = self._override(request)
        if isinstance(result, Exception):
            raise result
        return result if result is not None else self._inner.handle_request(request)


@dataclass
class World:
    engine: Engine
    harness: Harness
    mock: Any
    settings: Settings
    user: uuid.UUID
    application: uuid.UUID
    target: uuid.UUID
    template: dict[str, Any]

    # -- building ------------------------------------------------------------------------

    def use(self, transport: httpx.BaseTransport) -> Harness:
        """Run against a different transport (the mock is shared), keeping the settings."""
        self.harness = Harness(self.settings, self.mock, transport)
        return self.harness

    def new(self, status: str = "REQUESTED", **overrides: Any) -> uuid.UUID:
        """A deployment written straight to the table, as the API would have written it."""
        with self.engine.connect() as connection:
            reference, template = connection.execute(
                text(
                    "SELECT pipeline_reference, parameter_template FROM deployment_targets"
                    " WHERE id = :t"
                ),
                {"t": self.target},
            ).one()
        row: dict[str, Any] = {
            "id": uuid.uuid4(),
            "application_id": self.application,
            "target_id": self.target,
            "environment": "staging",
            "ref": "main",
            "user": self.user,
            "status": status,
            "approval": "NOT_REQUIRED",
            "key": uuid.uuid4().hex,
            "correlation": uuid.uuid4(),
            "digest": target_digest(reference, template),
            "deadline": "3600",
            "created": None,
            "build": "1" if status in ("RUNNING", "SUCCEEDED") else None,
            "queue": None,
            "failure": "PIPELINE_FAILED" if status == "FAILED" else None,
            "finished": status in ("SUCCEEDED", "FAILED", "CANCELLED", "ROLLED_BACK"),
            "health": json.dumps(policy.none_configured()) if status == "SUCCEEDED" else "{}",
        }
        row.update(overrides)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO deployments (id, application_id, deployment_target_id,"
                    " environment, pipeline_reference, requested_ref, requested_by,"
                    " approval_status, execution_status, external_execution_id, queue_item_id,"
                    " idempotency_key, correlation_id, target_digest, deadline_at, created_at,"
                    " failure_reason, completed_at, health_check_result)"
                    " VALUES (:id, :application_id, :target_id, :environment, :reference, :ref,"
                    " :user, :approval, :status, :build, :queue, :key, :correlation, :digest,"
                    " now() + make_interval(secs => CAST(:deadline AS integer)),"
                    " COALESCE(CAST(:created AS timestamptz), now()), :failure,"
                    " CASE WHEN :finished THEN now() END, CAST(:health AS jsonb))"
                ),
                {**row, "reference": reference},
            )
        return row["id"]  # type: ignore[no-any-return]

    # -- reading -------------------------------------------------------------------------

    def row(self, deployment_id: uuid.UUID) -> dict[str, Any]:
        with self.engine.connect() as connection:
            found = connection.execute(
                text("SELECT * FROM deployments WHERE id = :i"), {"i": deployment_id}
            ).mappings()
            return dict(found.one())

    def audit(self, deployment_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
        return [
            e for e in audit_rows(self.engine, action) if str(e["target_id"]) == str(deployment_id)
        ]

    def seconds_until_next_poll(self, deployment_id: uuid.UUID) -> float:
        with self.engine.connect() as connection:
            value = connection.execute(
                text(
                    "SELECT extract(epoch FROM next_poll_at - now()) FROM deployments WHERE id = :i"
                ),
                {"i": deployment_id},
            ).scalar_one()
        return float(value)

    def posts(self) -> list[dict[str, Any]]:
        return [
            r
            for r in self.mock.requests
            if r["method"] == "POST" and r["path"].endswith("/buildWithParameters")
        ]

    def asked(self, path: str) -> list[dict[str, Any]]:
        return [r for r in self.mock.requests if r["path"] == path]

    # -- acting --------------------------------------------------------------------------

    def run(self, deployment_id: uuid.UUID) -> str:
        return execute.run(self.engine, deployment_id, client=self.harness.client)

    def due(self, deployment_id: uuid.UUID) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE deployments SET next_poll_at = now() - interval '1 second'"
                    " WHERE id = :i"
                ),
                {"i": deployment_id},
            )

    def poll(self, deployment_id: uuid.UUID, clock: Callable[[], datetime] | None = None) -> str:
        kwargs = {} if clock is None else {"clock": clock}
        return poll.poll_one(self.engine, self.harness.client, deployment_id, **kwargs)

    def tick(self, deployment_id: uuid.UUID, clock: Callable[[], datetime] | None = None) -> str:
        self.due(deployment_id)
        return self.poll(deployment_id, clock)

    def drive(self, deployment_id: uuid.UUID, limit: int = 30) -> str:
        """Poll until the deployment is finished."""
        for _ in range(limit):
            self.tick(deployment_id)
            status = self.row(deployment_id)["execution_status"]
            if status in ("SUCCEEDED", "FAILED", "CANCELLED"):
                return str(status)
        raise AssertionError(f"still {self.row(deployment_id)['execution_status']} after {limit}")

    def claimed_at(self, deployment_id: uuid.UUID) -> datetime:
        return datetime.fromisoformat(self.row(deployment_id)["sanitized_result"]["claimed_at"])

    def later(self, deployment_id: uuid.UUID, seconds: float) -> Callable[[], datetime]:
        moment = self.claimed_at(deployment_id) + timedelta(seconds=seconds)
        return lambda: moment


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Settings:
    """Demonstration mode off (the suite's environment has it on), Jenkins configured."""
    monkeypatch.setenv("DCP_DEMO_MODE", "false")
    get_settings.cache_clear()
    return get_settings()


def _build_world(
    engine: Engine,
    harness: Harness,
    mock: Any,
    live: Settings,
    *,
    template: dict[str, Any],
    pipeline: str = JOB,
    environment: str = "staging",
) -> World:
    with engine.begin() as connection:
        user = a_user(connection)
        application = an_application(connection)
        target = a_target(
            connection,
            application,
            environment=environment,
            pipeline_reference=pipeline,
            parameter_template=json.dumps(template),
            patterns=["^main$"],
        )
    grant(engine, user, "DEVOPS_ENGINEER", FULL_SCOPE)
    return World(engine, harness, mock, live, user, application, target, template)


def _cleanup(engine: Engine, world: World) -> None:
    revoke_all(engine, world.user)
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM deployments WHERE application_id = :a"), {"a": world.application}
        )
        connection.execute(
            text("DELETE FROM deployment_targets WHERE application_id = :a"),
            {"a": world.application},
        )
        connection.execute(text("DELETE FROM applications WHERE id = :a"), {"a": world.application})


@pytest.fixture
def world(migrated: Engine, harness: Harness, mock: Any, live: Settings) -> Iterator[World]:
    built = _build_world(migrated, harness, mock, live, template=TEMPLATE)
    yield built
    _cleanup(migrated, built)


@pytest.fixture
def world_without_slot(
    migrated: Engine, harness: Harness, mock: Any, live: Settings
) -> Iterator[World]:
    built = _build_world(migrated, harness, mock, live, template=NO_SLOT_TEMPLATE)
    yield built
    _cleanup(migrated, built)


# --- the lifecycle ---------------------------------------------------------------------------


def test_a_deployment_is_claimed_triggered_followed_and_recorded(world: World) -> None:
    deployment = world.new()
    assert world.run(deployment) == "QUEUED:sent"

    row = world.row(deployment)
    assert row["execution_status"] == "QUEUED" and row["queue_item_id"] == 100
    assert row["external_execution_id"] is None and row["started_at"] is None
    assert row["sanitized_result"]["trigger"] == "sent"
    # Exactly one trigger, carrying exactly the rendered parameters and nothing a caller chose.
    [post] = world.posts()
    assert post["params"] == {
        "TARGET": "staging",
        "GIT_REF": "main",
        "DCP_DEPLOYMENT_ID": str(deployment),
    }
    assert post["authorised"] is True and post["crumb"] is True

    assert world.drive(deployment) == "SUCCEEDED"
    done = world.row(deployment)
    assert done["external_execution_id"] == "1" and done["queue_item_id"] == 100
    assert done["commit_sha"] == "0123456789abcdef0123456789abcdef01234567"
    assert done["health_check_result"] == policy.none_configured()
    assert done["sanitized_result"]["pipeline_result"] == "SUCCESS"
    assert done["failure_reason"] is None and done["last_poll_error"] is None
    assert done["started_at"] is not None and done["completed_at"] >= done["started_at"]
    assert done["next_poll_at"] is None, "a finished deployment is not polled"

    [started] = world.audit(deployment, "DEPLOYMENT_STARTED")
    [running] = world.audit(deployment, "DEPLOYMENT_RUNNING")
    [completed] = world.audit(deployment, "DEPLOYMENT_COMPLETED")
    assert completed["outcome"] == "SUCCESS"
    assert completed["sanitized_metadata"]["pipeline_result"] == "SUCCESS"
    assert running["sanitized_metadata"]["build"] == "1"
    for event in (started, running, completed):
        assert event["actor_id"] == world.user
        assert event["actor_label"] == "system:deployments-worker"
        assert event["sanitized_metadata"]["on_behalf_of"] == str(world.user)
    assert len(world.posts()) == 1, "following a build never triggers one"


def test_a_finished_deployment_frees_the_applications_slot(world: World) -> None:
    first = world.new()
    world.run(first)
    world.drive(first)
    second = world.new()  # would be refused by the unique index if the slot were still held
    assert world.row(second)["execution_status"] == "REQUESTED"


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        ("FAILURE", "PIPELINE_FAILED"),
        ("UNSTABLE", "PIPELINE_UNSTABLE"),
        ("ABORTED", "PIPELINE_ABORTED"),
        ("NOT_BUILT", "PIPELINE_NOT_BUILT"),
    ],
)
def test_only_a_pipeline_success_succeeds_every_other_result_has_its_own_failure(
    world: World, result: str, reason: str
) -> None:
    deployment = world.new()
    world.run(deployment)
    for _ in range(4):
        world.tick(deployment)
        if world.row(deployment)["execution_status"] == "RUNNING":
            break

    def finished(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == BUILD_PATH:
            return httpx.Response(
                200,
                json={"building": False, "result": result, "timestamp": 1, "duration": 3},
            )
        return None

    world.use(Programmable(world.mock, finished))
    assert world.tick(deployment) == f"FAILED:{reason}"
    row = world.row(deployment)
    assert row["execution_status"] == "FAILED" and row["failure_reason"] == reason
    assert row["sanitized_result"]["pipeline_result"] == result
    assert row["sanitized_result"]["reconciliation_required"] is False
    assert row["health_check_result"] == {}, "a failure records no success evidence"
    assert row["completed_at"] is not None and row["next_poll_at"] is None


def test_a_real_build_failure_from_jenkins_is_a_failed_deployment(world: World) -> None:
    world.mock.mode = "build_failure"
    deployment = world.new()
    world.run(deployment)
    assert world.drive(deployment) == "FAILED"
    row = world.row(deployment)
    assert row["failure_reason"] == "PIPELINE_FAILED"
    [completed] = world.audit(deployment, "DEPLOYMENT_COMPLETED")
    assert completed["outcome"] == "FAILURE"
    assert completed["sanitized_metadata"]["failure_reason"] == "PIPELINE_FAILED"


def test_a_queue_item_cancelled_in_jenkins_ends_the_deployment_cancelled(world: World) -> None:
    deployment = world.new()
    world.run(deployment)
    world.mock.mode = "queue_cancelled"
    assert world.tick(deployment) == "CANCELLED"
    row = world.row(deployment)
    assert row["execution_status"] == "CANCELLED" and row["failure_reason"] is None
    assert row["completed_at"] is not None
    assert world.audit(deployment, "DEPLOYMENT_COMPLETED")[0]["sanitized_metadata"]["status"] == (
        "CANCELLED"
    )
    world.new()  # the slot is free


def test_a_build_that_is_neither_building_nor_finished_is_not_a_verdict(world: World) -> None:
    """Jenkins did not say: not success, not failure. It is counted as a failed look."""
    deployment = world.new()
    world.run(deployment)
    for _ in range(4):
        world.tick(deployment)
        if world.row(deployment)["execution_status"] == "RUNNING":
            break

    def silent(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == BUILD_PATH:
            return httpx.Response(200, json={"building": False, "result": None})
        return None

    world.use(Programmable(world.mock, silent))
    assert world.tick(deployment) == "RUNNING:poll_failed"
    row = world.row(deployment)
    assert row["execution_status"] == "RUNNING" and row["poll_attempts"] == 1
    assert row["last_poll_error"] == "provider_error" and row["completed_at"] is None


# --- one trigger, however it is delivered ----------------------------------------------------


def test_a_second_delivery_of_the_same_message_changes_and_sends_nothing(world: World) -> None:
    deployment = world.new()
    assert world.run(deployment) == "QUEUED:sent"
    before = world.row(deployment)
    assert world.run(deployment) == "not_claimable"
    assert world.run(deployment) == "not_claimable"
    assert len(world.posts()) == 1
    assert world.row(deployment)["queue_item_id"] == before["queue_item_id"]
    assert len(world.audit(deployment, "DEPLOYMENT_STARTED")) == 1


def test_a_claim_lost_between_the_read_and_the_write_sends_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guarded ``UPDATE`` is the guard, not the read before it.

    Another worker claims the row after this one has read it as claimable and before it writes:
    the write must find the status moved and change nothing, and no trigger may follow.
    """
    deployment = world.new()
    real_now = execute.now_utc
    calls: list[int] = []

    def now_after_a_rival_claims() -> datetime:
        if not calls:
            calls.append(1)
            with world.engine.begin() as connection:
                connection.execute(
                    text("UPDATE deployments SET execution_status = 'QUEUED' WHERE id = :i"),
                    {"i": deployment},
                )
        return real_now()

    monkeypatch.setattr(execute, "now_utc", now_after_a_rival_claims)
    assert world.run(deployment) == "lost_claim"
    assert world.mock.requests == [], "the loser sent nothing"
    assert world.audit(deployment, "DEPLOYMENT_STARTED") == []
    assert world.row(deployment)["execution_status"] == "QUEUED"


def test_four_workers_racing_one_deployment_send_one_trigger(world: World) -> None:
    deployment = world.new()
    barrier = threading.Barrier(4)
    outcomes: list[str] = []

    def work() -> None:
        barrier.wait()
        outcomes.append(world.run(deployment))

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes).count("QUEUED:sent") == 1, outcomes
    assert all(o in ("QUEUED:sent", "lost_claim", "not_claimable") for o in outcomes), outcomes
    assert len(world.posts()) == 1
    assert len(world.audit(deployment, "DEPLOYMENT_STARTED")) == 1


def test_an_abandoned_worker_loses_its_lease_and_the_next_one_resumes_without_a_trigger(
    world: World,
) -> None:
    deployment = world.new()
    world.run(deployment)
    world.due(deployment)
    assert poll.lease_due(world.engine) == [deployment], "the due row is leased"
    # The worker that leased it dies here, before polling. While the lease stands, nobody else may.
    assert poll.lease_due(world.engine) == []
    assert 50 < world.seconds_until_next_poll(deployment) <= 60
    # The lease expires.
    world.due(deployment)
    assert poll.lease_due(world.engine) == [deployment]
    assert world.poll(deployment) == "QUEUED:waiting"
    world.due(deployment)
    assert poll.lease_due(world.engine) == [deployment]
    assert world.drive(deployment) == "SUCCEEDED"
    assert len(world.posts()) == 1, "resuming never re-triggers"


def test_the_lease_takes_only_in_flight_rows_whose_time_has_come(world: World) -> None:
    ready = world.new("QUEUED", queue=100)
    world.due(ready)
    # Not yet due: leased by nobody. Unclaimed and finished rows are never leased at all.
    with world.engine.begin() as connection:
        connection.execute(
            text("UPDATE deployments SET next_poll_at = now() + interval '1 hour' WHERE id = :i"),
            {"i": world.new("FAILED")},
        )
    assert poll.lease_due(world.engine, batch=10) == [ready]
    assert poll.lease_due(world.engine, batch=10) == []


def test_a_row_in_flight_with_no_next_poll_time_is_due_not_forgotten(world: World) -> None:
    deployment = world.new("QUEUED", queue=100)
    with world.engine.begin() as connection:
        connection.execute(
            text("UPDATE deployments SET next_poll_at = NULL WHERE id = :i"), {"i": deployment}
        )
    assert poll.lease_due(world.engine) == [deployment]


def test_the_batch_is_bounded(world: World) -> None:
    deployment = world.new("QUEUED", queue=100)
    world.due(deployment)
    assert poll.lease_due(world.engine, batch=1) == [deployment]


# --- an outage leaves the row as it was ------------------------------------------------------


def test_a_jenkins_outage_mid_poll_changes_nothing_but_the_schedule_and_it_recovers(
    world: World,
) -> None:
    deployment = world.new()
    world.run(deployment)
    world.mock.mode = "outage"
    delays = []
    for expected_attempts in (1, 2, 3, 4):
        assert world.tick(deployment) == "QUEUED:poll_failed"
        row = world.row(deployment)
        assert row["execution_status"] == "QUEUED", "an outage is never a result"
        assert row["poll_attempts"] == expected_attempts
        assert row["last_poll_error"] == "provider_error"
        assert row["completed_at"] is None and row["failure_reason"] is None
        delays.append(round(world.seconds_until_next_poll(deployment)))
    assert delays == [10, 20, 40, 60], "backing off to a cap"
    assert world.row(deployment)["last_polled_at"] is not None

    world.mock.mode = "ok"
    world.harness.circuit.reset()
    assert world.tick(deployment) == "QUEUED:waiting"
    healed = world.row(deployment)
    assert healed["poll_attempts"] == 0 and healed["last_poll_error"] is None
    assert round(world.seconds_until_next_poll(deployment)) == 5
    assert world.drive(deployment) == "SUCCEEDED"
    assert len(world.posts()) == 1


def test_an_open_circuit_is_a_failed_look_with_its_own_reason(world: World) -> None:
    deployment = world.new()
    world.run(deployment)
    world.mock.mode = "outage"
    reasons = []
    for _ in range(8):
        world.tick(deployment)
        reasons.append(world.row(deployment)["last_poll_error"])
    assert reasons[0] == "provider_error" and reasons[-1] == "circuit_open"
    assert world.row(deployment)["execution_status"] == "QUEUED"


def test_the_row_never_carries_jenkins_words(world: World) -> None:
    deployment = world.new()
    world.run(deployment)
    world.mock.mode = "outage"
    world.tick(deployment)
    row = world.row(deployment)
    rendered = json.dumps(row, default=str) + json.dumps(
        world.audit(deployment, "DEPLOYMENT_STARTED"), default=str
    )
    for forbidden in (TOKEN, "down", "No valid crumb", "Authorization", "http://"):
        assert forbidden not in rendered, forbidden


# --- what a failed trigger means -------------------------------------------------------------


def test_an_outage_before_the_trigger_is_a_failure_and_nothing_was_queued(world: World) -> None:
    world.mock.mode = "outage"
    deployment = world.new()
    assert world.run(deployment) == "FAILED:JENKINS_UNAVAILABLE"
    row = world.row(deployment)
    assert row["sanitized_result"]["trigger"] == "not_sent"
    assert row["sanitized_result"]["reconciliation_required"] is False
    assert world.posts() == [], "the crumb read failed; no trigger was sent"


def test_a_wrong_token_is_a_failure_that_names_authentication(world: World) -> None:
    world.mock.token = "some-other-token"
    deployment = world.new()
    assert world.run(deployment) == "FAILED:JENKINS_AUTHENTICATION"
    assert world.posts() == []


def test_a_job_jenkins_does_not_know_is_job_not_found_and_is_not_retried(
    migrated: Engine, harness: Harness, mock: Any, live: Settings
) -> None:
    built = _build_world(migrated, harness, mock, live, template=TEMPLATE, pipeline="demo/nope")
    try:
        deployment = built.new()
        assert built.run(deployment) == "FAILED:JOB_NOT_FOUND"
        assert len(built.asked("/job/demo/job/nope/buildWithParameters")) == 1
    finally:
        _cleanup(migrated, built)


def test_a_connection_that_never_opened_is_unavailable_and_sent_once_at_most(
    world: World,
) -> None:
    attempts = []

    def refuse(request: httpx.Request) -> Any:
        if request.method == "POST":
            attempts.append(1)
            return httpx.ConnectError("refused", request=request)
        return None

    world.use(Programmable(world.mock, refuse))
    deployment = world.new()
    assert world.run(deployment) == "FAILED:JENKINS_UNAVAILABLE"
    assert len(attempts) == 1, "a state-changing call is never retried"


def test_a_5xx_after_the_post_may_have_queued_a_build_so_it_is_looked_for_not_failed(
    world: World,
) -> None:
    attempts = []

    def server_error(request: httpx.Request) -> Any:
        if request.method == "POST":
            attempts.append(1)
            return httpx.Response(503, text="try later")
        return None

    world.use(Programmable(world.mock, server_error))
    deployment = world.new()
    assert world.run(deployment) == "reconcile"
    row = world.row(deployment)
    assert row["execution_status"] == "QUEUED" and row["queue_item_id"] is None
    assert row["sanitized_result"]["trigger"] == "outcome_unknown"
    assert row["completed_at"] is None, "the slot stays held: a build may be running"
    assert len(attempts) == 1


# --- reconciling an unknown outcome ----------------------------------------------------------


def test_a_lost_answer_is_waited_on_then_found_by_deployment_id_and_never_retriggered(
    world: World,
) -> None:
    lost = LostResponse(world.mock, deliver=True)
    world.use(lost)
    deployment = world.new()
    assert world.run(deployment) == "reconcile"
    assert lost.posts == 1 and len(world.posts()) == 1, "the build was queued; the answer was lost"
    assert world.row(deployment)["queue_item_id"] is None

    # A live worker is given time to record its answer: no looking yet.
    assert world.tick(deployment, world.later(deployment, 5)) == "QUEUED:waiting"
    assert world.asked("/queue/api/json") == []

    # After the grace period it looks, finds DCP's own id in the queue, and follows that item.
    assert world.tick(deployment, world.later(deployment, 61)) == "QUEUED:reconciled"
    assert world.row(deployment)["queue_item_id"] == 100
    assert world.drive(deployment) == "SUCCEEDED"
    assert lost.posts == 1 and len(world.posts()) == 1, "never triggered again"
    assert world.row(deployment)["external_execution_id"] == "1"


def test_a_build_that_already_started_is_found_as_a_build(world: World) -> None:
    lost = LostResponse(world.mock, deliver=True)
    world.use(lost)
    deployment = world.new()
    world.run(deployment)
    with world.mock._lock:
        for queue_item in world.mock.queue.values():
            world.mock._start(queue_item)  # Jenkins started it while DCP was not looking
    assert world.tick(deployment, world.later(deployment, 61)) == "RUNNING"
    assert world.row(deployment)["external_execution_id"] == "1"
    assert world.drive(deployment) == "SUCCEEDED"
    assert lost.posts == 1


def test_an_answer_lost_with_no_build_behind_it_is_given_up_on_honestly(world: World) -> None:
    dropped = LostResponse(world.mock, deliver=False)
    world.use(dropped)
    deployment = world.new()
    world.run(deployment)
    assert world.tick(deployment, world.later(deployment, 61)) == "QUEUED:waiting"
    assert world.row(deployment)["execution_status"] == "QUEUED"
    assert round(world.seconds_until_next_poll(deployment)) == 10
    assert world.tick(deployment, world.later(deployment, 179)) == "QUEUED:waiting"
    assert world.tick(deployment, world.later(deployment, 181)) == (
        "FAILED:TRIGGER_OUTCOME_UNKNOWN"
    )
    row = world.row(deployment)
    assert row["failure_reason"] == "TRIGGER_OUTCOME_UNKNOWN"
    assert row["sanitized_result"]["reconciliation_required"] is True
    assert dropped.posts == 1 and world.posts() == [], "no second trigger, ever"
    assert row["completed_at"] is not None


def test_a_target_with_no_deployment_id_slot_cannot_be_reconciled_and_says_so(
    world_without_slot: World,
) -> None:
    world = world_without_slot
    world.use(LostResponse(world.mock, deliver=True))
    deployment = world.new()
    world.run(deployment)
    assert world.tick(deployment, world.later(deployment, 5)) == "QUEUED:waiting"
    assert world.tick(deployment, world.later(deployment, 61)) == ("FAILED:TRIGGER_OUTCOME_UNKNOWN")
    assert world.asked("/queue/api/json") == [], "nothing to look for, so it did not look"
    assert world.row(deployment)["sanitized_result"]["reconciliation_required"] is True


def test_a_queue_item_jenkins_forgot_is_found_as_the_build_it_became(world: World) -> None:
    deployment = world.new()
    world.run(deployment)
    world.mock.mode = "lose_queue_item"
    assert world.tick(deployment) == "RUNNING"
    assert world.row(deployment)["external_execution_id"] == "1"
    world.mock.mode = "ok"
    assert world.drive(deployment) == "SUCCEEDED"
    assert len(world.posts()) == 1


def test_a_forgotten_queue_item_with_no_build_behind_it_ends_queue_item_lost_and_flagged(
    world: World,
) -> None:
    deployment = world.new()
    world.run(deployment)

    def nothing_left(request: httpx.Request) -> httpx.Response | None:
        path = request.url.path
        if path.startswith("/queue/item/"):
            return httpx.Response(404, text="gone")
        if path == "/queue/api/json":
            return httpx.Response(200, json={"items": []})
        if path == "/job/demo/job/hello-pipeline/api/json":
            return httpx.Response(200, json={"builds": []})
        return None

    world.use(Programmable(world.mock, nothing_left))
    assert world.tick(deployment) == "FAILED:QUEUE_ITEM_LOST"
    row = world.row(deployment)
    assert row["sanitized_result"]["reconciliation_required"] is True
    assert row["completed_at"] is not None
    assert len(world.posts()) == 1, "the one trigger from the claim, and no other"


# --- the re-check: each refusal sends nothing to Jenkins -------------------------------------


def _sql(world: World, statement: str, **params: Any) -> None:
    with world.engine.begin() as connection:
        connection.execute(
            text(statement), {"u": world.user, "t": world.target, "a": world.application, **params}
        )


REFUSALS: dict[str, tuple[Callable[[World], None], str]] = {
    "requester_disabled": (
        lambda w: _sql(w, "UPDATE users SET status = 'DISABLED' WHERE id = :u"),
        "PERMISSION_REVOKED",
    ),
    "grants_revoked": (lambda w: revoke_all(w.engine, w.user), "PERMISSION_REVOKED"),
    "role_cannot_create": (
        lambda w: (revoke_all(w.engine, w.user), grant(w.engine, w.user, "VIEWER", FULL_SCOPE)),
        "PERMISSION_REVOKED",
    ),
    "scope_moved_to_production": (
        lambda w: (
            revoke_all(w.engine, w.user),
            grant(w.engine, w.user, "DEVOPS_ENGINEER", '{"environment":["production"]}'),
        ),
        "PERMISSION_REVOKED",
    ),
    "application_archived": (
        lambda w: _sql(w, "UPDATE applications SET deleted_at = now() WHERE id = :a"),
        "TARGET_VALIDATION_FAILED",
    ),
    "target_retired": (
        lambda w: _sql(
            w, "UPDATE deployment_targets SET deleted_at = now(), status = 'DISABLED' WHERE id = :t"
        ),
        "TARGET_VALIDATION_FAILED",
    ),
    "target_disabled": (
        lambda w: _sql(w, "UPDATE deployment_targets SET status = 'DISABLED' WHERE id = :t"),
        "TARGET_VALIDATION_FAILED",
    ),
    "template_edited": (
        lambda w: _sql(
            w,
            "UPDATE deployment_targets SET parameter_template = jsonb_set("
            "parameter_template, '{fixed,TARGET}', '\"production\"') WHERE id = :t",
        ),
        "TARGET_CHANGED",
    ),
    "pipeline_edited": (
        lambda w: _sql(
            w,
            "UPDATE deployment_targets SET pipeline_reference = 'demo/hello-rollback'"
            " WHERE id = :t",
        ),
        "TARGET_CHANGED",
    ),
    "ref_no_longer_permitted": (
        lambda w: _sql(
            w,
            "UPDATE deployment_targets SET allowed_ref_patterns = ARRAY['^release/.+$']"
            " WHERE id = :t",
        ),
        "REF_NOT_PERMITTED",
    ),
    "policy_linked_since": (
        lambda w: _sql(
            w,
            "UPDATE deployment_targets SET approval_policy_id = (SELECT id FROM approval_policies"
            " WHERE name = 'production-deployments') WHERE id = :t",
        ),
        "APPROVAL_REQUIRED",
    ),
}


@pytest.mark.parametrize("name", sorted(REFUSALS))
def test_a_check_that_no_longer_holds_fails_the_deployment_and_sends_nothing(
    world: World, name: str
) -> None:
    mutate, reason = REFUSALS[name]
    deployment = world.new()
    mutate(world)
    assert world.run(deployment) == f"FAILED:{reason}"
    assert world.mock.requests == [], "not even the crumb request was made"
    row = world.row(deployment)
    assert row["execution_status"] == "FAILED" and row["failure_reason"] == reason
    assert row["sanitized_result"]["trigger"] == "not_attempted"
    assert row["sanitized_result"]["reconciliation_required"] is False
    assert row["queue_item_id"] is None and row["completed_at"] is not None
    [denied] = world.audit(deployment, "DEPLOYMENT_DENIED")
    assert denied["sanitized_metadata"]["reason"] == reason
    assert denied["sanitized_metadata"]["phase"] == "worker"
    assert denied["actor_id"] == world.user
    [completed] = world.audit(deployment, "DEPLOYMENT_COMPLETED")
    assert completed["outcome"] == "FAILURE"


def test_jenkins_unconfigured_since_the_request_fails_the_deployment(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployment = world.new()
    for name in (
        "DCP_JENKINS_URL",
        "DCP_JENKINS_USER",
        "DCP_JENKINS_TOKEN_REFERENCE",
        "DCPSECRET_JENKINS_TOKEN",
    ):
        monkeypatch.delenv(name)
    get_settings.cache_clear()
    assert world.run(deployment) == "FAILED:NOT_CONFIGURED"
    assert world.mock.requests == []


def test_demonstration_mode_since_the_request_fails_the_deployment(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployment = world.new()
    monkeypatch.setenv("DCP_DEMO_MODE", "true")
    get_settings.cache_clear()
    assert world.run(deployment) == "FAILED:NOT_CONFIGURED"
    assert world.mock.requests == []


def test_a_production_row_is_refused_whatever_its_approval_flag_says(
    migrated: Engine, harness: Harness, mock: Any, live: Settings
) -> None:
    """Nothing can approve before M5.4, so a flag that says so is not evidence."""
    built = _build_world(migrated, harness, mock, live, template=TEMPLATE, environment="production")
    try:
        for flag in ("NOT_REQUIRED", "APPROVED"):
            deployment = built.new(environment="production", approval=flag, key=uuid.uuid4().hex)
            assert built.run(deployment) == "FAILED:APPROVAL_REQUIRED", flag
            assert built.mock.requests == []
    finally:
        _cleanup(migrated, built)


def test_the_dcp_setting_that_matters_is_read_when_the_worker_runs_not_when_it_started(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker is long-lived; its view of the configuration must not be frozen at start-up."""
    deployment = world.new()
    monkeypatch.setenv("DCP_DEMO_MODE", "true")
    get_settings.cache_clear()
    assert world.run(deployment).startswith("FAILED")
    monkeypatch.setenv("DCP_DEMO_MODE", "false")
    get_settings.cache_clear()
    again = world.new()
    assert world.run(again) == "QUEUED:sent"


# --- the deadline, the reaper, the healer ----------------------------------------------------


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING"])
def test_a_deployment_past_its_deadline_is_timed_out_and_says_a_build_may_still_run(
    world: World, status: str
) -> None:
    deployment = world.new(status, queue=100 if status == "QUEUED" else None, deadline="-5")
    outcome = reap.reap(world.engine, enqueue=lambda _id: None)
    assert outcome["timed_out"] == 1
    row = world.row(deployment)
    assert row["execution_status"] == "FAILED" and row["failure_reason"] == "DEPLOYMENT_TIMED_OUT"
    assert row["sanitized_result"]["reconciliation_required"] is True
    assert row["sanitized_result"]["build_may_still_be_running"] is True
    assert row["completed_at"] is not None and row["next_poll_at"] is None
    assert world.mock.requests == [], "the reaper never calls Jenkins"
    [completed] = world.audit(deployment, "DEPLOYMENT_COMPLETED")
    assert completed["sanitized_metadata"]["phase"] == "reaper"
    assert completed["actor_label"] == "system:deployments-worker"
    world.new()  # the slot is free


@pytest.mark.parametrize("status", ["REQUESTED", "APPROVED"])
def test_an_unclaimed_deployment_past_its_deadline_expires_before_start(
    world: World, status: str
) -> None:
    deployment = world.new(status, deadline="-5")
    outcome = reap.reap(world.engine, enqueue=lambda _id: None)
    assert outcome["expired_before_start"] == 1
    row = world.row(deployment)
    assert row["failure_reason"] == "EXPIRED_BEFORE_START"
    assert row["sanitized_result"]["trigger"] == "not_attempted"
    assert row["sanitized_result"]["reconciliation_required"] is False
    assert world.mock.requests == []


def test_a_row_waiting_for_an_approval_is_not_the_reapers(world: World) -> None:
    deployment = world.new("AWAITING_APPROVAL", deadline="-5")
    reap.reap(world.engine, enqueue=lambda _id: None)
    assert world.row(deployment)["execution_status"] == "AWAITING_APPROVAL"


def test_the_reaper_leaves_a_deployment_inside_its_deadline_alone(world: World) -> None:
    deployment = world.new("RUNNING")
    outcome = reap.reap(world.engine, enqueue=lambda _id: None)
    assert outcome == {"timed_out": 0, "expired_before_start": 0, "requeued": 0}
    assert world.row(deployment)["execution_status"] == "RUNNING"


def test_the_healer_hands_an_old_unclaimed_row_back(world: World) -> None:
    old = world.new(created=(datetime.now(UTC) - timedelta(seconds=120)).isoformat())
    queued: list[uuid.UUID] = []
    outcome = reap.reap(world.engine, enqueue=queued.append)
    assert queued == [old] and outcome["requeued"] == 1


def test_the_healer_leaves_a_fresh_row_alone(world: World) -> None:
    """A live message is still on its way to a row this young."""
    fresh = world.new()
    queued: list[uuid.UUID] = []
    reap.reap(world.engine, enqueue=queued.append)
    assert queued == [] and world.row(fresh)["execution_status"] == "REQUESTED"


def test_a_row_the_healer_requeued_is_then_run_once(world: World) -> None:
    deployment = world.new(created=(datetime.now(UTC) - timedelta(seconds=120)).isoformat())
    delivered: list[uuid.UUID] = []
    reap.reap(world.engine, enqueue=delivered.append)
    reap.reap(world.engine, enqueue=delivered.append)  # the message may be sent twice
    assert delivered == [deployment, deployment]
    outcomes = [world.run(i) for i in delivered]
    assert outcomes == ["QUEUED:sent", "not_claimable"]
    assert len(world.posts()) == 1


def test_a_claim_after_the_deadline_expires_the_row_instead_of_triggering(world: World) -> None:
    deployment = world.new(deadline="-5")
    assert world.run(deployment) == "FAILED:EXPIRED_BEFORE_START"
    assert world.mock.requests == []


def test_the_poller_leaves_an_overdue_deployment_to_the_reaper(world: World) -> None:
    deployment = world.new("RUNNING", deadline="-5")
    assert world.poll(deployment) == "overdue"
    assert world.mock.requests == []
    assert world.row(deployment)["execution_status"] == "RUNNING"


def test_a_late_success_cannot_resurrect_a_deployment_the_deadline_ended(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployment = world.new()
    world.run(deployment)
    for _ in range(4):
        world.tick(deployment)
        if world.row(deployment)["execution_status"] == "RUNNING":
            break
    assert world.row(deployment)["execution_status"] == "RUNNING"

    def build_while_the_deadline_passes(job: str, number: int) -> BuildState:
        # Jenkins is slow to answer; meanwhile the reaper ends the deployment at its deadline.
        with world.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE deployments SET deadline_at = now() - interval '1 second' WHERE id = :i"
                ),
                {"i": deployment},
            )
        reap.reap(world.engine, enqueue=lambda _id: None)
        return BuildState(False, "SUCCESS", None, 1, "0123456789abcdef0123456789abcdef01234567")

    monkeypatch.setattr(world.harness.client, "build", build_while_the_deadline_passes)
    assert world.tick(deployment) == "superseded"
    row = world.row(deployment)
    assert row["execution_status"] == "FAILED" and row["failure_reason"] == "DEPLOYMENT_TIMED_OUT"
    assert row["health_check_result"] == {}, "no success evidence was written"
    assert row["commit_sha"] is None
    assert len(world.audit(deployment, "DEPLOYMENT_COMPLETED")) == 1


# --- not polled -----------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["REQUESTED", "AWAITING_APPROVAL", "APPROVED"])
def test_a_row_nobody_has_claimed_is_not_polled(world: World, status: str) -> None:
    deployment = world.new(status)
    assert world.poll(deployment) == "not_polled"
    assert world.mock.requests == []


def test_an_unknown_id_is_not_polled(world: World) -> None:
    assert world.poll(uuid.uuid4()) == "not_polled"
    assert world.run(uuid.uuid4()) == "not_claimable"
