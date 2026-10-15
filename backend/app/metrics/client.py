"""Read-only Prometheus client: timeouts, one bounded retry, circuit breaker.

Scope: **reads only**. There is no write, no admin API call, no rule reload
and no delete-series path in this module, and nothing here takes a URL, a
path, a query or a label from a request, a database row, an alert payload or a
provider response. The base URL comes from settings; the query comes from
``app/metrics/templates.py``. That is the whole of mandatory rule 9's
requirement for this surface, and it is asserted by a test rather than
promised here.

---

**Does the Prometheus URL go through ``app/core/ssrf.validate_registered_url``?
No.** Resolved deliberately, because mandatory rule 9 says outbound
destinations are checked against the host allowlist and it would be easy to
read that as covering this:

1. ``validate_registered_url`` is **write-time validation of a URL that
   arrived in an API request** — its own docstring says so, and its callers
   are §18.2's ``health_check_config`` and ``repository_reference``.
   ``DCP_PROMETHEUS_URL`` arrives in the process environment from the
   deployment platform, exactly like ``DCP_DATABASE_URL``,
   ``DCP_REDIS_URL`` and ``DCP_OIDC_ISSUER``, none of which is allowlisted
   either. An allowlist cannot constrain someone who also sets the
   allowlist; gating it would be self-authorisation wearing a control's
   clothes.
2. §19.4's denied-range check — loopback, link-local, RFC1918 — would
   **deny the only addresses an internal Prometheus can have**. The local
   stack reaches it at ``prometheus:9090`` on a private Docker network, and
   a real deployment reaches it on an internal address too. That check
   exists because a *user*-supplied destination must not be steered at the
   cloud metadata endpoint; applied to an operator-configured monitoring
   host it protects nothing and breaks everything.
3. Gating it would force ``DCP_SSRF_HOST_ALLOWLIST=prometheus`` as the
   shipped default. That list governs the **user-supplied** surface, so
   widening it to admit an operator-supplied destination would make
   ``http://prometheus:9090`` a registrable application health-check target
   as a side effect. That is a net loss of control, and it is PROGRESS issue
   45 in reverse: there, an empty allowlist made a legitimate write
   impossible; here, a populated one would make an illegitimate write
   possible.

What §19 *does* require of a fetch, this module does: the scheme is
http(s)-only with https enforced outside the ``local`` environment, there are
no embedded credentials and no fragment (all four checked at load time in
``app/core/config.py``), **redirects are not followed**, the response size is
bounded, and every call carries a deadline at both ends.

---

**Failure is a data state, never a 5xx.** :class:`PrometheusUnavailableError` is a
plain exception and not an ``AppError`` on purpose: if it ever escaped a
caller it would surface as an unhandled error and get logged, rather than
being rendered to the client as a ``503`` for a page that is otherwise fine.
PRD §4.4 and CLAUDE.md §5 require the opposite shape — the section reports
``data_state: "unavailable"`` with a reason, and never a zero.

**Retry policy.** Exactly one retry, and only for a connection that was never
established (``ConnectError``/``ConnectTimeout``). Every call here is a read,
so retrying is safe in principle — there is no state-changing provider call
in this module to blind-retry, which is the hazard CLAUDE.md §5 names. A
*read* timeout is still not retried: the deadline the contract publishes is
10 s (docs/API_CONTRACTS.md §15.2), and retrying after one would quietly
double it.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Literal

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: ``unavailable_reason`` values this module produces. A closed set, because
#: the frontend renders a message per reason and an unexpected string would
#: render as itself.
Reason = Literal[
    "not_configured",
    "provider_timeout",
    "provider_unreachable",
    "provider_error",
    "circuit_open",
]

CircuitState = Literal["closed", "open", "half_open"]

#: Consecutive failures that open the circuit. Deliberately module constants
#: rather than settings: two more settings would each need a ``.env.example``
#: entry *and* a Compose passthrough to have any effect (PROGRESS issue 54),
#: and nothing has yet asked to tune these. They move to settings the first
#: time a deployment needs a different answer.
FAILURE_THRESHOLD: Final = 5
#: How long the circuit stays open before one probe is allowed through.
RESET_AFTER_SECONDS: Final = 30.0

#: Cap on a response we parse. A range query is bounded to 11 000 points per
#: series (docs/API_CONTRACTS.md §15.2) across at most 20 servers, which is
#: roughly 7 MB of JSON; this leaves headroom without letting a misconfigured
#: upstream exhaust memory.
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024

_QUERY_PATH: Final = "/api/v1/query"
_RANGE_PATH: Final = "/api/v1/query_range"


class PrometheusUnavailableError(Exception):
    """Prometheus could not answer. Carries the reason, never the URL."""

    def __init__(self, reason: Reason, detail: str | None = None) -> None:
        super().__init__(detail or reason)
        self.reason: Reason = reason


@dataclass(frozen=True, slots=True)
class Sample:
    """One instant value, with the timestamp Prometheus evaluated it at.

    ``value`` is ``None`` for ``NaN`` and infinities. Prometheus returns
    ``"NaN"`` from an empty rate window and ``"+Inf"`` from a division by
    zero; both mean "no usable number", and ``float("nan")`` serialises to
    invalid JSON while ``0`` would be the substitution PRD §4.2 forbids.
    """

    labels: Mapping[str, str]
    timestamp: datetime
    value: float | None


@dataclass(frozen=True, slots=True)
class Series:
    """One range series. Gaps are **missing points**, not nulls and not zeros.

    Prometheus omits a timestamp it has no sample for. Nothing here invents
    one: aligning these points onto the grid a client asked for — and leaving
    the holes as holes — is M2.2's job (docs/API_CONTRACTS.md §15.2).
    """

    labels: Mapping[str, str]
    points: tuple[tuple[datetime, float | None], ...]


@dataclass
class CircuitBreaker:
    """Per-process breaker with a single half-open probe.

    ``ponytail: per-process and not concurrency-exact`` — two coroutines can
    both pass ``allow()`` in the half-open state, and N replicas each keep
    their own count. Both are acceptable while the only consequence is an
    extra probe against a monitoring server. A Redis-backed breaker is the
    upgrade path if the state has to be shared or reported consistently
    across replicas (docs/ARCHITECTURE.md §9 names the circuit state on
    §26.1, which is where the inconsistency would show).
    """

    failure_threshold: int = FAILURE_THRESHOLD
    reset_after_seconds: float = RESET_AFTER_SECONDS
    #: Monotonic, because the open/half-open transition is a duration. A wall
    #: clock can step backwards over an NTP correction and strand the circuit.
    clock: Callable[[], float] = time.monotonic
    consecutive_failures: int = 0
    opened_at: float | None = None
    probe_taken: bool = False
    #: Reported on §26.1, so these are wall-clock and nullable.
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_reason: Reason | None = None

    def state(self) -> CircuitState:
        if self.opened_at is None:
            return "closed"
        if self.clock() - self.opened_at >= self.reset_after_seconds:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        """May a call go out now? Consumes the half-open probe if it does."""
        state = self.state()
        if state == "closed":
            return True
        if state == "open":
            return False
        if self.probe_taken:
            return False
        self.probe_taken = True
        return True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None
        self.probe_taken = False
        self.last_success_at = datetime.now(UTC)

    def record_failure(self, reason: Reason) -> None:
        self.last_error_at = datetime.now(UTC)
        self.last_error_reason = reason
        self.consecutive_failures += 1
        # A failed half-open probe re-opens for a full cooldown rather than
        # leaving the circuit half-open and retrying on every request.
        if self.opened_at is not None or self.consecutive_failures >= self.failure_threshold:
            self.opened_at = self.clock()
            self.probe_taken = False

    def reset(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None
        self.probe_taken = False
        self.last_success_at = None
        self.last_error_at = None
        self.last_error_reason = None


#: Shared across requests: a breaker that is rebuilt per request has no memory
#: and is therefore not a breaker. Tests call :func:`reset_breaker`.
_BREAKER: Final[CircuitBreaker] = CircuitBreaker()


def breaker() -> CircuitBreaker:
    return _BREAKER


def reset_breaker() -> None:
    _BREAKER.reset()


def _to_float(raw: object) -> float | None:
    """Prometheus sends sample values as strings. ``NaN``/``Inf`` become None."""
    try:
        number = float(str(raw))
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _to_time(raw: object) -> datetime:
    return datetime.fromtimestamp(float(str(raw)), tz=UTC)


@dataclass(frozen=True, slots=True)
class PrometheusClient:
    """Read-only access to one Prometheus.

    ``transport`` exists for tests: an ``httpx.MockTransport`` exercises the
    timeout, error-class and circuit-breaker paths without a socket, which is
    the only way to assert the hang case deterministically.
    """

    settings: Settings = field(default_factory=get_settings)
    transport: httpx.AsyncBaseTransport | None = None
    circuit: CircuitBreaker = field(default_factory=breaker)

    @property
    def base_url(self) -> str:
        url = self.settings.prometheus_url
        if url is None:
            raise PrometheusUnavailableError("not_configured", "DCP_PROMETHEUS_URL is not set")
        return url

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.prometheus_timeout_seconds),
            # docs/SECURITY.md §19.5. A redirect from a monitoring server is a
            # misconfiguration or an attempt to move the request elsewhere.
            follow_redirects=False,
            transport=self.transport,
        )

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """One Prometheus read, with the breaker, the deadline and the retry."""
        if not self.circuit.allow():
            raise PrometheusUnavailableError("circuit_open", "the Prometheus circuit is open")
        url = f"{self.base_url}{path}"
        # Prometheus's own deadline, so a query that is expensive server-side
        # is abandoned there too rather than only here.
        params = {**params, "timeout": f"{self.settings.prometheus_timeout_seconds:g}s"}
        try:
            payload = await self._request(url, params)
        except PrometheusUnavailableError as error:
            self.circuit.record_failure(error.reason)
            raise
        self.circuit.record_success()
        return payload

    async def _request(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        """One attempt, then one retry if the connection never opened.

        Written as two statements rather than a loop: a loop needs an
        unreachable `raise` after it to satisfy the return type, and an
        unreachable line in the failure path of a provider client is a line
        nobody can ever test.
        """
        try:
            return await self._attempt(url, params)
        except PrometheusUnavailableError as error:
            if error.reason != "provider_unreachable":
                raise
            logger.info("prometheus_retrying", reason=error.reason)
        # The retry. See the module docstring for why only this reason, and
        # why a read timeout is deliberately not one of them.
        return await self._attempt(url, params)

    async def _attempt(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.get(
                    url, params=params, headers={"Accept": "application/json"}
                )
        except (httpx.ConnectTimeout, httpx.ConnectError) as error:
            logger.warning("prometheus_unreachable", error=type(error).__name__)
            raise PrometheusUnavailableError("provider_unreachable") from error
        except httpx.TimeoutException as error:
            logger.warning("prometheus_timed_out", error=type(error).__name__)
            raise PrometheusUnavailableError("provider_timeout") from error
        except httpx.HTTPError as error:
            # The URL is configuration, but it carries an internal hostname;
            # logged, never returned (docs/SECURITY.md §20).
            logger.warning("prometheus_transport_error", error=type(error).__name__)
            raise PrometheusUnavailableError("provider_unreachable") from error
        return self._parse(response)

    def _parse(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 500:
            logger.warning("prometheus_server_error", status=response.status_code)
            raise PrometheusUnavailableError("provider_error")
        if response.status_code >= 400:
            # A 4xx means the query we built is wrong, which is our bug and
            # not an outage. Logged loudly; reported as provider_error
            # because the caller's honest answer is still "no data".
            logger.error("prometheus_rejected_query", status=response.status_code)
            raise PrometheusUnavailableError("provider_error")
        if len(response.content) > MAX_RESPONSE_BYTES:
            logger.warning("prometheus_response_too_large", size=len(response.content))
            raise PrometheusUnavailableError("provider_error")
        try:
            body: Any = response.json()
        except ValueError as error:
            logger.warning("prometheus_response_not_json")
            raise PrometheusUnavailableError("provider_error") from error
        if not isinstance(body, dict) or body.get("status") != "success":
            # Provider output is untrusted data (mandatory rule 10): the
            # error *type* is logged, the free-text message is not, and
            # neither reaches the client.
            logger.warning(
                "prometheus_query_failed",
                error_type=str(body.get("errorType")) if isinstance(body, dict) else None,
            )
            raise PrometheusUnavailableError("provider_error")
        data = body.get("data")
        if not isinstance(data, dict):
            logger.warning("prometheus_response_malformed")
            raise PrometheusUnavailableError("provider_error")
        return data

    async def query(self, promql: str, *, at: datetime | None = None) -> tuple[Sample, ...]:
        """Instant query. ``promql`` comes from the template catalogue only."""
        params = {"query": promql}
        if at is not None:
            params["time"] = f"{at.timestamp():.3f}"
        data = await self._get(_QUERY_PATH, params)
        result = data.get("result")
        if not isinstance(result, list):
            return ()
        samples: list[Sample] = []
        for entry in result:
            if not isinstance(entry, dict):
                continue
            value = entry.get("value")
            if not isinstance(value, list) or len(value) != 2:
                continue
            labels = entry.get("metric")
            samples.append(
                Sample(
                    labels={
                        str(key): str(item)
                        for key, item in (labels.items() if isinstance(labels, dict) else ())
                    },
                    timestamp=_to_time(value[0]),
                    value=_to_float(value[1]),
                )
            )
        return tuple(samples)

    async def query_range(
        self,
        promql: str,
        *,
        start: datetime,
        end: datetime,
        step_seconds: int,
    ) -> tuple[Series, ...]:
        """Range query. Bounds are the caller's to check (``metrics.service``)."""
        data = await self._get(
            _RANGE_PATH,
            {
                "query": promql,
                "start": f"{start.timestamp():.3f}",
                "end": f"{end.timestamp():.3f}",
                "step": f"{step_seconds}s",
            },
        )
        result = data.get("result")
        if not isinstance(result, list):
            return ()
        series: list[Series] = []
        for entry in result:
            if not isinstance(entry, dict):
                continue
            values = entry.get("values")
            if not isinstance(values, list):
                continue
            labels = entry.get("metric")
            series.append(
                Series(
                    labels={
                        str(key): str(item)
                        for key, item in (labels.items() if isinstance(labels, dict) else ())
                    },
                    points=tuple(
                        (_to_time(point[0]), _to_float(point[1]))
                        for point in values
                        if isinstance(point, list) and len(point) == 2
                    ),
                )
            )
        return tuple(series)

    async def probe(self) -> datetime:
        """Cheapest possible read, for the §26.1 integration status.

        ``vector(1)`` touches no series, so this answers "can we reach and
        parse this Prometheus" without depending on anything being scraped —
        which is the question the integration page asks (PRD §4.11).
        """
        samples = await self.query("vector(1)")
        if not samples:
            raise PrometheusUnavailableError("provider_error", "probe returned no sample")
        return samples[0].timestamp
