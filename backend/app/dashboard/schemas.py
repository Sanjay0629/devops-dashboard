"""Dashboard response models (docs/API_CONTRACTS.md §13).

**One source per section, one ``data_state`` per section.** That rule decides
the shape of this module, and it is the reason two blocks here are not laid
out exactly as §13.1's example payload draws them.

At M1.6 the product reads PostgreSQL and nothing else. Two sections therefore
have a real source and the rest have none:

* ``servers`` — counts of ``servers.status``, a real column. ``ok``.
* ``failed_services`` — a count over ``services``, by the state each row
  *shows* (M2.4), with the number of units that could not be read beside it.
  A zero over an empty registry is a real zero; a zero over unread units is
  not, and ``unobserved`` is what says which.
* everything else — ``not_configured`` with ``null`` values and a reason
  naming the milestone that will fill it.

§13.1's example draws ``healthy``, ``warning`` and ``critical`` inside the
``servers`` block beside ``total`` and ``managed``. They cannot live there
honestly: the first three are a *health* rollup with no source until M2.1,
the rest are counts of a stored column, and one envelope cannot carry one
``data_state`` for both. They are a sibling section, :class:`ServerHealthRollup`,
with the same field names. Recorded in docs/API_CONTRACTS.md §13.1.

For the same reason ``recent_health_changes`` (and, until M5.2, ``recent_deployments``, and until
M4.6 ``recent_operations``) were **sections carrying a null list**, not the bare
``[]`` the example shows. There is no deployments table, no operations table
and no health-event table; an empty array would say "nothing happened
recently", which is a claim this milestone cannot make. PRD §4.2's acceptance
criterion — "must not silently replace unavailable data with zero values" —
covers an empty list for the same reason it covers a zero.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.inventory.schemas import CursorPage, DataSection
from app.monitoring.health import HealthState
from app.operations.schemas import ActorView

#: Which milestone each absent source is waiting for. Named here rather than
#: written at each call site so the dashboard cannot tell an operator two
#: different stories about the same missing integration.
UTILISATION_REASON = (
    "Fleet-wide utilisation is not aggregated yet; per-server metrics are on each server's page."
)
#: A caller with no ``alert:read`` is told so, not shown a zero.
ALERTS_NOT_PERMITTED = "alert_read_not_permitted"
#: A deployment has an environment and an application and no server, so the four server-only
#: dashboard filters cannot be applied to it (``OPEN-140``).
DEPLOYMENTS_FILTER_REASON = (
    "Deployments have no provider, region, operating system or owner team, so a dashboard "
    "filtered by one of those cannot list them."
)


class ServerCounts(DataSection):
    """Counts of ``servers.status`` over the caller's readable servers.

    Soft-deleted rows are excluded, as everywhere else in inventory, so
    ``total`` is the number of registered servers the caller can see.

    ``stale`` and ``unreachable`` are the stored statuses, which AD-14's sweep
    will recompute from ``last_seen_at`` in M2.5. Until then nothing moves a
    server into them automatically, so they report what registration and
    administration have set — an accurate count of a real column, not an
    observation of the host.
    """

    total: int
    pending: int
    managed: int
    stale: int
    unreachable: int
    retired: int


class ServerHealthRollup(DataSection):
    """The health rollup §13.1 draws inside ``servers`` (M2.5).

    Counts **every readable server** — the population of ``servers.total`` — so
    the five counts sum to it; a retired or unmonitored server is ``unknown``,
    not omitted. They are the same states the ``health_status`` filter on §13.2
    selects, from the same rule, so a card's number is the length of the list it
    links to.

    ``ok`` only while Prometheus has answered recently; otherwise ``stale``,
    ``unavailable`` or ``not_configured`` and **every count is ``null``**, never
    ``0`` (§4 rule 2, PRD §4.2). ``collected_at`` is when Prometheus last
    answered, i.e. the age of the evidence.

    ``critical`` is always ``null``: it would come from utilisation (``OPEN-28``),
    which is not evaluated, and ``null`` says so where ``0`` would say "none".
    """

    healthy: int | None = None
    warning: int | None = None
    critical: int | None = None
    stale: int | None = None
    unreachable: int | None = None
    unknown: int | None = None


class UtilisationSection(DataSection):
    cpu_percent_avg: float | None = None
    memory_percent_avg: float | None = None
    disk_percent_max: float | None = None
    network_bytes_per_second: float | None = None


class FailedServicesSection(DataSection):
    """Units that *show* ``FAILED``, and how many could not be read at all.

    ``count`` is a real count over the registry (``services`` is the allowlist),
    but it is a count of what is **known**: a unit whose host is silent, or whose
    exporter reports nothing, shows ``UNKNOWN`` or ``UNCOLLECTED`` and is not in
    it. ``unobserved`` is how many such units there are, so ``0`` failed beside
    ``12`` unobserved cannot be mistaken for ``0`` failed of ``12``.
    """

    count: int | None = None
    unobserved: int | None = None


class ActiveAlertsSection(DataSection):
    """Open alerts by severity, from PostgreSQL (M3.3), scoped to what the caller may read.

    ``pipeline`` says whether the alerting pipeline has been *confirmed* by the always-firing
    watchdog (``app/alerts/status.py``): counts of ``0`` beside ``not_confirmed`` mean "cannot
    tell", never "all clear" (PRD §3). ``null`` when no Alertmanager is configured.
    """

    critical: int | None = None
    warning: int | None = None
    info: int | None = None
    pipeline: Literal["confirmed", "not_confirmed", "unknown"] | None = None


class RecentOperation(BaseModel):
    """One operation on the dashboard (§13.1). The detail, the reason and the result are §17.2's."""

    model_config = ConfigDict(extra="forbid")

    operation_id: uuid.UUID
    action_type: str
    server_id: uuid.UUID
    server_display_name: str
    service_name: str
    #: The environment stored on the operation when it was requested.
    environment: str
    execution_status: str
    requested_by: ActorView
    created_at: datetime


class RecentOperationsSection(DataSection):
    """The caller's most recent operations (M4.6, ``OPEN-116``): a real list, so ``[]`` is true.

    ``[]`` means "nothing happened that you may see" and is distinct from ``not_configured`` with
    ``null``. The rows are the ones ``operation:read`` reaches -- **not** "and the caller's own",
    because this section rides a cache keyed by scope and not by person.
    """

    items: list[RecentOperation] | None = None


class RecentDeployment(BaseModel):
    """One deployment on the dashboard (§13.1). The reason, the result and the build are §21.3's."""

    model_config = ConfigDict(extra="forbid")

    deployment_id: uuid.UUID
    application_id: uuid.UUID
    application_name: str
    #: The environment stored on the deployment when it was requested.
    environment: str
    requested_ref: str
    execution_status: str
    requested_by: ActorView
    created_at: datetime


class RecentDeploymentsSection(DataSection):
    """The caller's most recent deployments (M5.2, ``OPEN-140``): a real list, so ``[]`` is true.

    ``[]`` means "nothing happened that you may see" and is distinct from ``not_configured`` with
    ``null``. The rows are the ones ``deployment:read`` reaches -- **not** "and the caller's own",
    because this section rides a cache keyed by scope and not by person. With a server-only filter
    applied the section is ``unavailable`` with a reason, never ``[]``: an empty list would say
    nothing happened, when the truth is that the question cannot be asked.
    """

    items: list[RecentDeployment] | None = None


class HealthChange(BaseModel):
    """One status move made by the sweep (``SERVER_STATUS_CHANGED``)."""

    model_config = ConfigDict(extra="forbid")

    server_id: uuid.UUID
    display_name: str
    environment: str
    previous_status: str
    new_status: str
    health_state: HealthState | None
    occurred_at: datetime


class RecentHealthChangesSection(DataSection):
    """The latest sweep moves, newest first, over the caller's readable servers.

    A real list from a real table (``audit_events``), so ``[]`` is a true "no
    recorded change" and is distinct from ``null`` ("not available"). Only the
    sweep's moves appear: an administrator's edit is ``SERVER_UPDATED``.
    """

    items: list[HealthChange] | None = None


class DashboardFilters(BaseModel):
    """The filters actually applied, echoed back (§13.1 ``filters_applied``)."""

    model_config = ConfigDict(extra="forbid")

    environment: list[str] | None = None
    provider: list[str] | None = None
    region: list[str] | None = None
    application_id: list[uuid.UUID] | None = None
    operating_system: list[str] | None = None
    owner_team: list[str] | None = None


class DashboardSummary(BaseModel):
    """§13.1."""

    model_config = ConfigDict(extra="forbid")

    servers: ServerCounts
    server_health: ServerHealthRollup
    utilisation: UtilisationSection
    failed_services: FailedServicesSection
    active_alerts: ActiveAlertsSection
    recent_deployments: RecentDeploymentsSection
    recent_operations: RecentOperationsSection
    recent_health_changes: RecentHealthChangesSection
    filters_applied: DashboardFilters
    demo_mode: bool
    #: How long this payload may be served from cache. Surfaced so the client
    #: does not have to guess how often polling is worth anything.
    cache_ttl_seconds: int


class ServerHealthRow(BaseModel):
    """One row of §13.2's status table."""

    model_config = ConfigDict(extra="forbid")

    server_id: uuid.UUID
    display_name: str
    hostname: str
    environment: str
    provider: str
    status: str
    #: ``null`` when nothing could be evaluated (Prometheus unconfigured, silent
    #: or stale) and never defaulted to ``healthy``; the list's ``health``
    #: section says why, once, rather than on every row. ``unknown`` means it
    #: *was* evaluated and nothing is known about this host.
    health_state: HealthState | None
    last_seen_at: datetime | None
    #: Past the ``stale`` line. ``null`` whenever ``health_state`` is ``null`` or
    #: ``unknown`` — never seen is not staleness.
    stale: bool | None
    #: Real: a count of rows in ``services``.
    failed_service_count: int
    #: ``null`` until there is an alerts table (M3.3).
    active_alert_count: int | None


class DashboardHealthList(BaseModel):
    """§13.2. Cursor-paged, scope-filtered, with the honest sections beside it."""

    model_config = ConfigDict(extra="forbid")

    items: list[ServerHealthRow]
    page: CursorPage
    #: The rollup, and why every row's ``health_state`` is ``null`` when it is.
    health: ServerHealthRollup
    #: The same section as §13.1's: why a row's ``active_alert_count`` may be ``null``.
    alerts: ActiveAlertsSection
    collected_at: datetime
    filters_applied: DashboardFilters
    demo_mode: bool
    cache_ttl_seconds: int


HealthStatusFilter = Literal["healthy", "warning", "critical", "stale", "unreachable", "unknown"]
