"""The §26.1 Prometheus row, which is M2.1's integration-status deliverable.

What this file is about is the difference between four answers an operator has
to be able to tell apart — *not configured*, *working*, *working but recently
failing*, and *unreachable* — and the rule that none of them is ever an
exception. §26.1 exists to answer "is the portal broken, or is the thing it
monitors broken?" (PRD §4.11), and an endpoint that fails when its dependency
fails cannot answer that question at all.

The shared module-level breaker is reset around every test here, because this
is the one place that uses it rather than an injected one — and a breaker that
carried state from the previous test would make these pass or fail in file
order.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from app.core.config import get_settings
from app.metrics import service
from app.metrics.client import (
    FAILURE_THRESHOLD,
    CircuitBreaker,
    PrometheusClient,
    breaker,
    reset_breaker,
)

PROMETHEUS_URL = "http://prometheus.invalid:9090"


@pytest.fixture(autouse=True)
def _clean_breaker() -> Iterator[None]:
    reset_breaker()
    yield
    reset_breaker()


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DCP_PROMETHEUS_URL", PROMETHEUS_URL)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def answering(value: str = "1") -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "vector", "result": [{"metric": {}, "value": [0, value]}]},
            },
        )

    return httpx.MockTransport(handler)


def refusing() -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    return httpx.MockTransport(handler)


async def test_unconfigured_is_not_configured_and_probes_nothing() -> None:
    """No URL set, so no request is made and nothing is claimed about it."""
    get_settings.cache_clear()
    row = await service.prometheus_status()
    assert row.status == "not_configured"
    assert row.circuit_state is None
    assert row.last_success_at is None
    assert row.last_error_at is None
    assert "DCP_PROMETHEUS_URL" in (row.last_error_message or "")


async def test_a_reachable_prometheus_is_ok_with_a_closed_circuit(
    configured: None,
) -> None:
    client = PrometheusClient(settings=get_settings(), transport=answering())
    row = await service.prometheus_status(client)
    assert row.status == "ok"
    assert row.circuit_state == "closed"
    assert row.last_success_at is not None
    assert row.last_error_message is None


async def test_an_unreachable_prometheus_is_unavailable_never_an_exception(
    configured: None,
) -> None:
    """PRD §4.4 and CLAUDE.md §5: a dead dependency is a state, not a 5xx."""
    client = PrometheusClient(settings=get_settings(), transport=refusing())
    row = await service.prometheus_status(client)
    assert row.status == "unavailable"
    assert row.last_error_message == "provider_unreachable"
    assert row.last_error_at is not None
    # Still closed: one failure is not an outage, and opening on the first
    # error would take the page down for a single dropped packet.
    assert row.circuit_state == "closed"


async def test_a_recovery_reads_ok_and_still_shows_the_last_failure(
    configured: None,
) -> None:
    """Three states, and the evidence for the fourth left visible.

    A recovered Prometheus is ``ok`` — this milestone emits no ``degraded``,
    because a successful probe resets the breaker's consecutive-failure count
    and so any flap verdict built on that count would always be false. What
    the row keeps is both timestamps and the previous reason, so an operator
    can see that an ``ok`` Prometheus failed moments ago and judge for
    themselves.
    """
    failing = PrometheusClient(settings=get_settings(), transport=refusing())
    await service.prometheus_status(failing)
    assert breaker().consecutive_failures == 1

    working = PrometheusClient(settings=get_settings(), transport=answering())
    row = await service.prometheus_status(working)
    assert row.status == "ok"
    assert row.last_success_at is not None
    assert row.last_error_at is not None
    assert row.last_error_message == "provider_unreachable"
    assert breaker().consecutive_failures == 0


async def test_an_open_circuit_is_reported_as_open(configured: None) -> None:
    """The state §26.1 names, surfaced rather than hidden behind the symptom."""
    client = PrometheusClient(settings=get_settings(), transport=refusing())
    for _ in range(FAILURE_THRESHOLD):
        await service.prometheus_status(client)
    row = await service.prometheus_status(client)
    assert row.circuit_state == "open"
    assert row.status == "unavailable"
    # Fast-failed by the breaker rather than by another refused connection.
    assert row.last_error_message == "circuit_open"


async def test_the_breaker_the_row_reports_is_the_shared_one(configured: None) -> None:
    """Otherwise the page reports a breaker nothing uses.

    The client under test gets its own breaker here, which is exactly the
    mistake being guarded against: the row must still read the process-wide
    one, because that is the breaker the request path consults.
    """
    private = CircuitBreaker()
    client = PrometheusClient(settings=get_settings(), transport=refusing(), circuit=private)
    row = await service.prometheus_status(client)
    assert private.consecutive_failures == 1
    assert breaker().consecutive_failures == 0
    assert row.circuit_state == breaker().state()


async def test_the_full_page_lists_every_integration_the_contract_names() -> None:
    """§26.1 names seven integration classes; all seven appear.

    Omitting the unbuilt ones would make the page read as "this is
    everything", which is the same substitution PRD §4.2 forbids for a count:
    an operator could not tell a missing integration from a healthy one.
    """
    get_settings.cache_clear()
    page = await service.integration_status()
    names = [row.name for row in page.integrations]
    assert names == [
        "Identity provider",
        "Prometheus",
        "Loki",
        "Cloud accounts",
        "Alertmanager",
        "Jenkins",
        "Secret store",
        "Notification channels",
    ]
    # Every one that is not configured says so (Loki since M3.2 and Alertmanager since M3.3
    # are live probes, unconfigured here because the test environment sets no URL).
    for row in page.integrations[2:]:
        assert row.status == "not_configured"
        assert row.last_error_message
    # No row leaks a URL, a credential or a provider payload (§20).
    for row in page.integrations:
        assert "://" not in (row.last_error_message or "")


async def test_the_cloud_accounts_row_says_why_it_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M4.1: an adapter layer exists, a real adapter does not, and demo mode is not a probe."""
    reasons = {}
    for demo in ("false", "true"):
        monkeypatch.setenv("DCP_DEMO_MODE", demo)
        get_settings.cache_clear()
        page = await service.integration_status()
        row = next(r for r in page.integrations if r.name == "Cloud accounts")
        assert row.status == "not_configured"
        assert row.demo_mode is (demo == "true")
        reasons[demo] = row.last_error_message
    get_settings.cache_clear()
    assert "M4.3" in (reasons["false"] or "")
    assert "Demonstration mode" in (reasons["true"] or "")
