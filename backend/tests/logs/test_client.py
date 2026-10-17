"""The Loki client: parsing, the closed set of failure reasons, the breaker."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.core.config import Settings
from app.logs.client import LokiClient, LokiUnavailableError
from app.metrics.client import CircuitBreaker

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def client(
    handler, settings: Settings, *, url: str | None = "http://loki.invalid:3100"
) -> LokiClient:
    return LokiClient(
        settings=settings.model_copy(update={"loki_url": url}),
        transport=httpx.MockTransport(handler),
        circuit=CircuitBreaker(),
    )


def streams(*values: list, labels: dict | None = None, stats: dict | None = None) -> httpx.Response:
    data = {
        "resultType": "streams",
        "result": [{"stream": labels or {"server_id": "s"}, "values": list(values)}],
    }
    if stats is not None:
        data["stats"] = stats
    return httpx.Response(200, json={"status": "success", "data": data})


async def ask(c: LokiClient) -> object:
    return await c.query_range('{a="b"}', start_ns=1, end_ns=2, limit=10, direction="backward")


async def test_a_range_result_keeps_labels_lines_and_nanosecond_timestamps(
    settings: Settings,
) -> None:
    c = client(
        lambda r: streams(["1760000000000000001", "a"], ["1760000000000000000", "b"]), settings
    )
    result = await ask(c)
    assert [(e.timestamp_ns, e.line) for e in result.entries] == [
        (1760000000000000001, "a"),
        (1760000000000000000, "b"),
    ]
    assert result.entries[0].labels == {"server_id": "s"}


async def test_bytes_scanned_is_none_when_loki_does_not_say_never_zero(settings: Settings) -> None:
    assert (await ask(client(lambda r: streams(), settings))).bytes_scanned is None
    stats = {"summary": {"totalBytesProcessed": 77}}
    assert (await ask(client(lambda r: streams(stats=stats), settings))).bytes_scanned == 77


async def test_the_request_carries_the_bounds_and_follows_no_redirect(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return streams()

    await ask(client(handler, settings))
    params = dict(seen[0].url.params)
    assert params == {
        "query": '{a="b"}',
        "start": "1",
        "end": "2",
        "limit": "10",
        "direction": "backward",
    }
    assert seen[0].url.host == "loki.invalid"


async def test_malformed_values_are_skipped_not_fatal(settings: Settings) -> None:
    c = client(lambda r: streams(["x", "bad ts"], ["5"], ["6", "ok"]), settings)
    assert [e.line for e in (await ask(c)).entries] == ["ok"]


async def test_instant_counts_omit_servers_with_no_lines(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        result = [
            {"metric": {"server_id": "a"}, "value": [1, "7"]},
            {"metric": {}, "value": [1, "x"]},
        ]
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "vector", "result": result}}
        )

    assert await client(handler, settings).counts_by_server("q", at=NOW) == {"a": 7}


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(500, text="boom"), "provider_error"),
        (httpx.Response(400, text="parse error echoing the query"), "provider_error"),
        (httpx.Response(429), "provider_error"),
        (httpx.Response(302, headers={"Location": "http://evil.invalid/"}), "provider_error"),
        (httpx.Response(200, text="<html>not json</html>"), "provider_error"),
        (httpx.Response(200, json={"status": "error"}), "provider_error"),
        (httpx.Response(200, json={"status": "success", "data": "x"}), "provider_error"),
        (
            httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix"}}),
            "provider_error",
        ),
    ],
)
async def test_every_bad_answer_is_a_closed_reason_never_an_empty_result(
    settings: Settings, response: httpx.Response, reason: str
) -> None:
    with pytest.raises(LokiUnavailableError) as caught:
        await ask(client(lambda r: response, settings))
    assert caught.value.reason == reason


async def test_a_redirect_is_not_followed(settings: Settings) -> None:
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.host)
        return httpx.Response(302, headers={"Location": "http://elsewhere.invalid/x"})

    with pytest.raises(LokiUnavailableError):
        await ask(client(handler, settings))
    assert hits == ["loki.invalid"]


async def test_an_oversized_body_is_refused(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.logs.client.MAX_RESPONSE_BYTES", 10)
    with pytest.raises(LokiUnavailableError):
        await ask(client(lambda r: streams(["1", "a" * 100]), settings))


async def test_a_read_timeout_is_a_timeout_and_is_not_retried(settings: Settings) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LokiUnavailableError) as caught:
        await ask(client(handler, settings))
    assert (caught.value.reason, len(calls)) == ("provider_timeout", 1)


async def test_a_refused_connection_is_retried_exactly_once(settings: Settings) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LokiUnavailableError) as caught:
        await ask(client(handler, settings))
    assert (caught.value.reason, len(calls)) == ("provider_unreachable", 2)


async def test_unset_url_is_not_configured_and_makes_no_call(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be made")

    with pytest.raises(LokiUnavailableError) as caught:
        await ask(client(handler, settings, url=None))
    assert caught.value.reason == "not_configured"


async def test_the_circuit_opens_after_five_failures_and_stops_calling(settings: Settings) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500)

    c = client(handler, settings)
    for _ in range(5):
        with pytest.raises(LokiUnavailableError):
            await ask(c)
    with pytest.raises(LokiUnavailableError) as caught:
        await ask(c)
    assert (caught.value.reason, len(calls)) == ("circuit_open", 5)


async def test_a_failure_leaks_neither_the_body_nor_the_url(settings: Settings) -> None:
    with pytest.raises(LokiUnavailableError) as caught:
        await ask(client(lambda r: httpx.Response(400, text="SECRET-IN-BODY"), settings))
    assert "SECRET-IN-BODY" not in str(caught.value)
    assert "loki.invalid" not in str(caught.value)
