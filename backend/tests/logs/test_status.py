"""The §26.1 Loki row: four answers an operator must be able to tell apart."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from app.core.config import Settings, get_settings
from app.logs.client import LokiClient, reset_breaker
from app.logs.status import loki_status
from app.metrics.client import CircuitBreaker


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    reset_breaker()
    yield
    reset_breaker()
    get_settings.cache_clear()


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCP_LOKI_URL", "http://loki.invalid:3100")
    get_settings.cache_clear()


def client(handler, settings: Settings) -> LokiClient:
    return LokiClient(
        settings=settings.model_copy(update={"loki_url": "http://loki.invalid:3100"}),
        transport=httpx.MockTransport(handler),
        circuit=CircuitBreaker(),
    )


def vector(_request: httpx.Request) -> httpx.Response:
    body = {"status": "success", "data": {"resultType": "vector", "result": []}}
    return httpx.Response(200, json=body)


async def test_unset_url_is_not_configured_and_asks_nothing(settings: Settings) -> None:
    get_settings.cache_clear()
    row = await loki_status()
    assert (row.name, row.type, row.status) == ("Loki", "logs", "not_configured")
    assert row.circuit_state is None and row.last_success_at is None


async def test_a_reachable_loki_is_ok_and_the_probe_selects_no_stream(
    settings: Settings, configured: None
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["query"])
        return vector(request)

    row = await loki_status(client(handler, settings))
    assert (row.status, seen) == ("ok", ["vector(1)"])


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(500), "provider_error"),
        (httpx.Response(200, text="not json"), "provider_error"),
    ],
)
async def test_an_unavailable_loki_is_a_row_with_a_reason_never_an_exception(
    settings: Settings, configured: None, response: httpx.Response, reason: str
) -> None:
    row = await loki_status(client(lambda r: response, settings))
    assert (row.status, row.last_error_message) == ("unavailable", reason)
    assert "loki.invalid" not in (row.last_error_message or "")


async def test_a_refused_connection_is_unavailable_too(
    settings: Settings, configured: None
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    row = await loki_status(client(refuse, settings))
    assert (row.status, row.last_error_message) == ("unavailable", "provider_unreachable")
