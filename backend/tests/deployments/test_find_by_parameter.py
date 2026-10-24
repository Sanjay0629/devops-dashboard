"""``JenkinsClient.find_by_parameter`` and the mock's matching responses (M5.2; ``OPEN-137``).

This is how an unknown trigger outcome is reconciled: look, read-only, for the one queue item or
build carrying DCP's own ``deployment_id`` UUID, and **never trigger again**. It is proven against
the mock only. Whether a real Jenkins exposes build parameters on queue items and builds depends on
its plugins; when it does not, the answer is ``None`` and the caller ends the row honestly
(``TRIGGER_OUTCOME_UNKNOWN``), which ``test_a_jenkins_that_hides_parameters_finds_nothing`` pins.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.deployments.jenkins import JenkinsClient, JenkinsError, Located
from app.metrics.client import FAILURE_THRESHOLD
from tests.deployments.harness import JOB, Harness, flaky

NAME = "DCP_DEPLOYMENT_ID"


def _trigger(harness: Harness, deployment_id: str | None = None, **extra: str) -> str:
    deployment_id = deployment_id or str(uuid.uuid4())
    harness.client.trigger(JOB, {"GIT_REF": "main", NAME: deployment_id, **extra})
    return deployment_id


def _start_the_build(harness: Harness, queue_id: int) -> int:
    """Poll the queue item until Jenkins has started the build, as a poller would."""
    for _ in range(10):
        state = harness.client.queue_item(queue_id)
        if state.kind == "started":
            assert state.build_number is not None
            return state.build_number
    raise AssertionError("the mock never started the build")


# --- finding it ---------------------------------------------------------------------------


def test_a_queued_item_is_found_by_its_deployment_id(harness: Harness) -> None:
    deployment_id = _trigger(harness)
    assert harness.client.find_by_parameter(JOB, NAME, deployment_id) == Located("queue", 100)


def test_a_started_build_is_found_by_its_deployment_id(harness: Harness) -> None:
    deployment_id = _trigger(harness)
    number = _start_the_build(harness, 100)
    assert harness.client.find_by_parameter(JOB, NAME, deployment_id) == Located("build", number)


def test_the_right_one_is_found_among_several(harness: Harness) -> None:
    first, second, third = (_trigger(harness) for _ in range(3))
    assert harness.client.find_by_parameter(JOB, NAME, second) == Located("queue", 101)
    assert harness.client.find_by_parameter(JOB, NAME, third) == Located("queue", 102)
    assert harness.client.find_by_parameter(JOB, NAME, first) == Located("queue", 100)


def test_a_deployment_id_nobody_triggered_finds_nothing(harness: Harness) -> None:
    _trigger(harness)
    assert harness.client.find_by_parameter(JOB, NAME, str(uuid.uuid4())) is None


def test_nothing_triggered_at_all_finds_nothing(harness: Harness) -> None:
    assert harness.client.find_by_parameter(JOB, NAME, str(uuid.uuid4())) is None


def test_the_parameter_name_must_match_not_only_the_value(harness: Harness) -> None:
    deployment_id = _trigger(harness)
    assert harness.client.find_by_parameter(JOB, "SOME_OTHER_NAME", deployment_id) is None


def test_a_value_that_merely_contains_the_id_is_not_a_match(harness: Harness) -> None:
    other = str(uuid.uuid4())
    _trigger(harness, deployment_id=f"{other}-extra")
    assert harness.client.find_by_parameter(JOB, NAME, other) is None


def test_a_lost_queue_item_is_still_found_as_the_build_it_became(
    harness: Harness, mock: Any
) -> None:
    """Jenkins forgets a queue item (404) once it has started; the build is what remains."""
    deployment_id = _trigger(harness)
    mock.mode = "lose_queue_item"
    assert harness.client.queue_item(100).kind == "gone"
    found = harness.client.find_by_parameter(JOB, NAME, deployment_id)
    assert found == Located("build", 1)


def test_a_jenkins_that_hides_parameters_finds_nothing(harness: Harness, mock: Any) -> None:
    """The honest limit: with no parameter visible there is nothing to match, so ``None``."""
    deployment_id = _trigger(harness)
    mock.mode = "hide_parameters"
    assert harness.client.find_by_parameter(JOB, NAME, deployment_id) is None


# --- it only reads ------------------------------------------------------------------------


@pytest.mark.parametrize("found", [True, False], ids=["found in the queue", "reads both lists"])
def test_it_issues_only_get_requests_and_sends_no_crumb(harness: Harness, found: bool) -> None:
    triggered = _trigger(harness)
    harness.mock.requests.clear()
    harness.client.find_by_parameter(JOB, NAME, triggered if found else str(uuid.uuid4()))
    sent = harness.requests()
    assert len(sent) == (1 if found else 2), "it must look, and look at both lists when it must"
    assert {r["method"] for r in sent} == {"GET"}
    assert not any(r["path"].endswith("buildWithParameters") for r in sent)
    assert not any(r["crumb"] for r in sent)


def test_it_never_triggers_a_build(harness: Harness) -> None:
    deployment_id = _trigger(harness)
    before = len(harness.mock.queue)
    for _ in range(3):
        harness.client.find_by_parameter(JOB, NAME, deployment_id)
        harness.client.find_by_parameter(JOB, NAME, str(uuid.uuid4()))
    assert len(harness.mock.queue) == before == 1


def test_it_reads_the_queue_before_the_builds(harness: Harness) -> None:
    harness.mock.requests.clear()
    harness.client.find_by_parameter(JOB, NAME, str(uuid.uuid4()))
    paths = [r["path"] for r in harness.requests()]
    assert paths == ["/queue/api/json", f"/job/{JOB.replace('/', '/job/')}/api/json"]


def test_it_asks_jenkins_for_only_what_it_needs(harness: Harness) -> None:
    harness.mock.requests.clear()
    harness.client.find_by_parameter(JOB, NAME, str(uuid.uuid4()))
    queue, builds = harness.requests()
    assert queue["query"]["tree"] == "items[id,actions[parameters[name,value]]]"
    assert builds["query"]["tree"] == "builds[number,actions[parameters[name,value]]]{0,50}"


def test_it_stops_at_the_queue_when_the_queue_has_the_answer(harness: Harness) -> None:
    deployment_id = _trigger(harness)
    harness.mock.requests.clear()
    harness.client.find_by_parameter(JOB, NAME, deployment_id)
    assert [r["path"] for r in harness.requests()] == ["/queue/api/json"]


# --- reads fail like reads ----------------------------------------------------------------


def test_an_outage_is_retried_like_any_read_then_raised(harness: Harness, mock: Any) -> None:
    mock.mode = "outage"
    with pytest.raises(JenkinsError) as raised:
        harness.client.find_by_parameter(JOB, NAME, str(uuid.uuid4()))
    assert raised.value.reason == "provider_error"
    assert raised.value.may_have_acted is False  # a read never acts
    assert len(harness.sleeps) == 2  # two retries, as every read


def test_a_job_jenkins_does_not_know_is_job_not_found(harness: Harness) -> None:
    with pytest.raises(JenkinsError) as raised:
        harness.client.find_by_parameter("demo/not-a-job", NAME, str(uuid.uuid4()))
    assert raised.value.reason == "job_not_found"


def test_the_circuit_is_respected(harness: Harness, mock: Any) -> None:
    mock.mode = "outage"
    for _ in range(FAILURE_THRESHOLD):
        with pytest.raises(JenkinsError):
            harness.client.find_by_parameter(JOB, NAME, str(uuid.uuid4()))
    harness.mock.requests.clear()
    with pytest.raises(JenkinsError) as raised:
        harness.client.find_by_parameter(JOB, NAME, str(uuid.uuid4()))
    assert raised.value.reason == "circuit_open"
    assert harness.requests() == []


# --- two of one means something went wrong, and it is not guessed at ----------------------


def test_two_queue_items_with_one_deployment_id_are_ambiguous(harness: Harness) -> None:
    """A double trigger. Which one is DCP's? Neither: the caller records it as unreconcilable."""
    deployment_id = str(uuid.uuid4())
    _trigger(harness, deployment_id)
    _trigger(harness, deployment_id)
    with pytest.raises(JenkinsError) as raised:
        harness.client.find_by_parameter(JOB, NAME, deployment_id)
    assert raised.value.reason == "provider_error"


def test_two_builds_with_one_deployment_id_are_ambiguous(harness: Harness) -> None:
    deployment_id = str(uuid.uuid4())
    _trigger(harness, deployment_id)
    _trigger(harness, deployment_id)
    _start_the_build(harness, 100)
    _start_the_build(harness, 101)
    with pytest.raises(JenkinsError) as raised:
        harness.client.find_by_parameter(JOB, NAME, deployment_id)
    assert raised.value.reason == "provider_error"


# --- malformed answers --------------------------------------------------------------------


def _answer(body: Any, status: int = 200) -> Any:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return respond


@pytest.mark.parametrize(
    "body",
    [
        [],
        "items",
        {"items": "none"},
        {"items": {"id": 1}},
        {"items": [{"id": "x", "actions": [{"parameters": [{"name": NAME, "value": "V"}]}]}]},
        {"items": [{"id": 0, "actions": [{"parameters": [{"name": NAME, "value": "V"}]}]}]},
        {"items": [{"id": True, "actions": [{"parameters": [{"name": NAME, "value": "V"}]}]}]},
    ],
)
def test_a_malformed_queue_answer_is_provider_error_not_a_miss(
    settings: Settings, mock: Any, body: Any
) -> None:
    """A garbled answer must not read as "not found": that would let a caller conclude no build."""
    harness, _ = flaky(settings, mock, get=_answer(body))
    with pytest.raises(JenkinsError) as raised:
        harness.client.find_by_parameter(JOB, NAME, "V")
    assert raised.value.reason == "provider_error"


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"builds": "none"},
        {"builds": [{"number": "1", "actions": []}]},
        {"builds": [{"number": 0, "actions": []}]},
    ],
)
def test_a_malformed_build_list_is_provider_error_not_a_miss(
    settings: Settings, mock: Any, body: Any
) -> None:
    def respond(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == "/queue/api/json":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json=body)

    harness, _ = flaky(settings, mock, get=respond)
    with pytest.raises(JenkinsError) as raised:
        harness.client.find_by_parameter(JOB, NAME, "V")
    assert raised.value.reason == "provider_error"


def test_actions_of_other_shapes_are_skipped_not_fatal(settings: Settings, mock: Any) -> None:
    body = {
        "items": [
            {"id": 5, "actions": []},
            {"id": 6, "actions": [{"_class": "hudson.model.CauseAction"}]},
            {"id": 7, "actions": [{"parameters": "none"}]},
            {"id": 8, "actions": [{"parameters": [None, 3, {"name": NAME}, {"value": "V"}]}]},
            {"id": 9, "actions": "none"},
            {"id": 10, "actions": [{"parameters": [{"name": NAME, "value": "V"}]}]},
        ]
    }
    harness, _ = flaky(settings, mock, get=_answer(body))
    assert harness.client.find_by_parameter(JOB, NAME, "V") == Located("queue", 10)


def test_a_non_string_value_is_not_a_match(settings: Settings, mock: Any) -> None:
    queue = {"items": [{"id": 5, "actions": [{"parameters": [{"name": NAME, "value": 1234}]}]}]}

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=queue if request.url.path == "/queue/api/json" else {"builds": []}
        )

    harness, _ = flaky(settings, mock, get=respond)
    assert harness.client.find_by_parameter(JOB, NAME, "1234") is None


# --- what it will accept, before anything is sent ----------------------------------------


@pytest.mark.parametrize("name", ["", "1ABC", "A B", "A-B", "a" * 65, "NAME\n", "../x"])
def test_a_bad_parameter_name_is_refused_before_any_request(harness: Harness, name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        harness.client.find_by_parameter(JOB, name, str(uuid.uuid4()))
    assert harness.requests() == []


@pytest.mark.parametrize("value", ["", "a b", "x" * 65, "v\n", "v&other=1", "../x", "a;b"])
def test_a_bad_value_is_refused_before_any_request(harness: Harness, value: str) -> None:
    with pytest.raises(ValueError, match="value"):
        harness.client.find_by_parameter(JOB, NAME, value)
    assert harness.requests() == []


def test_a_bad_job_path_is_refused_before_any_request(harness: Harness) -> None:
    from app.deployments.paths import InvalidJobPathError

    for job in ("../etc/passwd", "a/../b", "a//b", ""):
        with pytest.raises(InvalidJobPathError):
            harness.client.find_by_parameter(job, NAME, str(uuid.uuid4()))
    assert harness.requests() == []


def test_the_signature_has_no_url_command_or_free_form_slot() -> None:
    assert list(inspect.signature(JenkinsClient.find_by_parameter).parameters) == [
        "self",
        "job_path",
        "name",
        "value",
    ]


def test_located_is_a_closed_pair() -> None:
    assert Located("queue", 5).kind == "queue" and Located("build", 7).number == 7
    with pytest.raises(AttributeError):  # frozen
        Located("queue", 5).number = 6  # type: ignore[misc]
