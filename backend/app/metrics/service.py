"""Query-bound guards, the catalogue response, and the Prometheus status row.

The guards are the second half of AD-04. Refusing client PromQL stops someone
*writing* an expensive query; these stop someone *asking for* one with the
parameters a legitimate endpoint accepts — a 90-day range at a 15-second step
over 20 servers is 10 million points from a template nobody tampered with.

Every bound is from docs/API_CONTRACTS.md §15.2 and §15.3 rather than invented
here, and they are checked **before** a query is rendered, so an over-large
request costs one comparison instead of a Prometheus round trip.

M2.1 ships no endpoint that takes a range — §15.1–§15.3 are M2.2 — so these
are exercised by their own tests rather than by a route. They are written now
because the milestone that adds the endpoints should not also be the milestone
that decides the bounds under deadline.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from app.alerts.status import alertmanager_status
from app.core.config import Settings, get_settings
from app.core.errors import AppError, field_error
from app.logs.status import loki_status
from app.metrics import templates
from app.metrics.client import PrometheusClient, PrometheusUnavailableError, breaker
from app.metrics.schemas import (
    IntegrationStatus,
    IntegrationStatusList,
    MetricTemplateInfo,
    MetricTemplateList,
)

#: §15.2: "max range 30 days".
MAX_RANGE_SECONDS: Final = 30 * 24 * 3600
#: §15.2: "(to - from) / step ≤ 11 000". Prometheus's own default
#: ``--query.max-samples`` guard is per-query and server-side; this one is
#: ours, so the refusal is a structured 422 rather than a provider error.
MAX_POINTS: Final = 11_000
#: §15.2: "step_seconds ≥ 15".
MIN_STEP_SECONDS: Final = 15
#: §15.2: "1–5 templates".
MAX_TEMPLATES: Final = 5
#: §15.3: "at most 20 servers or 4 environments per request".
MAX_SERVERS: Final = 20
MAX_ENVIRONMENTS: Final = 4


class QueryLimitExceededError(AppError):
    """``422 query_limit_exceeded``, carrying the bound that was crossed.

    One error code for every bound, with ``details`` naming the maximum, so a
    client can tell the user what to change. §15.2 asks for "the allowed
    maximum in ``details``" and that is the only thing worth returning: the
    requested value is already known to the caller.
    """

    status_code = 422
    code = "query_limit_exceeded"
    message = "The requested query exceeds a configured limit."

    def __init__(self, limit: str, maximum: float, requested: float) -> None:
        super().__init__(
            f"The requested query exceeds the {limit} limit.",
            details={"limit": limit, "maximum": maximum, "requested": requested},
        )


def check_selection(
    *,
    template_count: int,
    server_count: int = 1,
    environment_count: int = 0,
) -> None:
    """Fan-out bounds (§15.2 templates, §15.3 servers and environments).

    ``server_count`` of zero is allowed and is not a limit failure: §15.3
    drops out-of-scope servers silently and reports ``excluded_count``, so a
    request that scopes down to nothing is an empty answer, not an error.
    """
    if template_count < 1:
        raise field_error(["query", "metrics"], "required", "At least one template is required.")
    if template_count > MAX_TEMPLATES:
        raise QueryLimitExceededError("templates", MAX_TEMPLATES, template_count)
    if server_count > MAX_SERVERS:
        raise QueryLimitExceededError("servers", MAX_SERVERS, server_count)
    if environment_count > MAX_ENVIRONMENTS:
        raise QueryLimitExceededError("environments", MAX_ENVIRONMENTS, environment_count)


def check_time_range(*, start: datetime, end: datetime, step_seconds: int) -> int:
    """Validate a range request and return the point count it will produce.

    Returning the count rather than ``None`` is deliberate: the caller needs
    it to size its own response, and recomputing it there is how the guard and
    the response end up disagreeing.
    """
    if start >= end:
        # Not a limit: ordering is a field relationship, and §8's per-field
        # shape is the useful answer for it.
        raise field_error(["query", "from"], "min_value", "'from' must be earlier than 'to'.")
    if step_seconds < MIN_STEP_SECONDS:
        # A limit rather than a bound on the field, because the thing being
        # protected is query cost: a smaller step is strictly more points.
        raise QueryLimitExceededError("step_seconds", MIN_STEP_SECONDS, step_seconds)
    span = (end - start).total_seconds()
    if span > MAX_RANGE_SECONDS:
        raise QueryLimitExceededError("range_seconds", MAX_RANGE_SECONDS, span)
    # Integer arithmetic on the way up: Prometheus returns a point at `start`
    # as well, so a span that divides exactly yields one more point than the
    # division suggests, and a floor here would admit a query 1 point over.
    points = int(span // step_seconds) + 1
    if points > MAX_POINTS:
        raise QueryLimitExceededError("points", MAX_POINTS, points)
    return points


def catalogue(settings: Settings | None = None) -> MetricTemplateList:
    """§15.4. Built from the one catalogue, so the API cannot drift from it."""
    settings = settings or get_settings()
    return MetricTemplateList(
        templates=[
            MetricTemplateInfo(
                template=template.id,
                unit=template.unit,
                description=template.description,
                # ``type: ignore`` would be the alternative: the catalogue's
                # families are plain strings and the schema's are the
                # OperatingSystem literal. ``test_templates`` asserts the two
                # agree, which is the check that actually matters.
                applies_to_os=list(template.applies_to_os),  # type: ignore[arg-type]
                default_thresholds=(
                    dict(template.default_thresholds)
                    if template.default_thresholds is not None
                    else None
                ),
            )
            for template in templates.CATALOGUE
        ],
        demo_mode=settings.demo_mode,
    )


#: Integrations §26.1 names that have no configuration surface yet. Listed
#: rather than omitted: a status page that shows only the integrations which
#: exist reads as "this is everything", and an operator cannot tell a missing
#: integration from a healthy one. Each says which milestone brings it, the
#: same convention ``app/dashboard/schemas.py`` uses for a sourceless section.
_PENDING: Final[tuple[tuple[str, str, str], ...]] = (
    # §26.1 asks for a row per *cloud account*, and migration 0002 seeds one, so
    # this is a deliberate single row rather than an enumeration. There is no
    # provider adapter to probe until M4.1, so N rows would carry one fact N
    # times — and reading the accounts would mean a database session on a
    # status endpoint that otherwise needs none. It becomes per-account when
    # there is a per-account answer. Recorded in PROGRESS.md.
    ("Cloud accounts", "cloud", "No cloud provider adapter exists yet (M4.1)."),
    # Replaced by a live probe (M3.3) at this position, so the page keeps the contract's
    # order; it stays in the list so a name can never be dropped from the page by accident.
    ("Alertmanager", "alerts", "Alertmanager is probed live (M3.3)."),
    ("Jenkins", "ci", "Deployment integration is not implemented yet (M5.1)."),
    ("Secret store", "secrets", "No secret store backend is selected yet (OPEN-06)."),
    ("Notification channels", "notifications", "Notifications are not implemented yet (M3.4)."),
)


async def prometheus_status(client: PrometheusClient | None = None) -> IntegrationStatus:
    """§26.1's Prometheus row, from a live probe.

    Never raises. The point of this endpoint is to answer "is the portal
    broken or is the thing it monitors broken" (PRD §4.11), and an endpoint
    that fails when a dependency fails cannot answer that.
    """
    settings = get_settings()
    circuit = breaker()
    if not settings.prometheus_configured:
        return IntegrationStatus(
            name="Prometheus",
            type="metrics",
            status="not_configured",
            last_error_message="DCP_PROMETHEUS_URL is not set.",
            demo_mode=settings.demo_mode,
        )
    client = client or PrometheusClient(settings=settings)
    reason: str | None = None
    try:
        await client.probe()
    except PrometheusUnavailableError as error:
        # The reason code, not the exception text: the text can name an
        # internal host (docs/SECURITY.md §20).
        reason = error.reason
    return IntegrationStatus(
        name="Prometheus",
        type="metrics",
        # Three states, not four. ``degraded`` would be the useful fourth —
        # "answering now but not reliably" — and it is deliberately **not**
        # emitted, because this milestone keeps nothing that could decide it
        # honestly: a successful probe resets the breaker's consecutive-failure
        # count, so any flap test written against that count is always false.
        # Saying it properly needs failure-rate history over a window, which is
        # a threshold nobody has asked for yet; recorded in PROGRESS.md rather
        # than guessed at. What the row *does* carry is both timestamps, so an
        # operator looking at an ``ok`` Prometheus can still see that it failed
        # a minute ago and draw their own conclusion.
        status="ok" if reason is None else "unavailable",
        last_success_at=circuit.last_success_at,
        last_error_at=circuit.last_error_at,
        last_error_message=reason or circuit.last_error_reason,
        circuit_state=circuit.state(),
        demo_mode=settings.demo_mode,
    )


async def integration_status() -> IntegrationStatusList:
    """§26.1. Prometheus, Loki and Alertmanager are probed; the rest report what is true of them."""
    settings = get_settings()
    rows = [
        IntegrationStatus(
            name="Identity provider",
            type="identity",
            # Configured is as far as this can honestly go without initiating
            # a login: a discovery fetch here would be a request to the IdP on
            # every poll of a status page, and OPEN-01 means the configured
            # provider is still the mock (CLAUDE.md §7).
            status="ok" if settings.oidc_configured else "not_configured",
            last_error_message=(
                None if settings.oidc_configured else "No identity provider is configured."
            ),
            demo_mode=settings.demo_mode,
        ),
        await prometheus_status(),
        await loki_status(),
    ]
    for name, kind, reason in _PENDING:
        if name == "Alertmanager":
            rows.append(await alertmanager_status())
            continue
        rows.append(
            IntegrationStatus(
                name=name,
                type=kind,
                status="not_configured",
                last_error_message=reason,
                demo_mode=settings.demo_mode,
            )
        )
    return IntegrationStatusList(integrations=rows)
