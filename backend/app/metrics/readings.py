"""Metric values: current (§15.1), history (§15.2) and comparison (§15.3).

Where ``templates`` owns the queries and ``client`` owns the transport, this
module decides **what a result means** — and the rule it enforces everywhere is
the one PRD §4.4 and CLAUDE.md §5 give: a reading is a value or a reason, never
a substitute. Every path that has no number says why, in one of the closed
``UnavailableReason`` codes, and none of them returns ``0``.

Four distinct "no number" states are kept distinct, because they send an
operator to four different places:

* ``not_configured`` — nothing is set up to collect this (monitoring disabled
  on the server, no ``metrics_instance``, no Prometheus URL).
* ``unsupported`` — the template has no query for this OS family (``OPEN-09``).
* ``unavailable`` — Prometheus failed, or answered with nothing for this host.
* ``stale`` — a value exists but is too old to call current. Carries no value.

``PrometheusUnavailableError`` is caught here and nowhere else on the way up:
it is not an ``AppError`` on purpose (see ``client``), so a dead Prometheus
becomes a section of a successful response instead of a 5xx.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.dependencies import Authorized
from app.authz.queries import readable_servers
from app.core.config import get_settings
from app.core.errors import field_error
from app.inventory.models import Server
from app.inventory.schemas import DataState
from app.inventory.service import NotFoundError, server_target
from app.metrics import service, templates, thresholds
from app.metrics.client import PrometheusClient, PrometheusUnavailableError, Sample, Series
from app.metrics.schemas import (
    CompareRequest,
    ComparisonSeries,
    CurrentMetrics,
    MetricComparison,
    MetricReading,
    MetricSeries,
    Point,
    RangeMetrics,
    UnavailableReason,
)
from app.metrics.templates import MetricTemplate


def make_client() -> PrometheusClient:
    """The one place a client is built per request.

    A function rather than a default argument so a test can substitute a
    transport without reaching into the app's dependency graph.
    """
    return PrometheusClient()


def template_ids_or_error(requested: Sequence[str] | None) -> list[MetricTemplate]:
    """Resolve template ids to catalogue entries, in order, without duplicates.

    ``None`` is "all", which is §15.1's default. An unknown id is a ``422``
    naming the field — the catalogue is closed, so there is nothing to fall
    back to, and silently dropping it would answer a different question.
    """
    if requested is None:
        return list(templates.CATALOGUE)
    chosen: list[MetricTemplate] = []
    for template_id in dict.fromkeys(requested):
        template = templates.BY_ID.get(template_id)
        if template is None:
            raise field_error(["query", "metrics"], "unknown_template", "Unknown metric template.")
        chosen.append(template)
    return chosen


async def metrics_server(db: AsyncSession, server_id: uuid.UUID, authorized: Authorized) -> Server:
    """Load a server the caller may read **metrics** for, or raise ``404``.

    ``metrics:read`` is the permission and so ``metrics:read`` is the scope
    asked about: a caller scoped to staging servers for inventory but not for
    metrics must not read a number off a host they cannot read metrics for.
    Out of scope reads as absent, as everywhere else (§2).
    """
    server = await db.get(Server, server_id)
    if (
        server is None
        or server.deleted_at is not None
        or not authorized.permits("metrics", "read", server_target(server))
    ):
        raise NotFoundError
    return server


def _failure(error: PrometheusUnavailableError) -> tuple[DataState, UnavailableReason]:
    if error.reason == "not_configured":
        return "not_configured", "prometheus_not_configured"
    return "unavailable", error.reason


_NO_INSTANCE: tuple[DataState, UnavailableReason] = ("not_configured", "no_metrics_instance")


def _gate(server: Server) -> tuple[DataState, UnavailableReason] | None:
    """Reasons a server cannot be queried at all, decided without a query."""
    if not server.monitoring_enabled:
        return "not_configured", "monitoring_disabled"
    if server.metrics_instance is None:
        return _NO_INSTANCE
    return None


def _thresholds(chosen: Sequence[MetricTemplate]) -> dict[str, dict[str, float]]:
    return {t.id: dict(t.default_thresholds) for t in chosen if t.default_thresholds is not None}


def _reading(
    template: MetricTemplate, state: DataState, reason: UnavailableReason | None
) -> MetricReading:
    return MetricReading(
        template=template.id,
        unit=template.unit,
        value=None,
        data_state=state,
        unavailable_reason=reason,
    )


def _unwrap(result: Any) -> Any:
    """A gathered result: return it, or re-raise anything that is not an outage.

    ``return_exceptions=True`` is there so one failed query does not discard
    its siblings; it must not also swallow a genuine bug.
    """
    if isinstance(result, BaseException) and not isinstance(result, PrometheusUnavailableError):
        raise result
    return result


# --- §15.1 ---------------------------------------------------------------------


async def current(
    server: Server,
    chosen: Sequence[MetricTemplate],
    *,
    client: PrometheusClient | None = None,
) -> CurrentMetrics:
    """Current values for one server."""
    demo = get_settings().demo_mode
    gate = _gate(server)
    instance = server.metrics_instance
    if gate is not None or instance is None:
        readings = [_reading(t, *(gate or _NO_INSTANCE)) for t in chosen]
    else:
        readings = await _read_instant(server, instance, chosen, client or make_client())
    return CurrentMetrics(
        server_id=server.id,
        metrics=readings,
        thresholds=_thresholds(chosen),
        demo_mode=demo,
    )


async def _read_instant(
    server: Server, instance: str, chosen: Sequence[MetricTemplate], client: PrometheusClient
) -> list[MetricReading]:
    instances = [instance]
    family = server.operating_system

    queries: dict[str, str] = {}
    unsupported: set[str] = set()
    for template in chosen:
        try:
            queries[template.id] = templates.render(template.id, family, instances)
        except templates.UnsupportedOsFamilyError:
            unsupported.add(template.id)
    scrape_query: str | None = None
    if queries:
        try:
            scrape_query = templates.render_scrape_timestamp(family, instances)
        except templates.UnsupportedOsFamilyError:
            # A family with templates but no `agent_up` cannot be dated, and an
            # undated number is not presented as current.
            unsupported.update(queries)
            queries.clear()

    results: dict[str, Any] = {}
    scrape: Any = ()
    if scrape_query is not None:
        gathered = await asyncio.gather(
            client.query(scrape_query),
            *(client.query(q) for q in queries.values()),
            return_exceptions=True,
        )
        scrape = _unwrap(gathered[0])
        results = {tid: _unwrap(r) for tid, r in zip(queries, gathered[1:], strict=True)}

    readings: list[MetricReading] = []
    for template in chosen:
        if template.id in unsupported:
            readings.append(_reading(template, "unsupported", "unsupported_os"))
            continue
        readings.append(_instant_reading(template, scrape, results[template.id]))
    return readings


def _single(samples: Sequence[Sample]) -> Sample | None:
    return samples[0] if len(samples) == 1 else None


def _instant_reading(template: MetricTemplate, scrape: Any, result: Any) -> MetricReading:
    for outcome in (scrape, result):
        if isinstance(outcome, PrometheusUnavailableError):
            return _reading(template, *_failure(outcome))
    # More than one sample for an equality selector means the label is not
    # the key we think it is; fail closed rather than pick one.
    if len(scrape) > 1 or len(result) > 1:
        return _reading(template, "unavailable", "provider_error")
    scraped, value = _single(scrape), _single(result)
    if scraped is None or scraped.value is None or value is None or value.value is None:
        return _reading(template, "unavailable", "no_data")
    collected_at = datetime.fromtimestamp(scraped.value, tz=UTC)
    # Both timestamps are Prometheus's own, so the age cannot be skewed by
    # this host's clock.
    age = (scraped.timestamp - collected_at).total_seconds()
    if thresholds.is_stale(age):
        return MetricReading(
            template=template.id,
            unit=template.unit,
            value=None,
            data_state="stale",
            collected_at=collected_at,
            stale_after_seconds=thresholds.STALE_AFTER_SECONDS,
        )
    return MetricReading(
        template=template.id,
        unit=template.unit,
        value=value.value,
        data_state="ok",
        collected_at=collected_at,
        threshold_state=thresholds.classify(template.default_thresholds, value.value),
    )


# --- §15.2 / §15.3 shared ------------------------------------------------------


def _utc(moment: datetime, loc: list[str]) -> datetime:
    """Reject a naive timestamp: its meaning would depend on the server's zone."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise field_error(loc, "timezone_required", "A timezone offset is required.")
    return moment.astimezone(UTC)


def _align(series: Series, start: datetime, step: int, count: int) -> list[Point] | None:
    """Lay a series on the requested grid, leaving every hole a ``None``.

    Prometheus omits a timestamp it has no sample for. Putting the grid back
    is what makes a gap *visible* to the chart; the point is not filled,
    interpolated or zeroed. An all-``None`` result is "no data", not a flat
    line of nothing, so it is reported as ``None``.
    """
    values: dict[int, float | None] = {}
    for moment, value in series.points:
        index = round((moment - start).total_seconds() / step)
        if 0 <= index < count:
            values[index] = value
    if all(v is None for v in values.values()):
        return None
    return [(start + timedelta(seconds=k * step), values.get(k)) for k in range(count)]


def _window(step_seconds: int) -> int:
    """``rate()`` window for a range: never narrower than the step.

    A step wider than the window samples disjoint windows and under-reports
    the load in between.
    """
    return max(templates.DEFAULT_WINDOW_SECONDS, step_seconds)


# --- §15.2 ---------------------------------------------------------------------


async def history(
    server: Server,
    chosen: Sequence[MetricTemplate],
    *,
    start: datetime,
    end: datetime,
    step_seconds: int,
    client: PrometheusClient | None = None,
) -> RangeMetrics:
    """Time series for one server."""
    start, end = _utc(start, ["query", "from"]), _utc(end, ["query", "to"])
    service.check_selection(template_count=len(chosen))
    count = service.check_time_range(start=start, end=end, step_seconds=step_seconds)

    gate = _gate(server)
    instance = server.metrics_instance
    if gate is not None or instance is None:
        gate = gate or _NO_INSTANCE
        series = [
            MetricSeries(template=t.id, unit=t.unit, data_state=gate[0], unavailable_reason=gate[1])
            for t in chosen
        ]
    else:
        series = await _read_range(
            server, instance, chosen, start, step_seconds, count, end, client or make_client()
        )
    return RangeMetrics(
        server_id=server.id,
        start=start,
        end=end,
        step_seconds=step_seconds,
        series=series,
        thresholds=_thresholds(chosen),
        demo_mode=get_settings().demo_mode,
    )


async def _read_range(
    server: Server,
    instance: str,
    chosen: Sequence[MetricTemplate],
    start: datetime,
    step: int,
    count: int,
    end: datetime,
    client: PrometheusClient,
) -> list[MetricSeries]:
    queries: dict[str, str] = {}
    out: dict[str, MetricSeries] = {}
    for template in chosen:
        try:
            queries[template.id] = templates.render(
                template.id,
                server.operating_system,
                [instance],
                window_seconds=_window(step),
            )
        except templates.UnsupportedOsFamilyError:
            out[template.id] = MetricSeries(
                template=template.id,
                unit=template.unit,
                data_state="unsupported",
                unavailable_reason="unsupported_os",
            )
    gathered = await asyncio.gather(
        *(client.query_range(q, start=start, end=end, step_seconds=step) for q in queries.values()),
        return_exceptions=True,
    )
    for template_id, result in zip(queries, gathered, strict=True):
        template = templates.BY_ID[template_id]
        out[template_id] = _range_series(template, _unwrap(result), start, step, count)
    return [out[t.id] for t in chosen]


def _range_series(
    template: MetricTemplate, result: Any, start: datetime, step: int, count: int
) -> MetricSeries:
    def empty(state: DataState, reason: UnavailableReason) -> MetricSeries:
        return MetricSeries(
            template=template.id,
            unit=template.unit,
            data_state=state,
            unavailable_reason=reason,
        )

    if isinstance(result, PrometheusUnavailableError):
        return empty(*_failure(result))
    if len(result) > 1:
        return empty("unavailable", "provider_error")
    points = _align(result[0], start, step, count) if result else None
    if points is None:
        return empty("unavailable", "no_data")
    return MetricSeries(template=template.id, unit=template.unit, data_state="ok", points=points)


# --- §15.3 ---------------------------------------------------------------------


async def compare(
    db: AsyncSession,
    authorized: Authorized,
    body: CompareRequest,
    *,
    client: PrometheusClient | None = None,
) -> MetricComparison:
    """One metric across several servers, scope-filtered inside the query."""
    template = templates.BY_ID.get(body.metric)
    if template is None:
        raise field_error(["body", "metric"], "unknown_template", "Unknown metric template.")
    start, end = _utc(body.from_, ["body", "from"]), _utc(body.to, ["body", "to"])
    ids = list(dict.fromkeys(body.server_ids)) if body.server_ids is not None else []
    environments = list(dict.fromkeys(body.environments)) if body.environments is not None else []
    service.check_selection(
        template_count=1, server_count=len(ids), environment_count=len(environments)
    )
    count = service.check_time_range(start=start, end=end, step_seconds=body.step_seconds)

    chosen = (
        Server.id.in_(ids) if body.server_ids is not None else Server.environment.in_(environments)
    )
    servers = list(
        (
            await db.scalars(
                select(Server)
                .where(
                    Server.deleted_at.is_(None),
                    readable_servers(authorized.grants, "metrics", "read"),
                    chosen,
                )
                .order_by(Server.display_name, Server.id)
            )
        ).all()
    )
    # An environment selection means "what I may read there", so nothing is
    # excluded from the caller's point of view and reporting a count would
    # disclose how many servers exist outside their scope.
    excluded = len(ids) - len(servers) if body.server_ids is not None else 0
    # After scoping, so the cap counts what will actually be queried.
    service.check_selection(template_count=1, server_count=len(servers))

    series = await _compare_series(template, servers, start, end, body.step_seconds, count, client)
    return MetricComparison(
        metric=template.id,
        unit=template.unit,
        start=start,
        end=end,
        step_seconds=body.step_seconds,
        series=series,
        thresholds=dict(template.default_thresholds) if template.default_thresholds else None,
        excluded_count=excluded,
        demo_mode=get_settings().demo_mode,
    )


async def _compare_series(
    template: MetricTemplate,
    servers: Sequence[Server],
    start: datetime,
    end: datetime,
    step: int,
    count: int,
    client: PrometheusClient | None,
) -> list[ComparisonSeries]:
    results: dict[uuid.UUID, tuple[DataState, UnavailableReason | None, list[Point] | None]] = {}
    queryable: dict[str, list[Server]] = {}
    for server in servers:
        gate = _gate(server)
        if gate is not None:
            results[server.id] = (gate[0], gate[1], None)
        else:
            queryable.setdefault(server.operating_system, []).append(server)

    queries: dict[str, str] = {}
    for family, members in queryable.items():
        try:
            queries[family] = templates.render(
                template.id,
                family,
                [m.metrics_instance for m in members if m.metrics_instance is not None],
                window_seconds=_window(step),
            )
        except templates.UnsupportedOsFamilyError:
            for member in members:
                results[member.id] = ("unsupported", "unsupported_os", None)

    if queries:
        prom = client or make_client()
        gathered = await asyncio.gather(
            *(
                prom.query_range(q, start=start, end=end, step_seconds=step)
                for q in queries.values()
            ),
            return_exceptions=True,
        )
        for family, outcome in zip(queries, gathered, strict=True):
            outcome = _unwrap(outcome)
            for member in queryable[family]:
                results[member.id] = _member_result(outcome, member, start, step, count)

    return [
        ComparisonSeries(
            server_id=s.id,
            display_name=s.display_name,
            environment=s.environment,  # type: ignore[arg-type]
            data_state=results[s.id][0],
            unavailable_reason=results[s.id][1],
            points=results[s.id][2],
        )
        for s in servers
    ]


def _member_result(
    outcome: Any, server: Server, start: datetime, step: int, count: int
) -> tuple[DataState, UnavailableReason | None, list[Point] | None]:
    if isinstance(outcome, PrometheusUnavailableError):
        return (*_failure(outcome), None)
    mine = [s for s in outcome if s.labels.get("instance") == server.metrics_instance]
    if len(mine) > 1:
        return "unavailable", "provider_error", None
    points = _align(mine[0], start, step, count) if mine else None
    if points is None:
        return "unavailable", "no_data", None
    return "ok", None, points
