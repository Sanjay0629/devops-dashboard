"""Read-only Loki client: deadline, one connect retry, circuit breaker (AD-05).

Scope: **reads only**, and nothing here takes a URL, a path or a query from a
request. The base URL is configuration (``DCP_LOKI_URL``); the query strings are
rendered by ``app/logs/logql.py`` from validated fields. There is no push, no
delete and no admin call in this module. Why the URL is not run through
``validate_registered_url`` is written out in ``app/metrics/client.py`` and
applies here unchanged: it is deployment configuration, not a destination a user
supplied.

Shape follows ``app/metrics/client.py`` on purpose, and the breaker *class* is
imported from there rather than copied (it is generic). Loki gets its own breaker
**instance**: a Prometheus outage must not stop log search, nor the reverse.

Failure is an exception carrying a closed reason; the caller turns it into
``503 provider_unavailable`` / ``504 provider_timeout`` (docs/API_CONTRACTS.md
§19.1), never into an empty result. The response body of a failed call is never
returned or logged (mandatory rule 10) -- only its status.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.metrics.client import CircuitBreaker, Reason

logger = get_logger(__name__)

#: A page is at most 1 000 lines (plus tie-skip overfetch, see ``service``), each
#: bounded by Loki's own line-size limit; this stops a misbehaving upstream.
MAX_RESPONSE_BYTES: Final = 32 * 1024 * 1024

_QUERY_RANGE: Final = "/loki/api/v1/query_range"
_QUERY: Final = "/loki/api/v1/query"


class LokiUnavailableError(Exception):
    """Loki could not answer. Carries the reason, never the URL."""

    def __init__(self, reason: Reason, detail: str | None = None) -> None:
        super().__init__(detail or reason)
        self.reason: Reason = reason


@dataclass(frozen=True, slots=True)
class RawEntry:
    """One stored line as Loki returned it, before any redaction."""

    timestamp_ns: int
    labels: dict[str, str]
    line: str
    #: Position within its own stream's values. With the stream's labels it makes
    #: the order of equal-timestamp lines total (see ``service``): the *order of
    #: streams* in a Loki response is not something to depend on.
    index: int = 0


@dataclass(frozen=True, slots=True)
class RangeResult:
    entries: tuple[RawEntry, ...]
    #: ``None`` when Loki did not report it -- not ``0``.
    bytes_scanned: int | None


_BREAKER: Final[CircuitBreaker] = CircuitBreaker()


def breaker() -> CircuitBreaker:
    return _BREAKER


def reset_breaker() -> None:
    _BREAKER.reset()


@dataclass(frozen=True, slots=True)
class LokiClient:
    """``transport`` exists for tests, as on the Prometheus client."""

    settings: Settings = field(default_factory=get_settings)
    transport: httpx.AsyncBaseTransport | None = None
    circuit: CircuitBreaker = field(default_factory=breaker)

    @property
    def base_url(self) -> str:
        url = self.settings.loki_url
        if url is None:
            raise LokiUnavailableError("not_configured", "DCP_LOKI_URL is not set")
        return url

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not self.circuit.allow():
            raise LokiUnavailableError("circuit_open", "the Loki circuit is open")
        url = f"{self.base_url}{path}"
        try:
            payload = await self._request(url, params)
        except LokiUnavailableError as error:
            self.circuit.record_failure(error.reason)
            raise
        except asyncio.CancelledError:
            self.circuit.release_probe()
            raise
        self.circuit.record_success()
        return payload

    async def _request(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        """One attempt, then one retry only if the connection never opened."""
        try:
            return await self._attempt(url, params)
        except LokiUnavailableError as error:
            if error.reason != "provider_unreachable":
                raise
            logger.info("loki_retrying", reason=error.reason)
        return await self._attempt(url, params)

    async def _attempt(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.loki_timeout_seconds),
                # docs/SECURITY.md §19.5: a redirect from a log store is a
                # misconfiguration or an attempt to move the request elsewhere.
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.get(
                    url, params=params, headers={"Accept": "application/json"}
                )
        except (httpx.ConnectTimeout, httpx.ConnectError) as error:
            logger.warning("loki_unreachable", error=type(error).__name__)
            raise LokiUnavailableError("provider_unreachable") from error
        except httpx.TimeoutException as error:
            logger.warning("loki_timed_out", error=type(error).__name__)
            raise LokiUnavailableError("provider_timeout") from error
        except httpx.HTTPError as error:
            logger.warning("loki_transport_error", error=type(error).__name__)
            raise LokiUnavailableError("provider_unreachable") from error
        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 300:
            # 3xx is a redirect we did not follow (never followed, SECURITY §19.5);
            # 4xx is our query being wrong or Loki shedding load; 5xx is Loki.
            # Either way the honest answer is "unavailable", and the body (which
            # can echo the query) is neither logged nor returned.
            logger.warning("loki_rejected_request", status=response.status_code)
            raise LokiUnavailableError("provider_error")
        if len(response.content) > MAX_RESPONSE_BYTES:
            logger.warning("loki_response_too_large", size=len(response.content))
            raise LokiUnavailableError("provider_error")
        try:
            body: Any = response.json()
        except ValueError as error:
            logger.warning("loki_response_not_json")
            raise LokiUnavailableError("provider_error") from error
        if not isinstance(body, dict) or body.get("status") != "success":
            logger.warning("loki_query_failed")
            raise LokiUnavailableError("provider_error")
        data = body.get("data")
        if not isinstance(data, dict):
            logger.warning("loki_response_malformed")
            raise LokiUnavailableError("provider_error")
        return data

    async def query_range(
        self, logql: str, *, start_ns: int, end_ns: int, limit: int, direction: str
    ) -> RangeResult:
        """A log query. ``start`` is inclusive and ``end`` exclusive (measured, M3.2 spike)."""
        data = await self._get(
            _QUERY_RANGE,
            {
                "query": logql,
                "start": str(start_ns),
                "end": str(end_ns),
                "limit": str(limit),
                "direction": direction,
            },
        )
        if data.get("resultType") != "streams":
            raise LokiUnavailableError("provider_error")
        result = data.get("result")
        entries: list[RawEntry] = []
        for stream in result if isinstance(result, list) else ():
            if not isinstance(stream, dict):
                continue
            raw_labels = stream.get("stream")
            labels = (
                {str(k): str(v) for k, v in raw_labels.items()}
                if isinstance(raw_labels, dict)
                else {}
            )
            for position, value in enumerate(stream.get("values") or ()):
                if isinstance(value, list) and len(value) >= 2:
                    try:
                        entries.append(RawEntry(int(value[0]), labels, str(value[1]), position))
                    except (TypeError, ValueError):
                        continue
        return RangeResult(tuple(entries), _bytes_scanned(data))

    async def counts_by_server(self, logql: str, *, at: datetime) -> dict[str, int]:
        """An instant ``sum by (server_id)`` query. A server with no lines is **absent**."""
        data = await self._get(_QUERY, {"query": logql, "time": str(int(at.timestamp()))})
        if data.get("resultType") != "vector":
            raise LokiUnavailableError("provider_error")
        counts: dict[str, int] = {}
        result = data.get("result")
        for sample in result if isinstance(result, list) else ():
            if not isinstance(sample, dict):
                continue
            metric, value = sample.get("metric"), sample.get("value")
            if isinstance(metric, dict) and isinstance(value, list) and len(value) == 2:
                try:
                    counts[str(metric.get("server_id"))] = int(float(str(value[1])))
                except ValueError:
                    continue
        return counts


def _bytes_scanned(data: dict[str, Any]) -> int | None:
    stats = data.get("stats")
    summary = stats.get("summary") if isinstance(stats, dict) else None
    value = summary.get("totalBytesProcessed") if isinstance(summary, dict) else None
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
