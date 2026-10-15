"""The Prometheus client: error-class mapping, breaker, timeouts (M2.1).

Beyond the two files IMPLEMENTATION_PLAN.md M2.1 names, because the plan's
own test list asks for "error-class mapping" and "timeout and circuit-breaker
behaviour with a stub that hangs", and neither belongs in a file about
templates or guards.

Everything here runs against ``httpx.MockTransport``. That is not a
convenience: a hang and a connect failure are the two behaviours a real
container cannot be made to produce on demand, and a test that waited for a
real timeout would take ten seconds to assert one branch.

The property every case shares: **a failure is a reason, never an exception
the API renders as a 5xx and never a zero** (PRD §4.4, CLAUDE.md §5).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.metrics import templates
from app.metrics.client import (
    FAILURE_THRESHOLD,
    MAX_RESPONSE_BYTES,
    RESET_AFTER_SECONDS,
    CircuitBreaker,
    PrometheusClient,
    PrometheusUnavailableError,
)

PROMETHEUS_URL = "http://prometheus.invalid:9090"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    monkeypatch.setenv("DCP_PROMETHEUS_URL", PROMETHEUS_URL)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def responder(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def ok(value: str = "1.5", timestamp: float = 1_760_000_000.0) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {"metric": {"instance": "node-exporter:9100"}, "value": [timestamp, value]}
                    ],
                },
            },
        )

    return responder(handler)


def client(settings: Settings, transport: httpx.MockTransport) -> PrometheusClient:
    """A client with a breaker of its own, so no test leaks state into another."""
    return PrometheusClient(settings=settings, transport=transport, circuit=CircuitBreaker())


def static(status: int, content: bytes) -> httpx.MockTransport:
    """A transport that answers every request with the same raw body."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content, headers={"content-type": "application/json"})

    return responder(handler)


# --- configuration -----------------------------------------------------------


async def test_an_unconfigured_prometheus_is_not_configured_not_unavailable() -> None:
    """Two different facts, and an operator has to be able to tell them apart.

    "Nobody set this up" is not "the monitoring server is down", and reporting
    the first as the second sends someone to debug a host that was never
    configured (CLAUDE.md §7).
    """
    get_settings.cache_clear()
    unconfigured = PrometheusClient(settings=get_settings(), transport=ok())
    with pytest.raises(PrometheusUnavailableError) as caught:
        await unconfigured.query("vector(1)")
    assert caught.value.reason == "not_configured"


async def test_the_url_comes_from_settings_and_nowhere_else(settings: Settings) -> None:
    """Mandatory rule 9, asserted rather than promised.

    The request's destination is recorded by the transport, so this is the
    test that a template, a label or a response cannot influence where the
    client connects.
    """
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    await client(settings, responder(handler)).query(
        templates.render("agent_up", "LINUX", ["node-exporter:9100"])
    )
    assert len(seen) == 1
    assert str(seen[0]).startswith(f"{PROMETHEUS_URL}/api/v1/query?")
    assert seen[0].host == "prometheus.invalid"


async def test_every_call_carries_a_deadline_at_both_ends(settings: Settings) -> None:
    """Ours as an httpx timeout, and Prometheus's own as a query parameter.

    Without the second, a query that is expensive server-side keeps running
    after we have stopped waiting for it.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    await client(settings, responder(handler)).query("vector(1)")
    assert seen[0].url.params["timeout"] == "10s"
    assert seen[0].extensions["timeout"] == {
        "connect": 10.0,
        "pool": 10.0,
        "read": 10.0,
        "write": 10.0,
    }


# --- parsing -----------------------------------------------------------------


async def test_an_explicit_evaluation_time_is_sent(settings: Settings) -> None:
    """§15.1 reads "current values", which still needs a pinned instant.

    Without it, several templates read at slightly different wall-clock
    moments and the response's single ``collected_at`` is a lie about at
    least one of them.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    at = datetime(2026, 10, 16, 12, 0, 30, tzinfo=UTC)
    await client(settings, responder(handler)).query("vector(1)", at=at)
    assert seen[0].url.params["time"] == f"{at.timestamp():.3f}"


async def test_a_sample_is_parsed_with_its_timestamp(settings: Settings) -> None:
    samples = await client(settings, ok("42.25")).query("vector(1)")
    assert len(samples) == 1
    assert samples[0].value == 42.25
    assert samples[0].labels == {"instance": "node-exporter:9100"}
    assert samples[0].timestamp.isoformat() == "2025-10-09T08:53:20+00:00"


@pytest.mark.parametrize("unusable", ["NaN", "+Inf", "-Inf", "not-a-number"])
async def test_an_unusable_value_becomes_null_and_never_zero(
    settings: Settings, unusable: str
) -> None:
    """PRD §4.2: unavailable data is never replaced with a zero value.

    ``NaN`` comes back from a rate window with one sample and ``+Inf`` from a
    division by zero, so these are ordinary answers rather than corruption —
    and ``0`` would read as "this host is idle".
    """
    samples = await client(settings, ok(unusable)).query("vector(1)")
    assert samples[0].value is None


async def test_a_range_gap_stays_a_missing_point(settings: Settings) -> None:
    """Prometheus omits a timestamp it has no sample for; nothing invents one.

    Aligning these onto the grid a client asked for, with the holes left as
    holes, is M2.2's job. The client's contribution is not to fill them here.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"instance": "node-exporter:9100"},
                            # 0, 15 and 45 seconds: the 30-second point is
                            # absent, not null and not zero.
                            "values": [[0, "1"], [15, "2"], [45, "4"]],
                        }
                    ],
                },
            },
        )

    series = await client(settings, responder(handler)).query_range(
        "vector(1)",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        step_seconds=15,
    )
    assert [point[1] for point in series[0].points] == [1.0, 2.0, 4.0]
    assert [point[0].timestamp() for point in series[0].points] == [0.0, 15.0, 45.0]


async def test_a_malformed_payload_is_a_reason_not_a_crash(settings: Settings) -> None:
    """Provider output is untrusted data (mandatory rule 10)."""
    for body in (
        b"not json at all",
        json.dumps({"status": "error", "errorType": "bad_data"}).encode(),
        json.dumps({"status": "success"}).encode(),
        json.dumps({"status": "success", "data": "not an object"}).encode(),
        json.dumps(["not", "an", "object"]).encode(),
    ):
        with pytest.raises(PrometheusUnavailableError) as caught:
            await client(settings, static(200, body)).query("vector(1)")
        assert caught.value.reason == "provider_error", body


async def test_a_result_that_is_not_a_list_is_an_empty_answer(settings: Settings) -> None:
    """Empty is a legitimate Prometheus answer: no series matched the selector.

    It is *not* an error, and it is not a zero either — the caller turns it
    into ``unavailable`` with a reason, which is M2.2's mapping.
    """
    transport = responder(
        lambda _request: httpx.Response(200, json={"status": "success", "data": {"result": {}}})
    )
    assert await client(settings, transport).query("vector(1)") == ()
    assert (
        await client(settings, transport).query_range(
            "vector(1)",
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            step_seconds=15,
        )
        == ()
    )


async def test_a_malformed_result_entry_is_skipped_not_guessed_at(
    settings: Settings,
) -> None:
    """Provider output is untrusted data (mandatory rule 10).

    A result array with junk in it must not crash the request and must not
    produce a half-built sample either. The well-formed entries come back and
    the rest are dropped — which for a metrics read is the honest answer,
    because a sample we could not parse is a sample we do not have.
    """
    body = json.dumps(
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    "not an object",
                    {"metric": {}, "value": "not a pair"},
                    {"metric": {}, "value": [1]},
                    {"metric": "not a mapping", "value": [1_000.0, "7"]},
                    {"metric": {"instance": "a:9100"}, "value": [1_000.0, "8"]},
                ],
            },
        }
    ).encode()
    samples = await client(settings, static(200, body)).query("vector(1)")
    assert [sample.value for sample in samples] == [7.0, 8.0]
    assert samples[0].labels == {}

    ranged = json.dumps(
        {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    "not an object",
                    {"metric": {}, "values": "not a list"},
                    {"metric": {}, "values": [[1_000.0, "1"], "junk", [1_010.0]]},
                ],
            },
        }
    ).encode()
    series = await client(settings, static(200, ranged)).query_range(
        "vector(1)",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        step_seconds=15,
    )
    assert len(series) == 1
    assert [point[1] for point in series[0].points] == [1.0]


async def test_an_oversized_response_is_refused(settings: Settings) -> None:
    """A bound on what we will parse (docs/SECURITY.md §19.6)."""
    oversized = b'{"status":"success","data":{"result":[]}}' + b" " * (MAX_RESPONSE_BYTES + 1)
    with pytest.raises(PrometheusUnavailableError) as caught:
        await client(settings, static(200, oversized)).query("vector(1)")
    assert caught.value.reason == "provider_error"


# --- error-class mapping -----------------------------------------------------


@pytest.mark.parametrize(
    ("raised", "reason"),
    [
        (httpx.ReadTimeout("hung"), "provider_timeout"),
        (httpx.WriteTimeout("hung"), "provider_timeout"),
        (httpx.PoolTimeout("hung"), "provider_timeout"),
        (httpx.ConnectTimeout("no route"), "provider_unreachable"),
        (httpx.ConnectError("refused"), "provider_unreachable"),
        (httpx.ReadError("reset"), "provider_unreachable"),
    ],
)
async def test_each_transport_failure_maps_to_its_reason(
    settings: Settings, raised: Exception, reason: str
) -> None:
    """A stub that hangs, a stub that refuses, and a stub that resets.

    ``ConnectTimeout`` is deliberately *not* ``provider_timeout``: a
    connection that was never established is the unreachable case, and it is
    the only one this client retries.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise raised

    with pytest.raises(PrometheusUnavailableError) as caught:
        await client(settings, responder(handler)).query("vector(1)")
    assert caught.value.reason == reason


@pytest.mark.parametrize("status", [400, 404, 422, 500, 502, 503])
async def test_an_http_error_is_never_rendered_as_a_5xx(settings: Settings, status: int) -> None:
    """The reason this exception is not an ``AppError``.

    If it were, a Prometheus 503 escaping a caller would become a DCP 503 for
    a page that is otherwise fine. PRD §4.4 wants the section marked
    unavailable instead, which only works if the exception cannot be rendered.
    """
    transport = responder(lambda _request: httpx.Response(status, text="nope"))
    with pytest.raises(PrometheusUnavailableError) as caught:
        await client(settings, transport).query("vector(1)")
    assert caught.value.reason == "provider_error"
    assert not isinstance(caught.value, AppError)


async def test_a_failure_never_echoes_the_upstream_body_or_host(settings: Settings) -> None:
    """docs/SECURITY.md §20: no provider payload, no internal hostname."""
    transport = responder(
        lambda _request: httpx.Response(500, text="prometheus.invalid: internal detail 10.0.0.9")
    )
    with pytest.raises(PrometheusUnavailableError) as caught:
        await client(settings, transport).query("vector(1)")
    rendered = str(caught.value)
    assert "prometheus.invalid" not in rendered
    assert "10.0.0.9" not in rendered
    assert "internal detail" not in rendered


# --- retry -------------------------------------------------------------------


async def test_a_refused_connection_is_retried_once(settings: Settings) -> None:
    """Safe because every call here is a read.

    Nothing in this module changes state, so there is no blind-retry hazard
    of the kind CLAUDE.md §5 forbids — the thing being retried is a GET.
    """
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    assert await client(settings, responder(handler)).query("vector(1)") == ()
    assert len(attempts) == 2


async def test_a_read_timeout_is_not_retried(settings: Settings) -> None:
    """Retrying would quietly double the 10 s deadline §15.2 publishes."""
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ReadTimeout("hung")

    with pytest.raises(PrometheusUnavailableError):
        await client(settings, responder(handler)).query("vector(1)")
    assert len(attempts) == 1


async def test_the_retry_is_bounded_at_one(settings: Settings) -> None:
    """A permanently refused connection costs two attempts, not a loop."""
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("refused")

    with pytest.raises(PrometheusUnavailableError) as caught:
        await client(settings, responder(handler)).query("vector(1)")
    assert caught.value.reason == "provider_unreachable"
    assert len(attempts) == 2


# --- circuit breaker ---------------------------------------------------------


class FakeClock:
    """A monotonic clock the test advances, so no test sleeps."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_the_breaker_opens_on_consecutive_failures_only() -> None:
    """Consecutive: a success in between is a working dependency.

    Counting total failures instead would open the circuit on a server that
    fails one request in ten for a decade.
    """
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(FAILURE_THRESHOLD - 1):
        breaker.record_failure("provider_timeout")
    assert breaker.state() == "closed"
    breaker.record_success()
    for _ in range(FAILURE_THRESHOLD - 1):
        breaker.record_failure("provider_timeout")
    assert breaker.state() == "closed"
    breaker.record_failure("provider_timeout")
    assert breaker.state() == "open"
    assert breaker.allow() is False


def test_the_breaker_half_opens_and_allows_exactly_one_probe() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("provider_unreachable")
    assert breaker.allow() is False

    clock.now += RESET_AFTER_SECONDS - 0.01
    assert breaker.state() == "open"
    clock.now += 0.02
    assert breaker.state() == "half_open"
    assert breaker.allow() is True
    # One probe, not a flood: everything else waits for its verdict.
    assert breaker.allow() is False
    assert breaker.allow() is False


def test_a_successful_probe_closes_the_breaker() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("provider_error")
    clock.now += RESET_AFTER_SECONDS
    assert breaker.allow() is True
    breaker.record_success()
    assert breaker.state() == "closed"
    assert breaker.consecutive_failures == 0
    assert breaker.allow() is True


def test_a_failed_probe_reopens_for_a_full_cooldown() -> None:
    """Otherwise the circuit stays half-open and probes on every request.

    Which is the failure mode a breaker exists to prevent: a dead upstream
    being hammered at full request rate.
    """
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("provider_error")
    clock.now += RESET_AFTER_SECONDS
    assert breaker.allow() is True
    breaker.record_failure("provider_error")
    assert breaker.state() == "open"
    clock.now += RESET_AFTER_SECONDS - 0.01
    assert breaker.state() == "open"


async def test_an_open_circuit_fails_fast_without_a_request(settings: Settings) -> None:
    """The point of the breaker: no socket, no deadline, an immediate reason."""
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("refused")

    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    prometheus = PrometheusClient(settings=settings, transport=responder(handler), circuit=breaker)
    for _ in range(FAILURE_THRESHOLD):
        with pytest.raises(PrometheusUnavailableError):
            await prometheus.query("vector(1)")
    assert breaker.state() == "open"
    before = len(attempts)

    with pytest.raises(PrometheusUnavailableError) as caught:
        await prometheus.query("vector(1)")
    assert caught.value.reason == "circuit_open"
    assert len(attempts) == before


async def test_one_call_counts_as_one_failure_even_with_the_retry(
    settings: Settings,
) -> None:
    """Otherwise the retry halves the effective threshold.

    Five calls should open the circuit, not two and a half.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    prometheus = PrometheusClient(settings=settings, transport=responder(handler), circuit=breaker)
    for _ in range(FAILURE_THRESHOLD - 1):
        with pytest.raises(PrometheusUnavailableError):
            await prometheus.query("vector(1)")
    assert breaker.consecutive_failures == FAILURE_THRESHOLD - 1
    assert breaker.state() == "closed"


async def test_a_success_records_the_time_the_status_page_reports(
    settings: Settings,
) -> None:
    """§26.1 carries ``last_success_at``, so the breaker has to hold it."""
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    prometheus = PrometheusClient(settings=settings, transport=ok(), circuit=breaker)
    assert breaker.last_success_at is None
    await prometheus.probe()
    assert breaker.last_success_at is not None
    assert breaker.last_error_at is None


async def test_a_probe_with_no_sample_is_a_provider_error(settings: Settings) -> None:
    """``vector(1)`` always returns one sample; nothing back means not Prometheus."""
    transport = responder(
        lambda _request: httpx.Response(200, json={"status": "success", "data": {"result": []}})
    )
    prometheus = PrometheusClient(settings=settings, transport=transport, circuit=CircuitBreaker())
    with pytest.raises(PrometheusUnavailableError) as caught:
        await prometheus.probe()
    assert caught.value.reason == "provider_error"
