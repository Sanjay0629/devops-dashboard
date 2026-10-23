"""The Jenkins client against the mock Jenkins, in process (M5.1; ``OPEN-125``, ``OPEN-130``).

The mock's WSGI app is mounted with ``httpx.WSGITransport``: the checked outbound path refuses
loopback, so a real socket on this machine could not be used, and the resolver is injected to
return a private address the settings allow. That proves the client's behaviour, not a real
Jenkins'. The real network path is ``scripts/smoke_jenkins.py``.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from typing import Any

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.deployments.jenkins import (
    CONSOLE_LIMIT_BYTES,
    JenkinsClient,
    JenkinsError,
    QueueRef,
    QueueState,
    to_app_error,
)
from app.deployments.template import render_parameters
from app.metrics.client import FAILURE_THRESHOLD, CircuitBreaker
from tests.deployments.harness import JOB, TOKEN, Harness, flaky

PARAMS = {"GIT_REF": "main", "DCP_DEPLOYMENT_ID": "d-1"}


# --- the happy path ---------------------------------------------------------------------


def test_probe_is_authenticated_and_reads_only(harness: Harness) -> None:
    harness.client.probe()
    (request,) = harness.requests()
    assert (request["method"], request["path"], request["authorised"]) == ("GET", "/api/json", True)


def test_trigger_queue_build_console_end_to_end(harness: Harness) -> None:
    client = harness.client
    ref = client.trigger(JOB, PARAMS)
    assert ref == QueueRef(100)

    assert [client.queue_item(ref.queue_id).kind for _ in range(2)] == ["waiting", "waiting"]
    started = client.queue_item(ref.queue_id)
    assert started == QueueState("started", 1)

    assert client.build(JOB, 1).building and client.build(JOB, 1).building
    done = client.build(JOB, 1)
    assert (done.building, done.result, done.duration_ms) == (False, "SUCCESS", 4_200)
    assert done.commit_sha == "0123456789abcdef0123456789abcdef01234567"
    assert done.started_at is not None

    chunk = client.console(JOB, 1)
    assert "mock-canary-jenkins-0001" in chunk.text  # raw: redaction is the service layer's job
    assert chunk.more is False and chunk.next_offset == len(chunk.text.encode())
    assert client.console(JOB, 1, offset=chunk.next_offset).text == ""


def test_the_trigger_sends_exactly_the_rendered_parameters_and_nothing_else(
    harness: Harness,
) -> None:
    rendered = render_parameters(
        {"fixed": {"TARGET": "staging"}, "slots": {"GIT_REF": "ref", "ID": "deployment_id"}},
        ref="release/1.0",
        deployment_id=uuid.UUID(int=1),
        environment="staging",
        requested_by=uuid.UUID(int=2),
    )
    harness.client.trigger(JOB, rendered)
    (post,) = harness.requests("POST")
    assert post["params"] == rendered
    assert (
        post["query"] == {} and post["path"] == "/job/demo/job/hello-pipeline/buildWithParameters"
    )


def test_a_crumb_is_fetched_and_sent_when_the_jenkins_issues_one(harness: Harness) -> None:
    harness.mock.crumb = "enforce"
    harness.client.trigger(JOB, PARAMS)
    assert harness.requests("POST")[0]["crumb"] is True


def test_no_crumb_issuer_means_none_required(harness: Harness) -> None:
    harness.mock.crumb = "off"
    harness.client.trigger(JOB, PARAMS)
    assert harness.requests("POST")[0]["crumb"] is False


def test_a_crumb_the_jenkins_refuses_is_crumb_rejected_and_not_retried(
    settings: Settings, mock: Any
) -> None:
    mock.crumb = "enforce"
    harness, _ = flaky(settings, mock)
    harness.client._crumb_headers = lambda: {}  # type: ignore[method-assign]
    with pytest.raises(JenkinsError) as raised:
        harness.client.trigger(JOB, PARAMS)
    assert raised.value.reason == "crumb_rejected" and not raised.value.may_have_acted
    assert len(harness.requests("POST")) == 1


def test_queue_states(harness: Harness) -> None:
    harness.client.trigger(JOB, PARAMS)
    harness.mock.mode = "lose_queue_item"
    assert harness.client.queue_item(100) == QueueState("gone")  # a 404 is "gone", not an error
    harness.mock.mode = "queue_cancelled"
    assert harness.client.queue_item(100) == QueueState("cancelled")


def test_a_failed_build_is_reported_as_failed(harness: Harness) -> None:
    harness.mock.mode = "build_failure"
    harness.client.trigger(JOB, PARAMS)
    for _ in range(3):
        harness.client.queue_item(100)
    for _ in range(3):
        state = harness.client.build(JOB, 1)
    assert state.result == "FAILURE"


# --- credentials and refusals ------------------------------------------------------------


def test_a_wrong_token_is_auth_failed_and_the_circuit_treats_it_as_an_answer(
    settings: Settings, mock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DCPSECRET_JENKINS_TOKEN", "not-the-token")
    harness = Harness(settings, mock, httpx.WSGITransport(app=mock))
    for _ in range(FAILURE_THRESHOLD + 2):
        with pytest.raises(JenkinsError) as raised:
            harness.client.probe()
        assert raised.value.reason == "auth_failed"
    assert harness.circuit.state() == "closed"  # Jenkins answered each time


def test_an_unregistered_job_is_job_not_found(harness: Harness) -> None:
    with pytest.raises(JenkinsError) as raised:
        harness.client.trigger("demo/unknown", PARAMS)
    assert raised.value.reason == "job_not_found" and not raised.value.may_have_acted


def test_an_unconfigured_client_says_so(settings: Settings, mock: Any) -> None:
    unconfigured = Settings.model_construct(**{**settings.model_dump(), "jenkins_url": None})
    client = JenkinsClient(unconfigured, transport=httpx.WSGITransport(app=mock))
    assert not client.configured
    with pytest.raises(JenkinsError) as raised:
        client.probe()
    assert raised.value.reason == "not_configured"
    assert mock.requests == []


def test_an_unresolvable_token_is_not_configured(
    settings: Settings, mock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DCPSECRET_JENKINS_TOKEN")
    harness = Harness(settings, mock, httpx.WSGITransport(app=mock))
    with pytest.raises(JenkinsError) as raised:
        harness.client.probe()
    assert raised.value.reason == "not_configured"
    assert mock.requests == []


# --- the outbound path --------------------------------------------------------------------


@pytest.mark.parametrize(
    "addresses",
    [
        ["169.254.169.254"],  # the metadata endpoint
        ["127.0.0.1"],
        ["::ffff:169.254.169.254"],
        ["10.1.2.3", "169.254.169.254"],  # one good record and one bad: every address is checked
        ["192.168.1.1"],  # private, but outside DCP_SSRF_ALLOWED_NETWORKS
    ],
)
def test_a_name_that_resolves_somewhere_it_must_not_is_refused_before_any_request(
    settings: Settings, mock: Any, addresses: list[str]
) -> None:
    client = JenkinsClient(
        settings,
        transport=httpx.WSGITransport(app=mock),
        resolver=lambda host, port: addresses,
        circuit=CircuitBreaker(),
    )
    with pytest.raises(JenkinsError) as raised:
        client.probe()
    assert raised.value.reason == "destination_refused"
    assert mock.requests == []


def test_a_host_removed_from_the_allowlist_is_refused_at_request_time(
    settings: Settings, mock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allowlist is re-checked on every call, not only when the setting was loaded."""
    client = JenkinsClient(
        settings,
        transport=httpx.WSGITransport(app=mock),
        resolver=lambda host, port: ["10.1.2.3"],
        circuit=CircuitBreaker(),
    )
    for key in ("DCP_JENKINS_URL", "DCP_JENKINS_USER", "DCP_JENKINS_TOKEN_REFERENCE"):
        monkeypatch.delenv(key)
    monkeypatch.setenv("DCP_SSRF_HOST_ALLOWLIST", "somewhere-else")
    monkeypatch.setenv("DCP_SSRF_INSECURE_HOSTS", "")
    get_settings.cache_clear()
    with pytest.raises(JenkinsError) as raised:
        client.probe()
    assert raised.value.reason == "destination_refused"
    assert mock.requests == []


def test_a_redirect_is_not_followed(harness: Harness) -> None:
    harness.mock.mode = "redirect"
    with pytest.raises(JenkinsError) as raised:
        harness.client.trigger(JOB, PARAMS)
    assert raised.value.reason == "provider_error" and not raised.value.may_have_acted
    assert [r["path"] for r in harness.requests()] == [
        "/crumbIssuer/api/json",
        "/job/demo/job/hello-pipeline/buildWithParameters",
    ]


# --- failure: reads are retried, a trigger is never ---------------------------------------


def test_a_read_is_retried_twice_with_backoff_then_fails_and_counts(harness: Harness) -> None:
    harness.mock.mode = "outage"
    with pytest.raises(JenkinsError) as raised:
        harness.client.probe()
    assert raised.value.reason == "provider_error"
    assert len(harness.requests()) == 3  # one attempt and two retries
    assert (
        len(harness.sleeps) == 2
        and 0.5 <= harness.sleeps[0] < 0.7
        and 1.0 <= harness.sleeps[1] < 1.2
    )
    assert harness.circuit.consecutive_failures == 1  # one logical call, one failure


def test_the_circuit_opens_after_repeated_outages_and_stops_calling(harness: Harness) -> None:
    harness.mock.mode = "outage"
    for _ in range(FAILURE_THRESHOLD):
        with pytest.raises(JenkinsError):
            harness.client.probe()
    before = len(harness.requests())
    with pytest.raises(JenkinsError) as raised:
        harness.client.probe()
    assert raised.value.reason == "circuit_open"
    assert len(harness.requests()) == before  # nothing went out
    assert harness.circuit.state() == "open"


def test_the_circuit_recovers_through_one_half_open_probe(settings: Settings, mock: Any) -> None:
    now = [0.0]
    circuit = CircuitBreaker(clock=lambda: now[0])
    client = JenkinsClient(
        settings,
        transport=httpx.WSGITransport(app=mock),
        resolver=lambda host, port: ["10.1.2.3"],
        circuit=circuit,
        sleep=lambda s: None,
    )
    mock.mode = "outage"
    for _ in range(FAILURE_THRESHOLD):
        with pytest.raises(JenkinsError):
            client.probe()
    assert circuit.state() == "open"
    mock.mode = "ok"
    now[0] += 31
    assert circuit.state() == "half_open"
    client.probe()
    assert circuit.state() == "closed"


def test_a_429_honours_a_numeric_retry_after(settings: Settings, mock: Any) -> None:
    answered = {"n": 0}

    def get(request: httpx.Request) -> httpx.Response | None:
        answered["n"] += 1
        if answered["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return None

    harness, _ = flaky(settings, mock, get=get)
    harness.client.probe()
    assert len(harness.sleeps) == 1 and 2.0 <= harness.sleeps[0] < 2.2


def test_a_huge_retry_after_is_capped(settings: Settings, mock: Any) -> None:
    answered = {"n": 0}

    def get(request: httpx.Request) -> httpx.Response | None:
        answered["n"] += 1
        return httpx.Response(503, headers={"Retry-After": "3600"}) if answered["n"] == 1 else None

    harness, _ = flaky(settings, mock, get=get)
    harness.client.probe()
    assert harness.sleeps[0] < 5.2


def test_a_timeout_on_a_read_is_retried_then_provider_timeout(
    settings: Settings, mock: Any
) -> None:
    harness, transport = flaky(settings, mock, get=lambda r: httpx.ReadTimeout("slow"))
    with pytest.raises(JenkinsError) as raised:
        harness.client.probe()
    assert raised.value.reason == "provider_timeout"
    assert len(transport.calls) == 3
    assert harness.circuit.last_error_reason == "provider_timeout"


@pytest.mark.parametrize(
    ("post", "reason", "may_have_acted"),
    [
        (lambda r: httpx.Response(503), "trigger_outcome_unknown", True),
        (lambda r: httpx.Response(500), "trigger_outcome_unknown", True),
        (lambda r: httpx.ReadTimeout("after the write"), "trigger_outcome_unknown", True),
        (lambda r: httpx.ReadError("reset"), "trigger_outcome_unknown", True),
        (lambda r: httpx.Response(200), "trigger_outcome_unknown", True),  # accepted, untrackable
        (lambda r: httpx.Response(201), "trigger_outcome_unknown", True),  # no Location
        (lambda r: httpx.ConnectError("never left"), "provider_unreachable", False),
        (lambda r: httpx.ConnectTimeout("never left"), "provider_unreachable", False),
        (lambda r: httpx.Response(400), "provider_error", False),  # refused: did not act
        (lambda r: httpx.Response(429), "provider_error", False),
        (lambda r: httpx.Response(401), "auth_failed", False),
        (lambda r: httpx.Response(403, text="No valid crumb"), "crumb_rejected", False),
    ],
)
def test_a_trigger_is_one_attempt_and_says_whether_jenkins_may_have_acted(
    settings: Settings, mock: Any, post: Any, reason: str, may_have_acted: bool
) -> None:
    harness, transport = flaky(settings, mock, post=post)
    with pytest.raises(JenkinsError) as raised:
        harness.client.trigger(JOB, PARAMS)
    assert (raised.value.reason, raised.value.may_have_acted) == (reason, may_have_acted)
    assert [c for c in transport.calls if c[0] == "POST"].__len__() == 1  # never retried
    assert harness.sleeps == []


def test_a_201_with_a_foreign_location_yields_only_the_number(
    settings: Settings, mock: Any
) -> None:
    """The header is data: its host is ignored and it is never followed."""
    harness, transport = flaky(
        settings,
        mock,
        post=lambda r: httpx.Response(
            201, headers={"Location": "http://attacker.invalid/queue/item/777/"}
        ),
    )
    assert harness.client.trigger(JOB, PARAMS) == QueueRef(777)
    assert all("attacker" not in path for _, path in transport.calls)


@pytest.mark.parametrize("location", ["", "http://x/y", "/queue/item/abc/", "/queue/item//"])
def test_a_201_with_an_unusable_location_is_outcome_unknown(
    settings: Settings, mock: Any, location: str
) -> None:
    harness, _ = flaky(
        settings, mock, post=lambda r: httpx.Response(201, headers={"Location": location})
    )
    with pytest.raises(JenkinsError) as raised:
        harness.client.trigger(JOB, PARAMS)
    assert raised.value.reason == "trigger_outcome_unknown"


# --- bounds -------------------------------------------------------------------------------


def test_console_text_is_capped_and_resumable(settings: Settings, mock: Any) -> None:
    big = b"x" * (CONSOLE_LIMIT_BYTES + 5_000)

    def get(request: httpx.Request) -> httpx.Response | None:
        if request.url.path.endswith("progressiveText"):
            return httpx.Response(
                200, content=big, headers={"X-Text-Size": str(len(big)), "X-More-Data": "false"}
            )
        return None

    harness, _ = flaky(settings, mock, get=get)
    chunk = harness.client.console(JOB, 1, offset=100)
    assert len(chunk.text) == CONSOLE_LIMIT_BYTES
    assert chunk.more is True and chunk.next_offset == 100 + CONSOLE_LIMIT_BYTES


def test_an_oversized_json_body_is_refused(settings: Settings, mock: Any) -> None:
    harness, _ = flaky(
        settings, mock, get=lambda r: httpx.Response(200, content=b"[" + b"1," * 600_000 + b"1]")
    )
    with pytest.raises(JenkinsError) as raised:
        harness.client.probe()
    assert raised.value.reason == "provider_error"


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'{"building": "yes"}', b'{"building": false, "result": "WEIRD"}'],
)
def test_a_malformed_answer_is_provider_error(settings: Settings, mock: Any, body: bytes) -> None:
    harness, _ = flaky(settings, mock, get=lambda r: httpx.Response(200, content=body))
    with pytest.raises(JenkinsError) as raised:
        harness.client.build(JOB, 1)
    assert raised.value.reason == "provider_error"


def test_inputs_are_checked_before_anything_is_sent(harness: Harness) -> None:
    with pytest.raises(ValueError, match="queue_id"):
        harness.client.queue_item(0)
    with pytest.raises(ValueError, match="number"):
        harness.client.build(JOB, 0)
    with pytest.raises(ValueError, match="offset"):
        harness.client.console(JOB, 1, offset=-1)
    from app.deployments.paths import InvalidJobPathError

    with pytest.raises(InvalidJobPathError):
        harness.client.trigger("../etc/passwd", PARAMS)
    with pytest.raises(InvalidJobPathError):
        harness.client.build("a/../b", 1)
    assert harness.requests() == []


# --- nothing leaks ------------------------------------------------------------------------


def test_the_token_never_reaches_a_log_an_error_a_repr_or_the_recorder(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    harness.mock.mode = "outage"
    with pytest.raises(JenkinsError) as raised:
        harness.client.probe()
    harness.mock.mode = "ok"
    harness.client.trigger(JOB, PARAMS)
    for text in (
        caplog.text,
        str(raised.value),
        repr(raised.value),
        repr(harness.client),
        str(harness.client.__dict__.keys()),
        str(harness.mock.requests),
    ):
        assert TOKEN not in text


def test_an_error_maps_to_an_api_error_without_jenkins_words() -> None:
    for reason in (
        "not_configured", "provider_unreachable", "provider_timeout", "provider_error",
        "circuit_open", "auth_failed", "job_not_found", "crumb_rejected",
        "trigger_outcome_unknown", "destination_refused",
    ):  # fmt: skip
        error = to_app_error(JenkinsError(reason))  # type: ignore[arg-type]
        assert error.details == {"reason": reason}
        assert "Jenkins could not complete" in error.message or reason == "not_configured"


def test_the_client_has_no_parameter_through_which_a_url_or_command_could_travel() -> None:
    """Signature inspection, as M4.1 does for adapters: this is rule 1 and rule 9, asserted."""
    forbidden = {"url", "uri", "host", "base_url", "headers", "command", "argv", "script", "cmd"}
    for name, method in inspect.getmembers(JenkinsClient, inspect.isfunction):
        if name.startswith("_"):
            continue
        names = set(inspect.signature(method).parameters) - {"self"}
        assert not names & forbidden, f"{name} takes {names & forbidden}"
    assert set(inspect.signature(JenkinsClient.trigger).parameters) == {
        "self",
        "job_path",
        "parameters",
    }


def test_the_module_level_breaker_is_its_own_instance() -> None:
    from app.deployments import jenkins
    from app.logs import client as loki
    from app.metrics import client as prometheus

    assert jenkins.breaker() is not loki.breaker()
    assert jenkins.breaker() is not prometheus.breaker()
